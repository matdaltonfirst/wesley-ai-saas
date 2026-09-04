"""Tests for billing enforcement and rate limits on the endpoints that cost money.

These endpoints previously had no gate at all: billing lapses only blocked the
dashboard pages, while AI chat, the public widget, guest submissions, and the
auth endpoints stayed open. Each test below pins one of those holes shut.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from models import (
    db, Church, Conversation, GuestConnection, Message, User,
    WidgetConversation, WidgetMessage,
)


@pytest.fixture(autouse=True)
def _clean_conversations():
    """Remove conversations these tests commit for real.

    The church fixture tears down with a bulk delete, which bypasses the ORM
    cascade, so rows would otherwise survive into later modules that assert on
    conversation counts. Autouse fixtures are module-scoped in effect, so this
    only touches rows this file created.
    """
    yield
    Message.query.delete()
    WidgetMessage.query.delete()
    Conversation.query.delete()
    WidgetConversation.query.delete()
    db.session.commit()


def _lapse(church):
    """Put *church* fully out of billing: trial over, no subscription, no exemption."""
    church.billing_exempt = False
    church.trial_ends_at = datetime.utcnow() - timedelta(days=1)
    church.stripe_subscription_id = None
    church.manual_payment_active = False
    db.session.commit()


# ── Staff chat ────────────────────────────────────────────────────────────────

class TestStaffChatBillingGate:
    def test_lapsed_church_cannot_use_staff_chat(self, auth_client, church):
        _lapse(church)
        with patch("routes.chat.call_gemini") as gemini:
            res = auth_client.post("/api/chat", json={"question": "Draft a bulletin"})
        assert res.status_code == 402
        gemini.assert_not_called()

    def test_active_church_can_use_staff_chat(self, auth_client, church):
        with patch("routes.chat.call_gemini", return_value="Here you go."):
            res = auth_client.post("/api/chat", json={"question": "Draft a bulletin"})
        assert res.status_code == 200


# ── Public widget ─────────────────────────────────────────────────────────────

class TestWidgetBillingGate:
    def test_lapsed_church_widget_stops_answering(self, client, church):
        _lapse(church)
        with patch("routes.widget.call_gemini") as gemini:
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "What time is worship?",
            })
        assert res.status_code == 402
        gemini.assert_not_called()

    def test_gate_response_keeps_cors_header(self, client, church):
        """The widget renders the error inline, so it still needs the CORS header."""
        _lapse(church)
        res = client.post("/api/widget/chat", json={
            "church_id": church.id,
            "question": "What time is worship?",
        })
        assert res.headers.get("Access-Control-Allow-Origin") == "*"

    def test_gate_message_does_not_blame_the_church(self, client, church):
        """Visitors see this text — it must not disclose a billing problem."""
        _lapse(church)
        res = client.post("/api/widget/chat", json={
            "church_id": church.id,
            "question": "What time is worship?",
        })
        message = res.get_json()["error"].lower()
        for leak in ("billing", "subscription", "payment", "expired", "unpaid"):
            assert leak not in message

    def test_domain_exempt_church_widget_still_works(self, client, church):
        """A church exempt only through its staff email domain has no signed-in
        user on the widget path — it must not be gated by the fallback."""
        _lapse(church)
        db.session.add(User(
            email="pastor@wesleyai.co",
            password_hash=generate_password_hash("x", method="pbkdf2:sha256"),
            church_id=church.id,
            role="admin",
        ))
        db.session.commit()
        with patch("routes.widget.call_gemini", return_value="Worship is at 10 AM."):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "What time is worship?",
            })
        assert res.status_code == 200


# ── Guest connections ─────────────────────────────────────────────────────────

class TestGuestConnectionRateLimit:
    def test_submissions_are_capped_per_ip(self, app, client, church):
        """This endpoint writes to Planning Center and emails every admin, so an
        unmetered flood would pollute the church's system of record."""
        from app import _RateLimiter

        original = app.config["GUEST_LIMITER"]
        app.config["GUEST_LIMITER"] = _RateLimiter(max_requests=2, window_seconds=3600)
        try:
            payload = {"church_id": church.id, "name": "Ann", "email": "ann@example.com"}
            assert client.post("/api/guest-connection", json=payload).status_code == 201
            assert client.post("/api/guest-connection", json=payload).status_code == 201
            blocked = client.post("/api/guest-connection", json=payload)
            assert blocked.status_code == 429
            assert blocked.headers.get("Access-Control-Allow-Origin") == "*"
        finally:
            app.config["GUEST_LIMITER"] = original
            GuestConnection.query.filter_by(church_id=church.id).delete()
            db.session.commit()


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestAuthRateLimit:
    def test_password_guessing_is_capped(self, app, client, admin_user):
        from app import _RateLimiter

        original = app.config["AUTH_LIMITER"]
        app.config["AUTH_LIMITER"] = _RateLimiter(max_requests=3, window_seconds=900)
        try:
            attempt = {"email": admin_user.email, "password": "wrong-password"}
            for _ in range(3):
                assert client.post("/api/auth/login", json=attempt).status_code == 401
            assert client.post("/api/auth/login", json=attempt).status_code == 429
        finally:
            app.config["AUTH_LIMITER"] = original

    def test_reset_email_flooding_is_capped(self, app, client, admin_user):
        from app import _RateLimiter

        original = app.config["AUTH_LIMITER"]
        app.config["AUTH_LIMITER"] = _RateLimiter(max_requests=2, window_seconds=900)
        try:
            payload = {"email": admin_user.email}
            for _ in range(2):
                assert client.post("/api/auth/forgot-password", json=payload).status_code == 200
            assert client.post("/api/auth/forgot-password", json=payload).status_code == 429
        finally:
            app.config["AUTH_LIMITER"] = original
