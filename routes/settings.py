"""Church settings routes: branding, website URL, crawl, staff management."""

import re
import json
import secrets
import threading
import logging
from datetime import datetime

from flask import Blueprint, request, jsonify, url_for, current_app
from flask_login import login_required, current_user

from models import db, User, Church, CrawledPage, Invite
from config import DEFAULT_COLOR, FROM_EMAIL, SUPPORT_EMAIL
from denominations import (
    LocalPracticeError, church_profile, denomination_options,
    get_denomination_profile, is_valid_denomination, load_local_practices,
    local_practice_schema, validate_local_practices, validate_statement_of_faith,
)
from helpers import build_branding_dict, iso_utc, is_safe_url
from emails import send_invite_email

settings_bp = Blueprint("settings", __name__)
log = logging.getLogger("wesley")

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ── Church branding API ──────────────────────────────────────────────────────

@settings_bp.route("/api/church/branding", methods=["GET"])
@login_required
def get_church_branding():
    return jsonify(build_branding_dict(current_user.church))


@settings_bp.route("/api/church/branding", methods=["POST"])
@login_required
def save_church_branding():
    data = request.get_json(silent=True) or {}
    church = current_user.church

    bot_name = (data.get("bot_name") or "").strip()
    bot_subtitle = (data.get("bot_subtitle") or "").strip()
    welcome_message = (data.get("welcome_message") or "").strip()
    primary_color = (data.get("primary_color") or "").strip()
    church_city = (data.get("church_city") or "").strip()
    raw_sugs = data.get("starter_questions") or []

    if not bot_name:
        return jsonify({"error": "Bot name cannot be empty."}), 400
    if not welcome_message:
        return jsonify({"error": "Welcome message cannot be empty."}), 400
    if primary_color and not _HEX_COLOR_RE.match(primary_color):
        return jsonify({"error": "Primary color must be a valid hex color (e.g. #1a2b3c)."}), 400

    clean_sugs = [str(s).strip()[:200] for s in raw_sugs if str(s).strip()][:4]

    church.bot_name = bot_name[:100]
    church.bot_subtitle = bot_subtitle[:200] if bot_subtitle else None
    church.welcome_message = welcome_message[:500]
    church.primary_color = primary_color if primary_color else DEFAULT_COLOR
    church.church_city = church_city[:200] if church_city else None
    church.starter_questions = json.dumps(clean_sugs) if clean_sugs else None
    db.session.commit()
    return jsonify({"ok": True})


# ── Church settings API (website URL + crawl stats) ──────────────────────────

@settings_bp.route("/api/church/settings", methods=["GET"])
@login_required
def get_church_settings():
    church = current_user.church
    page_count = CrawledPage.query.filter_by(church_id=church.id).count()
    return jsonify({
        "website_url": church.website_url or "",
        "last_crawled_at": iso_utc(church.last_crawled_at),
        "page_count": page_count,
        "church_id": church.id,
    })


@settings_bp.route("/api/church/settings", methods=["POST"])
@login_required
def save_church_settings():
    data = request.get_json(silent=True) or {}
    url = (data.get("website_url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    if len(url) > 500:
        return jsonify({"error": "URL must be 500 characters or fewer."}), 400
    if url and not is_safe_url(url):
        return jsonify({"error": "URL must not point to a private or internal network address."}), 400
    current_user.church.website_url = url or None
    db.session.commit()
    return jsonify({"ok": True})


# ── Theology & Affiliation API ───────────────────────────────────────────────
#
# Read is open to any signed-in staff member of the church; every write requires
# the church-admin role. Every query is scoped by current_user.church_id, so one
# tenant can never read or write another's theological settings.

def _theology_payload(church) -> dict:
    profile = church_profile(church)
    return {
        "denomination": profile.key,
        "profile": profile.to_dict(),
        "selected_profile_version": church.denomination_profile_version or "",
        "profile_version_current": (
            not church.denomination_profile_version
            or church.denomination_profile_version == profile.version
        ),
        "denomination_updated_at": iso_utc(church.denomination_updated_at),
        "options": denomination_options(),
        "local_practices": load_local_practices(church),
        "local_practice_schema": local_practice_schema(),
        "statement_of_faith": church.statement_of_faith or "",
        "can_manage": current_user.role == "admin",
    }


@settings_bp.route("/api/church/theology", methods=["GET"])
@login_required
def get_church_theology():
    return jsonify(_theology_payload(current_user.church))


@settings_bp.route("/api/church/theology/denomination", methods=["POST"])
@login_required
def save_church_denomination():
    """Change the church's denominational affiliation (admin only).

    Local content — approved Q&A, snippets, documents, local practices, and the
    statement of faith — is deliberately preserved. It may need review under the
    new profile, which the UI warns about, but destroying it would be worse.
    """
    if current_user.role != "admin":
        return jsonify({"error": "Only church admins can change denominational settings."}), 403

    data = request.get_json(silent=True) or {}
    key = data.get("denomination")
    if not is_valid_denomination(key):
        return jsonify({"error": "Unknown denomination."}), 400

    church = current_user.church
    changed = church.denomination != key
    if changed and not data.get("confirm"):
        return jsonify({
            "error": "Changing denomination requires explicit confirmation.",
            "confirmation_required": True,
        }), 400

    profile = get_denomination_profile(key)
    church.denomination = profile.key
    church.denomination_profile_version = profile.version
    church.denomination_updated_at = datetime.utcnow()
    db.session.commit()
    log.info("[DENOM] church_id=%d denomination set to %s (changed=%s) by user_id=%d",
             church.id, profile.key, changed, current_user.id)
    return jsonify({"ok": True, "changed": changed, **_theology_payload(church)})


@settings_bp.route("/api/church/theology/local-practices", methods=["POST"])
@login_required
def save_church_local_practices():
    """Save validated structured local practices and statement of faith (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Only church admins can change denominational settings."}), 403

    data = request.get_json(silent=True) or {}
    church = current_user.church
    try:
        if "local_practices" in data:
            cleaned = validate_local_practices(data.get("local_practices"))
            church.local_practices = json.dumps(cleaned) if cleaned else None
        if "statement_of_faith" in data:
            statement = validate_statement_of_faith(data.get("statement_of_faith"))
            church.statement_of_faith = statement or None
    except LocalPracticeError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, **_theology_payload(church)})


# ── Manual re-crawl ──────────────────────────────────────────────────────────

@settings_bp.route("/api/church/crawl", methods=["POST"])
@login_required
def trigger_crawl():
    import logging
    log = logging.getLogger("wesley")

    church = current_user.church
    if not church.website_url:
        return jsonify({"error": "No website URL configured. Save a URL first."}), 400

    crawl_url  = church.website_url
    church_id  = church.id
    app = current_app._get_current_object()

    def run_crawl():
        try:
            with app.app_context():
                from crawler import crawl_church_website
                result = crawl_church_website(church_id, crawl_url)
                log.info("Manual crawl church_id=%d: %s", church_id, result)
        except Exception:
            log.exception("Background crawl failed for church_id=%d", church_id)

    t = threading.Thread(target=run_crawl, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Crawl started in the background."})


# ── Staff management API ─────────────────────────────────────────────────────

@settings_bp.route("/api/staff")
@login_required
def list_staff():
    """Return all users belonging to the current church (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403
    users = (
        User.query
        .filter_by(church_id=current_user.church_id)
        .order_by(User.created_at)
        .all()
    )
    pending = (
        Invite.query
        .filter_by(church_id=current_user.church_id, accepted=False)
        .order_by(Invite.created_at)
        .all()
    )
    return jsonify({
        "staff": [
            {"id": u.id, "email": u.email, "role": u.role, "created_at": iso_utc(u.created_at)}
            for u in users
        ],
        "pending_invites": [
            {"id": inv.id, "email": inv.email, "created_at": iso_utc(inv.created_at)}
            for inv in pending
        ],
    })


@settings_bp.route("/api/staff/invite", methods=["POST"])
@login_required
def invite_staff():
    """Send a staff invitation email (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403

    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"error": "Email is required."}), 400

    existing = User.query.filter_by(email=email, church_id=current_user.church_id).first()
    if existing:
        return jsonify({"error": "A user with that email already exists on your team."}), 400

    dup = Invite.query.filter_by(
        email=email, church_id=current_user.church_id, accepted=False
    ).first()
    if dup:
        return jsonify({"error": "An invitation has already been sent to that email."}), 400

    token  = secrets.token_urlsafe(32)
    invite = Invite(
        church_id=current_user.church_id,
        email=email,
        token=token,
    )
    db.session.add(invite)
    db.session.commit()

    invite_url = url_for("auth.accept_invite_page", token=token, _external=True)
    church_name = current_user.church.name
    _app = current_app._get_current_object()

    def _send_invite():
        try:
            with _app.app_context():
                send_invite_email(email, church_name, invite_url, FROM_EMAIL, SUPPORT_EMAIL)
        except Exception:
            log.exception("Failed sending staff invite email to %s", email)

    threading.Thread(target=_send_invite, daemon=True).start()

    return jsonify({"ok": True}), 201


@settings_bp.route("/api/staff/invite/<int:invite_id>/resend", methods=["POST"])
@login_required
def resend_invite(invite_id):
    """Resend a pending staff invitation email (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403

    invite = Invite.query.filter_by(
        id=invite_id, church_id=current_user.church_id, accepted=False
    ).first()
    if not invite:
        return jsonify({"error": "Invite not found."}), 404

    invite_url = url_for("auth.accept_invite_page", token=invite.token, _external=True)
    church_name = current_user.church.name
    _app = current_app._get_current_object()

    def _resend():
        try:
            with _app.app_context():
                send_invite_email(invite.email, church_name, invite_url, FROM_EMAIL, SUPPORT_EMAIL)
        except Exception:
            log.exception("Failed resending staff invite email to %s", invite.email)

    threading.Thread(target=_resend, daemon=True).start()
    return jsonify({"ok": True})


@settings_bp.route("/api/staff/invite/<int:invite_id>", methods=["DELETE"])
@login_required
def cancel_invite(invite_id):
    """Cancel a pending staff invitation (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403

    invite = Invite.query.filter_by(
        id=invite_id, church_id=current_user.church_id, accepted=False
    ).first()
    if not invite:
        return jsonify({"error": "Invite not found."}), 404

    db.session.delete(invite)
    db.session.commit()
    return jsonify({"ok": True})


@settings_bp.route("/api/staff/<int:user_id>", methods=["DELETE"])
@login_required
def remove_staff(user_id):
    """Remove a staff user from the church (admin only; cannot remove admins or self)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403

    if user_id == current_user.id:
        return jsonify({"error": "You cannot remove yourself."}), 400

    user = User.query.filter_by(id=user_id, church_id=current_user.church_id).first()
    if not user:
        return jsonify({"error": "User not found."}), 404

    if user.role == "admin":
        return jsonify({"error": "Admin accounts cannot be removed via this endpoint."}), 400

    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})
