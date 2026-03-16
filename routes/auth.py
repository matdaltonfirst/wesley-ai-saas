"""Auth routes: login, signup, logout, password reset, invite accept."""

import secrets
import threading
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, render_template, redirect, url_for
from flask_login import login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash

from models import db, User, Church, Invite
from config import FROM_EMAIL, APP_URL, SUPPORT_EMAIL
from emails import send_reset_email, send_welcome_email, send_invite_email
from helpers import validate_csrf_json

auth_bp = Blueprint("auth", __name__)


# ── Pages ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("pages.chat_page"))
    return render_template("auth.html", mode="login")


@auth_bp.route("/signup")
def signup_page():
    if current_user.is_authenticated:
        return redirect(url_for("pages.chat_page"))
    return render_template("auth.html", mode="signup")


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
    church_name = ""
    if token_valid and invite:
        church = Church.query.get(invite.church_id)
        church_name = church.name if church else ""
    return render_template(
        "invite.html",
        token=token,
        token_valid=token_valid,
        church_name=church_name,
    )


# ── API endpoints ────────────────────────────────────────────────────────────

@auth_bp.route("/api/auth/signup", methods=["POST"])
def api_signup():
    err, status = validate_csrf_json()
    if err:
        return err, status

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    church_name = (data.get("church_name") or "").strip()

    if not email or not password or not church_name:
        return jsonify({"error": "Email, password, and church name are required."}), 400
    if len(email) > 254:
        return jsonify({"error": "Email address is too long."}), 400
    if len(church_name) > 200:
        return jsonify({"error": "Church name must be 200 characters or fewer."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    if len(password) > 128:
        return jsonify({"error": "Password must be 128 characters or fewer."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"error": "An account with that email already exists."}), 400

    church = Church(
        name=church_name,
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
    )
    db.session.add(church)
    db.session.flush()

    user = User(
        email=email,
        password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        church_id=church.id,
    )
    db.session.add(user)
    db.session.commit()

    login_user(user)

    _church_name   = church.name
    _trial_ends_at = church.trial_ends_at
    threading.Thread(
        target=send_welcome_email,
        args=(email, _church_name, _trial_ends_at, FROM_EMAIL, APP_URL, SUPPORT_EMAIL),
        daemon=True,
    ).start()

    return jsonify({"ok": True}), 201


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_login():
    err, status = validate_csrf_json()
    if err:
        return err, status

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"error": "Invalid email or password."}), 401

    login_user(user)
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/forgot-password", methods=["POST"])
def api_forgot_password():
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

    if User.query.filter_by(email=invite.email).first():
        return jsonify({"error": "An account with this email already exists. Please log in."}), 400

    user = User(
        email=invite.email,
        password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        church_id=invite.church_id,
        role="staff",
    )
    db.session.add(user)
    invite.accepted = True
    db.session.commit()

    login_user(user)
    return jsonify({"ok": True})
