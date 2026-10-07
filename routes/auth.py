"""Sign-in routes.

Staff sign in with Google Workspace. The server performs the whole OAuth
exchange itself and trusts nothing the browser says about who the person is:

  1. ``state`` and ``nonce`` are random, stored in the session, and checked.
  2. The ID token's signature, audience, issuer and expiry are verified against
     Google's published keys (``google.oauth2.id_token``).
  3. The token must say the email is verified, the email must be on the church
     domain, and the Workspace ``hd`` claim must equal that domain. The
     ``hd`` request parameter we send to Google is only a hint to the account
     chooser; it is never relied on.
  4. The person must already exist and be active. Signing in with a church
     Google account is not enough on its own; an admin adds people and roles.

While Google is not configured (no GOOGLE_CLIENT_ID), the older password
sign-in remains so nobody is locked out. Once it is configured, the password
routes disappear.
"""

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from flask import (
    Blueprint, current_app, jsonify, redirect, render_template, request, session,
    url_for,
)
from flask_login import current_user, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from audit import log_event
from config import (
    APP_URL, FROM_EMAIL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ORG_DOMAIN,
    SUPPORT_EMAIL,
)
from emails import send_reset_email
from helpers import validate_csrf_json
from models import User, db

# Pre-computed dummy hash for constant-time comparison on failed lookups
_DUMMY_HASH = generate_password_hash("dummy-constant-time-compare", method="pbkdf2:sha256")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

auth_bp = Blueprint("auth", __name__)


def google_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _redirect_uri() -> str:
    return APP_URL.rstrip("/") + "/auth/google/callback"


def _on_church_domain(email: str) -> bool:
    return (email or "").lower().endswith("@" + ORG_DOMAIN.lower())


# ── Pages ────────────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("pages.home_page"))
    return render_template(
        "auth.html", google=google_enabled(), domain=ORG_DOMAIN,
        error=request.args.get("error", ""),
    )


@auth_bp.route("/logout")
def logout():
    if current_user.is_authenticated:
        log_event("auth.logout")
    logout_user()
    session.clear()
    return redirect(url_for("auth.login_page"))


# ── Google ───────────────────────────────────────────────────────────────────

def _verify_id_token(raw_token: str) -> dict:
    """Verify a Google ID token's signature, audience, issuer and expiry.

    Split out so tests can supply claims without contacting Google. Raises
    ValueError on any failure.
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(raw_token, google_requests.Request(), GOOGLE_CLIENT_ID)


def check_identity(claims: dict, nonce: str) -> str:
    """The email to sign in, or raise ValueError with a reason for the audit log.

    Everything here is decided from the verified token, never from the browser.
    """
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ValueError("nonce mismatch")
    if claims.get("email_verified") is not True:
        raise ValueError("email not verified")
    email = str(claims.get("email", "")).lower()
    if not _on_church_domain(email):
        raise ValueError("email outside the church domain")
    if str(claims.get("hd", "")).lower() != ORG_DOMAIN.lower():
        raise ValueError("not a Workspace account on the church domain")
    if not claims.get("sub"):
        raise ValueError("no subject")
    return email


@auth_bp.route("/auth/google")
def google_start():
    if not google_enabled():
        return "Google sign-in is not configured on this server.", 404
    session["g_state"] = secrets.token_urlsafe(24)
    session["g_nonce"] = secrets.token_urlsafe(24)
    return redirect(GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": session["g_state"],
        "nonce": session["g_nonce"],
        "hd": ORG_DOMAIN,            # a hint to the account chooser only
        "prompt": "select_account",
        "access_type": "online",
    }))


def _deny(reason: str, email: str = None):
    log_event("auth.denied", source=reason, detail={"email": email})
    return redirect(url_for("auth.login_page", error="denied"))


@auth_bp.route("/auth/google/callback")
def google_callback():
    if not google_enabled():
        return "Google sign-in is not configured on this server.", 404
    state = session.pop("g_state", None)
    nonce = session.pop("g_nonce", None)
    if request.args.get("error"):
        return _deny("google error: " + request.args["error"][:60])
    if not state or not secrets.compare_digest(request.args.get("state", ""), state):
        return _deny("state mismatch")

    try:
        resp = requests.post(GOOGLE_TOKEN_URL, data={
            "code": request.args.get("code", ""),
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        }, timeout=15)
        resp.raise_for_status()
        claims = _verify_id_token(resp.json()["id_token"])
        email = check_identity(claims, nonce)
    except Exception as exc:        # network, bad code, bad signature, bad claims
        return _deny(f"token rejected: {type(exc).__name__}: {str(exc)[:80]}")

    user = (User.query.filter_by(google_sub=claims["sub"]).first()
            or User.query.filter_by(email=email).first())
    if user is None or not user.active:
        return _deny("no active account", email)
    if user.google_sub and user.google_sub != claims["sub"]:
        return _deny("account id changed", email)

    user.google_sub = claims["sub"]
    user.email = email
    user.display_name = claims.get("name") or user.display_name
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    _start_session(user, "google")
    return redirect(url_for("pages.home_page"))


def _start_session(user, method: str) -> None:
    session.clear()
    login_user(user)
    session.permanent = True
    session["login_at"] = datetime.utcnow().isoformat()
    session["csrf_token"] = secrets.token_hex(32)
    log_event("auth.login", detail={"method": method}, user=user)


# ── Password sign-in (only while Google is not configured) ───────────────────

def _passwords_allowed():
    """A 404 response when Google is configured, else None."""
    if google_enabled():
        return jsonify({"error": "Not found."}), 404
    return None


def _auth_rate_limited():
    limiter = current_app.config.get("AUTH_LIMITER")
    if limiter and limiter.is_limited(request.remote_addr or "unknown"):
        return jsonify({
            "error": "Too many attempts. Please wait a few minutes and try again.",
        }), 429
    return None


@auth_bp.route("/api/auth/login", methods=["POST"])
def api_login():
    gone = _passwords_allowed()
    if gone:
        return gone
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
    ok = check_password_hash(user.password_hash if user and user.password_hash else _DUMMY_HASH, password)
    if not ok or not user or not user.active or not _on_church_domain(user.email):
        log_event("auth.denied", source="password", detail={"email": email})
        return jsonify({"error": "Invalid email or password."}), 401

    user.last_login_at = datetime.utcnow()
    db.session.commit()
    _start_session(user, "password")
    return jsonify({"ok": True})


@auth_bp.route("/forgot-password")
def forgot_password_page():
    if google_enabled():
        return redirect(url_for("auth.login_page"))
    if current_user.is_authenticated:
        return redirect(url_for("pages.home_page"))
    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>")
def reset_password_page(token: str):
    if google_enabled():
        return redirect(url_for("auth.login_page"))
    if current_user.is_authenticated:
        return redirect(url_for("pages.home_page"))
    user = User.query.filter_by(reset_token=token).first()
    token_valid = (
        user is not None
        and user.reset_token_expires is not None
        and user.reset_token_expires > datetime.utcnow()
    )
    return render_template("reset_password.html", token=token, token_valid=token_valid)


@auth_bp.route("/api/auth/forgot-password", methods=["POST"])
def api_forgot_password():
    gone = _passwords_allowed()
    if gone:
        return gone
    limited = _auth_rate_limited()
    if limited:
        return limited
    err, status = validate_csrf_json()
    if err:
        return err, status

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": True})

    user = User.query.filter_by(email=email).first()
    if user and user.active:
        token = secrets.token_urlsafe(32)
        user.reset_token = token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        reset_url = url_for("auth.reset_password_page", token=token, _external=True)
        send_reset_email(user.email, reset_url, FROM_EMAIL, SUPPORT_EMAIL)
    return jsonify({"ok": True})


@auth_bp.route("/api/auth/reset-password", methods=["POST"])
def api_reset_password():
    gone = _passwords_allowed()
    if gone:
        return gone
    err, status = validate_csrf_json()
    if err:
        return err, status

    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    password = (data.get("password") or "").strip()
    confirm = (data.get("confirm") or "").strip()

    if not token or not password or not confirm:
        return jsonify({"error": "All fields are required."}), 400
    if password != confirm:
        return jsonify({"error": "Passwords do not match."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    user = User.query.filter_by(reset_token=token).first()
    if not user or user.reset_token_expires is None or user.reset_token_expires <= datetime.utcnow():
        return jsonify({"error": "This reset link is invalid or has expired."}), 400

    user.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    user.reset_token = None
    user.reset_token_expires = None
    db.session.commit()
    log_event("auth.password_reset", user=user)
    return jsonify({"ok": True})
