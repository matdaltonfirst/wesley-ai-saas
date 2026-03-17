"""Tests for auth routes: signup, login, logout, forgot/reset password, invite."""

import secrets
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

from models import db, User, Church, Invite


# ── Signup ────────────────────────────────────────────────────────────────────

class TestSignup:
    def test_signup_success(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "newchurch@example.com",
            "password": "strongpass1",
            "church_name": "New Life Church",
        })
        assert res.status_code == 201
        assert res.get_json()["ok"] is True

        # Cleanup
        u = User.query.filter_by(email="newchurch@example.com").first()
        if u:
            Church.query.filter_by(id=u.church_id).delete()
            db.session.delete(u)
            db.session.commit()

    def test_signup_missing_email(self, client):
        res = client.post("/api/auth/signup", json={
            "password": "strongpass1",
            "church_name": "Missing Email Church",
        })
        assert res.status_code == 400
        assert "required" in res.get_json()["error"].lower()

    def test_signup_missing_church_name(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "nochurch@example.com",
            "password": "strongpass1",
        })
        assert res.status_code == 400

    def test_signup_password_too_short(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "short@example.com",
            "password": "abc",
            "church_name": "Short Pass Church",
        })
        assert res.status_code == 400
        assert "8 characters" in res.get_json()["error"]

    def test_signup_password_too_long(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "longpass@example.com",
            "password": "x" * 129,
            "church_name": "Long Pass Church",
        })
        assert res.status_code == 400
        assert "128" in res.get_json()["error"]

    def test_signup_email_too_long(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "a" * 250 + "@example.com",
            "password": "strongpass1",
            "church_name": "Long Email Church",
        })
        assert res.status_code == 400
        assert "too long" in res.get_json()["error"].lower()

    def test_signup_church_name_too_long(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "longname@example.com",
            "password": "strongpass1",
            "church_name": "C" * 201,
        })
        assert res.status_code == 400
        assert "200" in res.get_json()["error"]

    def test_signup_duplicate_email(self, client, admin_user):
        res = client.post("/api/auth/signup", json={
            "email": admin_user.email,
            "password": "strongpass1",
            "church_name": "Duplicate Church",
        })
        assert res.status_code == 400
        assert "already exists" in res.get_json()["error"].lower()


# ── Login ─────────────────────────────────────────────────────────────────────

class TestLogin:
    def test_login_success(self, client, admin_user):
        res = client.post("/api/auth/login", json={
            "email": admin_user.email,
            "password": admin_user._plaintext_password,
        })
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

    def test_login_wrong_password(self, client, admin_user):
        res = client.post("/api/auth/login", json={
            "email": admin_user.email,
            "password": "wrongpassword",
        })
        assert res.status_code == 401
        assert "Invalid" in res.get_json()["error"]

    def test_login_unknown_email(self, client):
        res = client.post("/api/auth/login", json={
            "email": "nobody@nowhere.com",
            "password": "somepassword",
        })
        assert res.status_code == 401

    def test_login_missing_fields(self, client):
        res = client.post("/api/auth/login", json={"email": "someone@example.com"})
        assert res.status_code == 401  # empty password will fail the hash check

    def test_login_no_body(self, client):
        res = client.post("/api/auth/login", data="not json",
                          content_type="text/plain")
        assert res.status_code == 401


# ── Logout ────────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_redirects(self, auth_client):
        res = auth_client.get("/logout")
        assert res.status_code == 302
        assert "/login" in res.headers["Location"]

    def test_logout_unauthenticated_still_redirects(self, client):
        res = client.get("/logout")
        assert res.status_code == 302


# ── Forgot password ───────────────────────────────────────────────────────────

class TestForgotPassword:
    def test_always_returns_ok_for_known_email(self, client, admin_user):
        """Should return ok=True even for a known email (prevents enumeration)."""
        res = client.post("/api/auth/forgot-password", json={"email": admin_user.email})
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

    def test_always_returns_ok_for_unknown_email(self, client):
        """Should return ok=True for unknown emails (prevents user enumeration)."""
        res = client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

    def test_empty_email_returns_ok(self, client):
        """Empty email is a no-op — returns ok silently."""
        res = client.post("/api/auth/forgot-password", json={"email": ""})
        assert res.status_code == 200


# ── Reset password ────────────────────────────────────────────────────────────

class TestResetPassword:
    def _create_user_with_token(self, church):
        """Helper: create a user with a valid reset token."""
        token = secrets.token_urlsafe(32)
        u = User(
            email="resetme@example.com",
            password_hash=generate_password_hash("oldpassword1", method="pbkdf2:sha256"),
            church_id=church.id,
            reset_token=token,
            reset_token_expires=datetime.utcnow() + timedelta(hours=1),
        )
        db.session.add(u)
        db.session.commit()
        return u, token

    def test_reset_success(self, client, church):
        u, token = self._create_user_with_token(church)
        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword1",
            "confirm": "newpassword1",
        })
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

        # Token should be cleared
        db.session.refresh(u)
        assert u.reset_token is None
        db.session.delete(u)
        db.session.commit()

    def test_reset_invalid_token(self, client):
        res = client.post("/api/auth/reset-password", json={
            "token": "not-a-real-token",
            "password": "newpassword1",
            "confirm": "newpassword1",
        })
        assert res.status_code == 400
        assert "invalid" in res.get_json()["error"].lower()

    def test_reset_expired_token(self, client, church):
        token = secrets.token_urlsafe(32)
        u = User(
            email="expired@example.com",
            password_hash=generate_password_hash("oldpassword1", method="pbkdf2:sha256"),
            church_id=church.id,
            reset_token=token,
            reset_token_expires=datetime.utcnow() - timedelta(hours=2),  # expired
        )
        db.session.add(u)
        db.session.commit()

        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword1",
            "confirm": "newpassword1",
        })
        assert res.status_code == 400

        db.session.delete(u)
        db.session.commit()

    def test_reset_passwords_dont_match(self, client, church):
        u, token = self._create_user_with_token(church)
        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "newpassword1",
            "confirm": "differentpassword1",
        })
        assert res.status_code == 400
        assert "match" in res.get_json()["error"].lower()

        db.session.delete(u)
        db.session.commit()

    def test_reset_password_too_short(self, client, church):
        u, token = self._create_user_with_token(church)
        res = client.post("/api/auth/reset-password", json={
            "token": token,
            "password": "short",
            "confirm": "short",
        })
        assert res.status_code == 400

        db.session.delete(u)
        db.session.commit()


# ── Invite accept ─────────────────────────────────────────────────────────────

class TestInviteAccept:
    def _create_invite(self, church):
        token = secrets.token_urlsafe(32)
        invite = Invite(
            church_id=church.id,
            email="invitee@example.com",
            token=token,
        )
        db.session.add(invite)
        db.session.commit()
        return invite, token

    def test_accept_invite_success(self, client, church):
        invite, token = self._create_invite(church)
        res = client.post("/api/invite/accept", json={
            "token": token,
            "password": "newstaffpass1",
            "confirm": "newstaffpass1",
        })
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

        # New user should exist with staff role
        u = User.query.filter_by(email="invitee@example.com").first()
        assert u is not None
        assert u.role == "staff"

        db.session.delete(u)
        db.session.delete(invite)
        db.session.commit()

    def test_accept_invite_invalid_token(self, client):
        res = client.post("/api/invite/accept", json={
            "token": "bogus-token",
            "password": "newstaffpass1",
            "confirm": "newstaffpass1",
        })
        assert res.status_code == 400

    def test_accept_invite_expired(self, client, church):
        token = secrets.token_urlsafe(32)
        invite = Invite(
            church_id=church.id,
            email="expired_invite@example.com",
            token=token,
            created_at=datetime.utcnow() - timedelta(days=8),  # older than 7 days
        )
        db.session.add(invite)
        db.session.commit()

        res = client.post("/api/invite/accept", json={
            "token": token,
            "password": "newstaffpass1",
            "confirm": "newstaffpass1",
        })
        assert res.status_code == 400

        db.session.delete(invite)
        db.session.commit()

    def test_accept_invite_passwords_dont_match(self, client, church):
        invite, token = self._create_invite(church)
        res = client.post("/api/invite/accept", json={
            "token": token,
            "password": "newstaffpass1",
            "confirm": "differentpass1",
        })
        assert res.status_code == 400

        db.session.delete(invite)
        db.session.commit()
