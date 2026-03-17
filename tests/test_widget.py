"""Tests for the public widget endpoints: branding and chat."""

from unittest.mock import patch

import pytest
from models import db, WidgetConversation, WidgetMessage


# ── Widget branding ───────────────────────────────────────────────────────────

class TestWidgetBranding:
    def test_missing_church_id(self, client):
        res = client.get("/api/widget/branding")
        assert res.status_code == 400
        assert "church_id" in res.get_json()["error"].lower()

    def test_invalid_church_id(self, client):
        res = client.get("/api/widget/branding?church_id=abc")
        assert res.status_code == 400

    def test_church_not_found(self, client):
        res = client.get("/api/widget/branding?church_id=999999")
        assert res.status_code == 404

    def test_returns_branding_for_valid_church(self, client, church):
        res = client.get(f"/api/widget/branding?church_id={church.id}")
        assert res.status_code == 200
        data = res.get_json()
        assert "bot_name" in data
        assert "welcome_message" in data
        assert "primary_color" in data
        assert "starter_questions" in data

    def test_cors_header_present(self, client, church):
        res = client.get(f"/api/widget/branding?church_id={church.id}")
        assert res.headers.get("Access-Control-Allow-Origin") == "*"

    def test_options_preflight(self, client):
        res = client.options("/api/widget/branding")
        assert res.status_code == 204
        assert res.headers.get("Access-Control-Allow-Origin") == "*"


# ── Widget chat ───────────────────────────────────────────────────────────────

class TestWidgetChat:
    def test_missing_church_id(self, client):
        res = client.post("/api/widget/chat", json={"question": "Hello?"})
        assert res.status_code == 400

    def test_missing_question(self, client, church):
        res = client.post("/api/widget/chat", json={"church_id": church.id})
        assert res.status_code == 400

    def test_invalid_church_id(self, client):
        res = client.post("/api/widget/chat", json={
            "church_id": "not-a-number",
            "question": "Hello?",
        })
        assert res.status_code == 400

    def test_church_not_found(self, client):
        res = client.post("/api/widget/chat", json={
            "church_id": 999999,
            "question": "Hello?",
        })
        assert res.status_code == 404

    def test_question_too_long(self, client, church):
        res = client.post("/api/widget/chat", json={
            "church_id": church.id,
            "question": "Q" * 2001,
        })
        assert res.status_code == 400
        assert "2,000" in res.get_json()["error"]

    def test_session_id_too_long(self, client, church):
        res = client.post("/api/widget/chat", json={
            "church_id": church.id,
            "question": "Hello?",
            "session_id": "x" * 65,
        })
        assert res.status_code == 400

    def test_chat_success_mocked(self, client, church):
        """Happy-path chat — Gemini is mocked to avoid real API calls."""
        with patch("routes.widget.call_gemini", return_value="Hello! How can I help?"):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "What time is Sunday service?",
            })
        assert res.status_code == 200
        data = res.get_json()
        assert data["answer"] == "Hello! How can I help?"
        assert "session_id" in data
        assert res.headers.get("Access-Control-Allow-Origin") == "*"

        # Cleanup the widget conversation created during this test
        wconv = WidgetConversation.query.filter_by(
            church_id=church.id, session_id=data["session_id"]
        ).first()
        if wconv:
            db.session.delete(wconv)
            db.session.commit()

    def test_chat_continues_existing_session(self, client, church):
        """Subsequent messages with the same session_id reuse the conversation."""
        with patch("routes.widget.call_gemini", return_value="First response."):
            res1 = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "First question",
            })
        assert res1.status_code == 200
        session_id = res1.get_json()["session_id"]

        with patch("routes.widget.call_gemini", return_value="Second response."):
            res2 = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "Second question",
                "session_id": session_id,
            })
        assert res2.status_code == 200
        assert res2.get_json()["session_id"] == session_id

        # The conversation should have 4 messages (2 user + 2 assistant)
        wconv = WidgetConversation.query.filter_by(
            church_id=church.id, session_id=session_id
        ).first()
        assert wconv is not None
        assert len(wconv.messages) == 4

        db.session.delete(wconv)
        db.session.commit()

    def test_chat_gemini_error_returns_clean_message(self, client, church):
        """An exception from Gemini should return a user-friendly error, not a 500."""
        with patch("routes.widget.call_gemini", side_effect=Exception("429 quota exceeded")):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "Will this blow up?",
            })
        assert res.status_code == 429
        assert "limit" in res.get_json()["error"].lower()

    def test_options_preflight(self, client):
        res = client.options("/api/widget/chat")
        assert res.status_code == 204
        assert res.headers.get("Access-Control-Allow-Origin") == "*"


# ── Authenticated widget conversation list ────────────────────────────────────

class TestWidgetConversationList:
    def test_list_requires_auth(self, client):
        res = client.get("/api/widget/conversations")
        assert res.status_code == 401

    def test_list_authenticated_empty(self, auth_client):
        res = auth_client.get("/api/widget/conversations")
        assert res.status_code == 200
        assert res.get_json()["conversations"] == []

    def test_list_shows_own_church_conversations(self, auth_client, church):
        wconv = WidgetConversation(church_id=church.id, session_id="test-session-abc")
        db.session.add(wconv)
        db.session.commit()

        res = auth_client.get("/api/widget/conversations")
        assert res.status_code == 200
        ids = [c["session_id"] for c in res.get_json()["conversations"]]
        assert "test-session-abc" in ids

        db.session.delete(wconv)
        db.session.commit()
