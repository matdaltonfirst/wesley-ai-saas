"""Tests for the staff chat API: /api/chat, /api/conversations, /api/conversations/<id>/messages."""

from unittest.mock import patch

import pytest
from models import db, Conversation, Message


# ── /api/chat ─────────────────────────────────────────────────────────────────

class TestChat:
    def test_chat_requires_auth(self, client):
        res = client.post("/api/chat", json={"question": "Hello?"})
        assert res.status_code == 401

    def test_chat_missing_question(self, auth_client):
        res = auth_client.post("/api/chat", json={})
        assert res.status_code == 400
        assert "question" in res.get_json()["error"].lower()

    def test_chat_empty_question(self, auth_client):
        res = auth_client.post("/api/chat", json={"question": "   "})
        assert res.status_code == 400

    def test_chat_question_too_long(self, auth_client):
        res = auth_client.post("/api/chat", json={"question": "Q" * 2001})
        assert res.status_code == 400
        assert "2,000" in res.get_json()["error"]

    def test_chat_creates_new_conversation(self, auth_client, church):
        with patch("routes.chat.call_gemini", return_value="Hello from Wesley!"):
            res = auth_client.post("/api/chat", json={"question": "What time is service?"})

        assert res.status_code == 200
        data = res.get_json()
        assert data["answer"] == "Hello from Wesley!"
        assert "conversation_id" in data
        assert isinstance(data["conversation_id"], int)
        assert "sources" in data

        # Cleanup
        conv = Conversation.query.get(data["conversation_id"])
        if conv:
            db.session.delete(conv)
            db.session.commit()

    def test_chat_continues_existing_conversation(self, auth_client, church):
        """Sending conversation_id appends to the existing conversation."""
        # Create conversation via first message
        with patch("routes.chat.call_gemini", return_value="First answer."):
            res1 = auth_client.post("/api/chat", json={"question": "First question"})
        assert res1.status_code == 200
        conv_id = res1.get_json()["conversation_id"]

        with patch("routes.chat.call_gemini", return_value="Second answer."):
            res2 = auth_client.post("/api/chat", json={
                "question": "Second question",
                "conversation_id": conv_id,
            })
        assert res2.status_code == 200
        assert res2.get_json()["conversation_id"] == conv_id

        # Conversation should now have 4 messages (2 user + 2 assistant)
        conv = Conversation.query.get(conv_id)
        assert len(conv.messages) == 4

        db.session.delete(conv)
        db.session.commit()

    def test_chat_conversation_not_found(self, auth_client):
        res = auth_client.post("/api/chat", json={
            "question": "Hello?",
            "conversation_id": 999999,
        })
        assert res.status_code == 404
        assert "not found" in res.get_json()["error"].lower()

    def test_chat_gemini_rate_limit_error(self, auth_client):
        with patch("routes.chat.call_gemini", side_effect=Exception("429 quota exceeded")):
            res = auth_client.post("/api/chat", json={"question": "Will this blow up?"})
        assert res.status_code == 429
        assert "limit" in res.get_json()["error"].lower()

    def test_chat_gemini_unavailable_error(self, auth_client):
        with patch("routes.chat.call_gemini", side_effect=Exception("503 service unavailable")):
            res = auth_client.post("/api/chat", json={"question": "Is Gemini down?"})
        assert res.status_code == 503

    def test_chat_no_body(self, auth_client):
        res = auth_client.post("/api/chat", data="not json", content_type="text/plain")
        assert res.status_code == 400

    def test_chat_returns_sources_list(self, auth_client, church):
        """The sources key is always present in a successful response."""
        with patch("routes.chat.call_gemini", return_value="The answer is 42."):
            res = auth_client.post("/api/chat", json={"question": "What is the answer?"})
        assert res.status_code == 200
        data = res.get_json()
        assert isinstance(data["sources"], list)

        conv = Conversation.query.get(data["conversation_id"])
        if conv:
            db.session.delete(conv)
            db.session.commit()


# ── /api/conversations ────────────────────────────────────────────────────────

class TestListConversations:
    def test_list_requires_auth(self, client):
        res = client.get("/api/conversations")
        assert res.status_code == 401

    def test_list_empty(self, auth_client):
        res = auth_client.get("/api/conversations")
        assert res.status_code == 200
        assert res.get_json()["conversations"] == []

    def test_list_shows_own_conversations(self, auth_client, church):
        conv = Conversation(church_id=church.id, title="Sunday Service")
        db.session.add(conv)
        db.session.commit()

        res = auth_client.get("/api/conversations")
        assert res.status_code == 200
        titles = [c["title"] for c in res.get_json()["conversations"]]
        assert "Sunday Service" in titles

        db.session.delete(conv)
        db.session.commit()

    def test_list_response_shape(self, auth_client, church):
        conv = Conversation(church_id=church.id, title="Test Conv")
        db.session.add(conv)
        db.session.commit()

        res = auth_client.get("/api/conversations")
        item = res.get_json()["conversations"][0]
        assert "id" in item
        assert "title" in item
        assert "updated_at" in item

        db.session.delete(conv)
        db.session.commit()


# ── /api/conversations/<id>/messages ─────────────────────────────────────────

class TestGetConversationMessages:
    def test_get_messages_requires_auth(self, client, church):
        conv = Conversation(church_id=church.id, title="Temp")
        db.session.add(conv)
        db.session.commit()

        res = client.get(f"/api/conversations/{conv.id}/messages")
        assert res.status_code == 401

        db.session.delete(conv)
        db.session.commit()

    def test_get_messages_not_found(self, auth_client):
        res = auth_client.get("/api/conversations/999999/messages")
        assert res.status_code == 404

    def test_get_messages_returns_correct_shape(self, auth_client, church):
        conv = Conversation(church_id=church.id, title="Shape Test")
        db.session.add(conv)
        db.session.flush()
        db.session.add(Message(conversation_id=conv.id, role="user", content="Hello"))
        db.session.add(Message(conversation_id=conv.id, role="assistant", content="Hi!"))
        db.session.commit()

        res = auth_client.get(f"/api/conversations/{conv.id}/messages")
        assert res.status_code == 200
        data = res.get_json()
        assert data["conversation_id"] == conv.id
        assert data["title"] == "Shape Test"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"
        assert "created_at" in data["messages"][0]

        db.session.delete(conv)
        db.session.commit()
