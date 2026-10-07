"""Settings routes: branding, website URL, crawl, theology, staff management."""

import re
import json
import secrets
import threading
import logging

from flask import Blueprint, request, jsonify, url_for, current_app
from flask_login import login_required, current_user

from models import db, User, CrawledPage, Invite
from config import DEFAULT_COLOR, FROM_EMAIL, ORG_DOMAIN, SUPPORT_EMAIL
from denominations import (
    PROFILE, LocalPracticeError, load_local_practices,
    local_practice_schema, validate_local_practices, validate_statement_of_faith,
)
from organization import get_org
from helpers import build_branding_dict, iso_utc, is_safe_url
from emails import send_invite_email

settings_bp = Blueprint("settings", __name__)
log = logging.getLogger("wesley")

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ── Church branding API ──────────────────────────────────────────────────────

@settings_bp.route("/api/church/branding", methods=["GET"])
@login_required
def get_church_branding():
    return jsonify(build_branding_dict())


@settings_bp.route("/api/church/branding", methods=["POST"])
@login_required
def save_church_branding():
    data = request.get_json(silent=True) or {}
    church = get_org()

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
    church = get_org()
    page_count = CrawledPage.query.count()
    return jsonify({
        "website_url": church.website_url or "",
        "last_crawled_at": iso_utc(church.last_crawled_at),
        "page_count": page_count,
        # The id the website embed passes as data-church-id.
        "church_id": church.legacy_widget_id or church.id,
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
    get_org().website_url = url or None
    db.session.commit()
    return jsonify({"ok": True})


# ── Theology API ─────────────────────────────────────────────────────────────
#
# The denomination is fixed (Wesleyan United Methodist), so there is nothing to
# select. Read is open to any signed-in staff member; writes need the admin role.

def _theology_payload(church) -> dict:
    return {
        "profile": PROFILE.to_dict(),
        "local_practices": load_local_practices(church),
        "local_practice_schema": local_practice_schema(),
        "statement_of_faith": church.statement_of_faith or "",
        "can_manage": current_user.role == "admin",
    }


@settings_bp.route("/api/church/theology", methods=["GET"])
@login_required
def get_church_theology():
    return jsonify(_theology_payload(get_org()))


@settings_bp.route("/api/church/theology/local-practices", methods=["POST"])
@login_required
def save_church_local_practices():
    """Save validated structured local practices and statement of faith (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Only admins can change local practice settings."}), 403

    data = request.get_json(silent=True) or {}
    church = get_org()
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

    church = get_org()
    if not church.website_url:
        return jsonify({"error": "No website URL configured. Save a URL first."}), 400

    crawl_url  = church.website_url
    app = current_app._get_current_object()

    def run_crawl():
        try:
            with app.app_context():
                from crawler import crawl_church_website
                result = crawl_church_website(crawl_url)
                log.info("Manual crawl: %s", result)
        except Exception:
            log.exception("Background crawl failed")

    t = threading.Thread(target=run_crawl, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Crawl started in the background."})


# ── Staff management API ─────────────────────────────────────────────────────

@settings_bp.route("/api/staff")
@login_required
def list_staff():
    """Return all users (admin only)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403
    users = (
        User.query
        
        .order_by(User.created_at)
        .all()
    )
    pending = (
        Invite.query
        .filter_by(accepted=False)
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
    if not email.endswith("@" + ORG_DOMAIN):
        return jsonify({"error": f"Staff accounts must use an @{ORG_DOMAIN} email address."}), 400

    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({"error": "A user with that email already exists on your team."}), 400

    dup = Invite.query.filter_by(
        email=email, accepted=False
    ).first()
    if dup:
        return jsonify({"error": "An invitation has already been sent to that email."}), 400

    token  = secrets.token_urlsafe(32)
    invite = Invite(
        email=email,
        token=token,
    )
    db.session.add(invite)
    db.session.commit()

    invite_url = url_for("auth.accept_invite_page", token=token, _external=True)
    church_name = get_org().name
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
        id=invite_id, accepted=False
    ).first()
    if not invite:
        return jsonify({"error": "Invite not found."}), 404

    invite_url = url_for("auth.accept_invite_page", token=invite.token, _external=True)
    church_name = get_org().name
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
        id=invite_id, accepted=False
    ).first()
    if not invite:
        return jsonify({"error": "Invite not found."}), 404

    db.session.delete(invite)
    db.session.commit()
    return jsonify({"ok": True})


@settings_bp.route("/api/staff/<int:user_id>", methods=["DELETE"])
@login_required
def remove_staff(user_id):
    """Remove a staff user (admin only; cannot remove admins or self)."""
    if current_user.role != "admin":
        return jsonify({"error": "Forbidden."}), 403

    if user_id == current_user.id:
        return jsonify({"error": "You cannot remove yourself."}), 400

    user = User.query.filter_by(id=user_id).first()
    if not user:
        return jsonify({"error": "User not found."}), 404

    if user.role == "admin":
        return jsonify({"error": "Admin accounts cannot be removed via this endpoint."}), 400

    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})
