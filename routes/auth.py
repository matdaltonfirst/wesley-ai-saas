"""Auth routes: login, logout, password reset, invite accept.

Accounts are staff of one church and are created only by invitation; there is
no public signup. Google Workspace sign-in replaces passwords in Phase 1b."""

import secrets
from datetime import datetime, timedelta

from flask import Blueprint, current_app, request, jsonify, render_template, redirect, url_for
from flask_login import login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash

from models import db, User, Invite
from config import FROM_EMAIL, ORG_DOMAIN, SUPPORT_EMAIL
from organization import get_org
from emails import send_reset_email
from helpers import validate_csrf_json

# Pre-computed dummy hash for constant-time comparison on failed lookups
_DUMMY_HASH = generate_password_hash("dummy-constant-time-compare", method="pbkdf2:sha256")

auth_bp = Blueprint("auth", __name__)


# ── Pages ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("pages.chat_page"))
    return render_template("auth.html")


@auth_bp.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/forgot-password")
def forgot_password_page():
    if current_user.is_authenticated:
        return redirect(url_for("pages.chat_page"))
    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>")
def reset_password_page(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("pages.chat_page"))
    user = User.query.filter_by(reset_token=token).first()
    token_valid = (
        user is not None
        and user.reset_token_expires is not None
        and user.reset_token_expires > datetime.utcnow()
    )
    return render_template("reset_password.html", token=token, token_valid=token_valid)


@auth_bp.route("/invite/<token>")
def accept_invite_page(token: str):
    """Public invite acceptance page — validates token and renders invite.html."""
    invite = Invite.query.filter_by(token=token, accepted=False).first()
    cutoff = datetime.utcnow() - timedelta(days=7)
    token_valid = (
        invite is not None
        and invite.created_at >= cutoff
    )
    return render_template(
        "invite.html",
        token=token,
        token_valid=token_valid,
        church_name=get_org().name if token_valid else "",
    )


# ── API endpoints ────────────────────────────────────────────────────────────

def _auth_rate_limited():
    """A 429 response when this IP has spent its auth budget, else None.

    Guards the three unauthenticated endpoints that are cheap to call and
    expensive to leave open: password guessing on login, free-trial farming on
    signup, and reset-email flooding on forgot-password.
    """
    limiter = current_app.config.get("AUTH_LIMITER")
    if limiter and limiter.is_limited(request.remote_addr or "unknown"):
        return jsonify({
            "error": "Too many attempts. Please wait a few minutes and try again.",
        }), 429
    return None


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_login():
    limited = _auth_rate_limited()
    if limited:
        return limited

    err, status = validate_csrf_json()
    if err:
        return err, status

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    user = User.query.filter_by(email=email).first()
    # Always perform password hash comparison to prevent timing-based user enumeration
    if not check_password_hash(user.password_hash if user else _DUMMY_HASH, password) or not user:
        return jsonify({"error": "Invalid email or password."}), 401
    # Only staff on the church's own email domain may sign in.
    if not user.email.lower().endswith("@" + ORG_DOMAIN):
        return jsonify({"error": "Invalid email or password."}), 401

    login_user(user)
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/forgot-password", methods=["POST"])
def api_forgot_password():
    limited = _auth_rate_limited()
    if limited:
        return limited

    err, status = validate_csrf_json()
    if err:
        return err, status

    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"ok": True})

    user = User.query.filter_by(email=email).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.reset_token         = token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        reset_url = url_for("auth.reset_password_page", token=token, _external=True)
        send_reset_email(user.email, reset_url, FROM_EMAIL, SUPPORT_EMAIL)

    return jsonify({"ok": True})


@auth_bp.route("/api/auth/reset-password", methods=["POST"])
def api_reset_password():
    err, status = validate_csrf_json()
    if err:
        return err, status

    data     = request.get_json(silent=True) or {}
    token    = (data.get("token") or "").strip()
    password = (data.get("password") or "").strip()
    confirm  = (data.get("confirm") or "").strip()

    if not token or not password or not confirm:
        return jsonify({"error": "All fields are required."}), 400
    if password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    user = User.query.filter_by(reset_token=token).first()
    if not user or user.reset_token_expires is None or user.reset_token_expires <= datetime.utcnow():
        return jsonify({"error": "This reset link is invalid or has expired."}), 400

    user.password_hash       = generate_password_hash(password, method="pbkdf2:sha256")
    user.reset_token         = None
    user.reset_token_expires = None
    db.session.commit()

    return jsonify({"ok": True})


@auth_bp.route("/api/invite/accept", methods=["POST"])
def api_accept_invite():
    """Create a staff account from a valid invite token."""
    err, status = validate_csrf_json()
    if err:
        return err, status

    data     = request.get_json(silent=True) or {}
    token    = (data.get("token") or "").strip()
    password = (data.get("password") or "").strip()
    confirm  = (data.get("confirm") or "").strip()

    if not token or not password or not confirm:
        return jsonify({"error": "All fields are required."}), 400
    if password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    invite = Invite.query.filter_by(token=token, accepted=False).first()
    cutoff = datetime.utcnow() - timedelta(days=7)
    if not invite or invite.created_at < cutoff:
        return jsonify({"error": "This invitation link is invalid or has expired."}), 400

    if not invite.email.lower().endswith("@" + ORG_DOMAIN):
        return jsonify({"error": "This invitation link is invalid or has expired."}), 400
    if User.query.filter_by(email=invite.email).first():
        return jsonify({"error": "An account with this email already exists. Please log in."}), 400

    user = User(
        email=invite.email,
        password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        role="staff",
    )
    db.session.add(user)
    invite.accepted = True
    db.session.commit()

    login_user(user)
    return jsonify({"ok": True})
