"""Tests for auth routes: login, logout, forgot/reset password, invite."""

import secrets
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

from models import AuditLog, User, db


# ── Signup is gone ────────────────────────────────────────────────────────────

class TestNoSignup:
    def test_signup_route_does_not_exist(self, client):
        res = client.post("/api/auth/signup", json={
            "email": "newchurch@daltonfumc.com", "password": "strongpass1",
            "church_name": "New Life Church"})
        assert res.status_code == 404
        assert client.get("/signup").status_code == 404


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

    def test_login_rejects_an_email_outside_the_church_domain(self, client, church):
        user = User(email="someone@example.com",
                    password_hash=generate_password_hash("SecureTestPass1!", method="pbkdf2:sha256"))
        db.session.add(user); db.session.commit()
        res = client.post("/api/auth/login", json={
            "email": "someone@example.com", "password": "SecureTestPass1!"})
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


# ── Google Workspace sign-in ──────────────────────────────────────────────────

import secrets
from unittest.mock import patch, MagicMock

import routes.auth as auth_routes
from tests.conftest import make_user


@pytest.fixture
def google(app, church, monkeypatch):
    """Turn Google sign-in on for a test and return a helper to run the flow."""
    monkeypatch.setattr(auth_routes, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_routes, "GOOGLE_CLIENT_SECRET", "client-secret")

    class Flow:
        def start(self, client):
            res = client.get("/auth/google")
            assert res.status_code == 302
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(res.headers["Location"]).query)
            return q["state"][0], q["nonce"][0], q

        def finish(self, client, state, claims, code="abc"):
            token_resp = MagicMock(status_code=200)
            token_resp.json.return_value = {"id_token": "signed"}
            token_resp.raise_for_status.return_value = None
            with patch.object(auth_routes.requests, "post", return_value=token_resp), \
                 patch.object(auth_routes, "_verify_id_token", return_value=claims):
                return client.get(f"/auth/google/callback?state={state}&code={code}")

        def claims(self, nonce_value, **over):
            base = {"sub": "g-123", "email": "carey@daltonfumc.com", "email_verified": True,
                    "hd": "daltonfumc.com", "name": "Carrie Ashcraft", "nonce": nonce_value}
            base.update(over)
            return base
    return Flow()


class TestGoogleSignIn:
    def test_start_redirects_to_google_with_state_nonce_and_domain_hint(self, client, google):
        state, nonce, q = google.start(client)
        assert q["client_id"] == ["client-id"] and q["hd"] == ["daltonfumc.com"]
        assert q["scope"] == ["openid email profile"] and state and nonce

    def test_a_known_person_signs_in_and_their_google_id_is_recorded(self, client, google):
        user = make_user("carey@daltonfumc.com", ["admin_assistant"])
        state, nonce, _ = google.start(client)
        res = google.finish(client, state, google.claims(nonce))
        assert res.status_code == 302 and res.headers["Location"].endswith("/home")
        db.session.refresh(user)
        assert user.google_sub == "g-123" and user.display_name == "Carrie Ashcraft"
        assert user.last_login_at is not None
        assert client.get("/api/conversations").status_code == 200
        assert AuditLog.query.filter_by(action="auth.login").count() == 1

    @pytest.mark.parametrize("override,why", [
        ({"email_verified": False}, "email not verified"),
        ({"email": "carey@gmail.com"}, "outside the church domain"),
        ({"hd": "gmail.com"}, "not a Workspace account"),
        ({"hd": None}, "not a Workspace account"),
        ({"email": "carey@daltonfumc.com.evil.com", "hd": "daltonfumc.com"}, "outside the church domain"),
        ({"nonce": "not-the-nonce"}, "nonce mismatch"),
        ({"sub": ""}, "no subject"),
    ])
    def test_a_token_that_fails_any_server_check_is_refused(self, client, google, override, why):
        make_user("carey@daltonfumc.com", ["admin_assistant"])
        state, nonce, _ = google.start(client)
        res = google.finish(client, state, google.claims(nonce, **override))
        assert res.status_code == 302 and "error=denied" in res.headers["Location"]
        assert client.get("/api/conversations").status_code == 401
        entry = AuditLog.query.filter_by(action="auth.denied").first()
        assert entry and why in entry.source

    def test_the_domain_comes_from_the_token_not_the_browser(self, client, google):
        """A hostile hd query parameter changes nothing: only verified claims count."""
        make_user("carey@daltonfumc.com", ["admin_assistant"])
        state, nonce, _ = google.start(client)
        res = google.finish(client, state, google.claims(nonce, email="evil@gmail.com", hd="gmail.com"))
        assert "error=denied" in res.headers["Location"]

    def test_a_wrong_state_is_refused(self, client, google):
        make_user("carey@daltonfumc.com", ["admin_assistant"])
        google.start(client)
        res = client.get("/auth/google/callback?state=forged&code=x")
        assert "error=denied" in res.headers["Location"]

    def test_a_church_google_account_without_an_account_here_is_refused(self, client, google):
        state, nonce, _ = google.start(client)
        res = google.finish(client, state, google.claims(nonce, email="stranger@daltonfumc.com", sub="g-9"))
        assert "error=denied" in res.headers["Location"]

    def test_a_deactivated_person_cannot_sign_in(self, client, google):
        make_user("carey@daltonfumc.com", ["admin_assistant"], active=False)
        state, nonce, _ = google.start(client)
        assert "error=denied" in google.finish(client, state, google.claims(nonce)).headers["Location"]

    def test_a_changed_google_account_for_the_same_email_is_refused(self, client, google):
        user = make_user("carey@daltonfumc.com", ["admin_assistant"])
        user.google_sub = "g-original"; db.session.commit()
        state, nonce, _ = google.start(client)
        res = google.finish(client, state, google.claims(nonce, sub="g-someone-else"))
        assert "error=denied" in res.headers["Location"]

    def test_the_same_state_cannot_be_replayed(self, client, google):
        make_user("carey@daltonfumc.com", ["admin_assistant"])
        state, nonce, _ = google.start(client)
        google.finish(client, state, google.claims(nonce))
        client.get("/logout")
        res = google.finish(client, state, google.claims(nonce))
        assert "error=denied" in res.headers["Location"]

    def test_password_sign_in_is_switched_off_once_google_is_configured(self, client, google):
        make_user("carey@daltonfumc.com", ["admin_assistant"])
        res = client.post("/api/auth/login", json={"email": "carey@daltonfumc.com", "password": "SecureTestPass1!"})
        assert res.status_code == 404
        assert client.get("/forgot-password").status_code == 302

    def test_the_login_page_offers_google_and_no_password_form(self, client, google):
        html = client.get("/login").get_data(as_text=True)
        assert "Sign in with Google" in html and 'id="loginBtn"' not in html


class TestSessions:
    def test_signing_out_ends_the_session_and_is_logged(self, client, church):
        user = make_user("a@daltonfumc.com", ["admin"])
        client.post("/api/auth/login", json={"email": user.email, "password": user._plaintext_password})
        client.get("/logout")
        assert client.get("/api/conversations").status_code == 401
        assert AuditLog.query.filter_by(action="auth.logout").count() == 1

    def test_a_session_older_than_the_absolute_cap_is_ended(self, app, client, church):
        user = make_user("a@daltonfumc.com", ["admin"])
        client.post("/api/auth/login", json={"email": user.email, "password": user._plaintext_password})
        app.config["TESTING"] = False
        try:
            with client.session_transaction() as s:
                s["login_at"] = (datetime.utcnow() - timedelta(hours=25)).isoformat()
            res = client.get("/api/conversations")
        finally:
            app.config["TESTING"] = True
        assert res.status_code == 401

    def test_deactivating_someone_ends_their_session(self, client, church):
        user = make_user("a@daltonfumc.com", ["admin"])
        client.post("/api/auth/login", json={"email": user.email, "password": user._plaintext_password})
        user.active = False; db.session.commit()
        assert client.get("/api/conversations").status_code == 401


class TestCsrf:
    def test_a_state_changing_request_without_the_token_is_refused(self, app, client, church):
        user = make_user("a@daltonfumc.com", ["admin"])
        client.post("/api/auth/login", json={"email": user.email, "password": user._plaintext_password})
        app.config["FORCE_CSRF"] = True
        try:
            res = client.post("/api/qna", json={"question": "q", "answer": "a"})
            assert res.status_code == 403 and "CSRF" in res.get_json()["error"]
            with client.session_transaction() as s:
                token = s["csrf_token"]
            ok = client.post("/api/qna", json={"question": "q", "answer": "a"}, headers={"X-CSRFToken": token})
            assert ok.status_code == 201
            bad = client.post("/api/qna", json={"question": "q", "answer": "a"}, headers={"X-CSRFToken": "wrong"})
            assert bad.status_code == 403
        finally:
            app.config["FORCE_CSRF"] = False

    def test_reads_do_not_need_a_token(self, app, client, church):
        user = make_user("a@daltonfumc.com", ["admin"])
        client.post("/api/auth/login", json={"email": user.email, "password": user._plaintext_password})
        app.config["FORCE_CSRF"] = True
        try:
            assert client.get("/api/qna").status_code == 200
        finally:
            app.config["FORCE_CSRF"] = False
