"""People, roles, the permission matrix, and the audit log (admin screens)."""

import secrets
from flask import Blueprint, jsonify, request
from flask_login import current_user
from werkzeug.security import generate_password_hash

from audit import log_event
from config import ORG_DOMAIN
from helpers import iso_utc
from models import AuditLog, RolePermission, User, UserRole, db
from permissions import (
    DEFAULTS, ROLES, _ADMIN_LOCKED, ADMIN, is_known_permission,
    is_known_role, matrix, permission_table, require,
)

team_bp = Blueprint("team", __name__)


def _person(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "display_name": u.display_name or "",
        "roles": sorted(u.roles), "active": bool(u.active),
        "signed_in_with_google": bool(u.google_sub),
        "last_login_at": iso_utc(u.last_login_at),
    }


def _clean_roles(raw):
    roles = raw if isinstance(raw, list) else []
    cleaned = sorted({r for r in roles if isinstance(r, str)})
    unknown = [r for r in cleaned if not is_known_role(r)]
    return cleaned, unknown


def _other_active_admins(excluding_user_id: int) -> int:
    return (User.query.join(UserRole, UserRole.user_id == User.id)
            .filter(UserRole.role == ADMIN, User.active.is_(True), User.id != excluding_user_id)
            .count())


@team_bp.route("/api/team")
@require("team.manage")
def list_team():
    users = User.query.order_by(User.email).all()
    return jsonify({
        "people": [_person(u) for u in users],
        "roles": [{"key": k, "label": v} for k, v in ROLES.items()],
        "domain": ORG_DOMAIN,
    })


@team_bp.route("/api/team", methods=["POST"])
@require("team.manage")
def add_person():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    roles, unknown = _clean_roles(data.get("roles"))
    if not email.endswith("@" + ORG_DOMAIN):
        return jsonify({"error": f"People must use an @{ORG_DOMAIN} address."}), 400
    if unknown:
        return jsonify({"error": f"Unknown role: {unknown[0]}"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "That person is already on the team."}), 400
    # No usable password: they sign in with Google. (Password mode people reset theirs.)
    user = User(email=email, display_name=(data.get("display_name") or "").strip()[:200] or None,
                password_hash=generate_password_hash(secrets.token_urlsafe(32), method="pbkdf2:sha256"))
    db.session.add(user)
    db.session.flush()
    for r in roles:
        db.session.add(UserRole(user_id=user.id, role=r))
    db.session.commit()
    log_event("team.add", source=email, detail={"roles": roles})
    return jsonify({"ok": True, "person": _person(user)}), 201


@team_bp.route("/api/team/<int:user_id>", methods=["PATCH"])
@require("team.manage")
def update_person(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "Person not found."}), 404
    data = request.get_json(silent=True) or {}
    before = {"roles": sorted(user.roles), "active": user.active}

    if "roles" in data:
        roles, unknown = _clean_roles(data["roles"])
        if unknown:
            return jsonify({"error": f"Unknown role: {unknown[0]}"}), 400
        if ADMIN in user.roles and ADMIN not in roles and _other_active_admins(user.id) == 0:
            return jsonify({"error": "There must be at least one active admin."}), 400
        UserRole.query.filter_by(user_id=user.id).delete()
        for r in roles:
            db.session.add(UserRole(user_id=user.id, role=r))
    if "active" in data:
        active = bool(data["active"])
        if not active and user.id == current_user.id:
            return jsonify({"error": "You cannot deactivate yourself."}), 400
        if not active and ADMIN in user.roles and _other_active_admins(user.id) == 0:
            return jsonify({"error": "There must be at least one active admin."}), 400
        user.active = active
    if "display_name" in data:
        user.display_name = (data["display_name"] or "").strip()[:200] or None
    db.session.commit()
    db.session.refresh(user)
    log_event("team.update", source=user.email,
              detail={"before": before, "after": {"roles": sorted(user.roles), "active": user.active}})
    return jsonify({"ok": True, "person": _person(user)})


@team_bp.route("/api/permissions")
@require("team.manage")
def get_permissions():
    effective = matrix()
    return jsonify({
        "roles": [{"key": k, "label": v} for k, v in ROLES.items()],
        "permissions": permission_table(),
        "matrix": {role: sorted(perms) for role, perms in effective.items()},
        "defaults": {role: sorted(perms) for role, perms in DEFAULTS.items()},
        "locked": {ADMIN: sorted(_ADMIN_LOCKED)},
    })


@team_bp.route("/api/permissions", methods=["POST"])
@require("team.manage")
def set_permission():
    data = request.get_json(silent=True) or {}
    role, permission, allowed = data.get("role"), data.get("permission"), data.get("allowed")
    if not is_known_role(role) or not is_known_permission(permission) or not isinstance(allowed, bool):
        return jsonify({"error": "Unknown role or permission."}), 400
    if role == ADMIN and permission in _ADMIN_LOCKED and not allowed:
        return jsonify({"error": "Admins always keep that permission."}), 400
    default = permission in DEFAULTS[role]
    row = RolePermission.query.filter_by(role=role, permission=permission).first()
    if allowed == default:
        if row:
            db.session.delete(row)            # back to the default: no override needed
    else:
        if not row:
            row = RolePermission(role=role, permission=permission, allowed=allowed)
            db.session.add(row)
        row.allowed = allowed
        row.updated_by = current_user.email
    db.session.commit()
    log_event("permissions.change", source=f"{role}:{permission}", detail={"allowed": allowed})
    return jsonify({"ok": True})


@team_bp.route("/api/audit")
@require("audit.read")
def list_audit():
    query = AuditLog.query
    action = (request.args.get("action") or "").strip()
    if action:
        query = query.filter(AuditLog.action.like(action + "%"))
    email = (request.args.get("email") or "").strip().lower()
    if email:
        query = query.filter(AuditLog.email == email)
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    except ValueError:
        limit = 100
    rows = query.order_by(AuditLog.id.desc()).limit(limit).all()
    return jsonify({"entries": [
        {"id": r.id, "at": iso_utc(r.at), "email": r.email or "", "action": r.action,
         "source": r.source or "", "detail": r.detail or "", "ip": r.ip or ""}
        for r in rows
    ]})
