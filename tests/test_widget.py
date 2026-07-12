"""Tests for the public widget endpoints: branding and chat."""

from unittest.mock import patch

import pytest
from models import db, WidgetConversation, WidgetMessage, QnAPair, AnswerFeedback


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
        assert isinstance(data["message_id"], int)
        assert res.headers.get("Access-Control-Allow-Origin") == "*"

        # Cleanup the widget conversation created during this test
        wconv = WidgetConversation.query.filter_by(
            church_id=church.id, session_id=data["session_id"]
        ).first()
        if wconv:
            db.session.delete(wconv)
            db.session.commit()

    def test_chat_returns_public_website_and_document_citations(self, client, church):
        public_docs = [{
            "content": "Children meet at 9 AM on Sundays.",
            "source": "Children's Ministry Guide.pdf",
            "location": "Page 4",
        }]
        web_pages = [{
            "content": "Sunday children programming begins at 9 AM.",
            "source": "Children's Ministry",
            "location": "https://grace.example/children",
        }]
        with patch("routes.widget.load_chatbot_documents", return_value=public_docs), \
             patch("routes.widget.load_curated_content", return_value=[]), \
             patch("routes.widget.load_church_web_content", return_value=web_pages), \
             patch("routes.widget.call_gemini", return_value="Children meet at 9 AM. [1][2]"):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "When do children meet on Sunday?",
            })

        assert res.status_code == 200
        data = res.get_json()
        assert data["sources"][0]["title"] == "Children's Ministry Guide.pdf"
        assert data["sources"][0]["type"] == "document"
        assert data["sources"][1] == {
            "title": "Children's Ministry",
            "location": "Website",
            "url": "https://grace.example/children",
            "type": "website",
        }

        wconv = WidgetConversation.query.filter_by(session_id=data["session_id"]).first()
        assistant = next(m for m in wconv.messages if m.role == "assistant")
        assert "Children's Ministry" in assistant.sources
        db.session.delete(wconv)
        db.session.commit()

    def test_chat_cites_staff_approved_qna(self, client, church):
        pair = QnAPair(
            church_id=church.id,
            question="What time is worship?",
            answer="Worship begins at 10 AM.",
        )
        db.session.add(pair)
        db.session.commit()

        with patch("routes.widget.load_chatbot_documents", return_value=[]), \
             patch("routes.widget.load_church_web_content", return_value=[]), \
             patch("routes.widget.call_gemini", return_value="Worship begins at 10 AM. [1]"):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "What time is worship?",
            })

        data = res.get_json()
        assert data["sources"] == [{
            "title": "Approved church answer",
            "location": "What time is worship?",
            "url": None,
            "type": "approved_answer",
        }]

        wconv = WidgetConversation.query.filter_by(session_id=data["session_id"]).first()
        db.session.delete(wconv)
        db.session.delete(pair)
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


# ── Answer feedback ───────────────────────────────────────────────────────────

class TestAnswerFeedback:
    def _create_answer(self, client, church):
        with patch("routes.widget.call_gemini", return_value="The office is open Friday."):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "Is the office open Friday?",
            })
        assert res.status_code == 200
        return res.get_json()

    def test_submit_not_helpful_feedback(self, client, church):
        answer = self._create_answer(client, church)
        res = client.post("/api/widget/feedback", json={
            "church_id": church.id,
            "session_id": answer["session_id"],
            "message_id": answer["message_id"],
            "rating": "not_helpful",
            "reason": "incorrect",
        })

        assert res.status_code == 201
        feedback = AnswerFeedback.query.filter_by(widget_message_id=answer["message_id"]).one()
        assert feedback.status == "open"
        assert feedback.reason == "incorrect"

        db.session.delete(feedback.widget_message.widget_conversation)
        db.session.commit()

    def test_feedback_requires_matching_session(self, client, church):
        answer = self._create_answer(client, church)
        res = client.post("/api/widget/feedback", json={
            "church_id": church.id,
            "session_id": "wrong-session",
            "message_id": answer["message_id"],
            "rating": "not_helpful",
            "reason": "incorrect",
        })

        assert res.status_code == 404
        assert AnswerFeedback.query.filter_by(widget_message_id=answer["message_id"]).first() is None

        wconv = WidgetConversation.query.filter_by(session_id=answer["session_id"]).one()
        db.session.delete(wconv)
        db.session.commit()

    def test_helpful_feedback_counts_but_stays_out_of_dismissed_queue(self, auth_client, church):
        answer = self._create_answer(auth_client, church)
        res = auth_client.post("/api/widget/feedback", json={
            "church_id": church.id,
            "session_id": answer["session_id"],
            "message_id": answer["message_id"],
            "rating": "helpful",
        })
        assert res.status_code == 201

        inbox = auth_client.get("/api/feedback?status=dismissed").get_json()
        assert inbox["stats"]["helpful"] == 1
        assert inbox["items"] == []

        wconv = WidgetConversation.query.filter_by(session_id=answer["session_id"]).one()
        db.session.delete(wconv)
        db.session.commit()

    def test_feedback_list_requires_auth(self, client):
        assert client.get("/api/feedback").status_code == 401

    def test_staff_can_publish_feedback_correction_to_qna(self, auth_client, church):
        answer = self._create_answer(auth_client, church)
        submitted = auth_client.post("/api/widget/feedback", json={
            "church_id": church.id,
            "session_id": answer["session_id"],
            "message_id": answer["message_id"],
            "rating": "not_helpful",
            "reason": "outdated",
        })
        assert submitted.status_code == 201

        inbox = auth_client.get("/api/feedback?status=open")
        assert inbox.status_code == 200
        item = inbox.get_json()["items"][0]
        assert item["question"] == "Is the office open Friday?"
        assert item["answer"] == "The office is open Friday."

        corrected = auth_client.post(f"/api/feedback/{item['id']}/correct", json={
            "question": "Is the church office open Friday?",
            "answer": "The church office is closed on Fridays.",
        })
        assert corrected.status_code == 200
        pair_id = corrected.get_json()["pair"]["id"]
        pair = QnAPair.query.get(pair_id)
        assert pair.is_active is True
        assert pair.answer == "The church office is closed on Fridays."

        feedback = AnswerFeedback.query.get(item["id"])
        assert feedback.status == "corrected"
        assert feedback.qna_pair_id == pair.id

        wconv = WidgetConversation.query.filter_by(session_id=answer["session_id"]).one()
        db.session.delete(feedback)
        db.session.delete(pair)
        db.session.delete(wconv)
        db.session.commit()


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


class TestAutoFlaggedFeedback:
    def _chat(self, client, church, reply):
        with patch("routes.widget.call_gemini", return_value=reply):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "When is the fall festival?",
            })
        assert res.status_code == 200
        return res.get_json()

    def _cleanup(self, answer):
        wconv = WidgetConversation.query.filter_by(session_id=answer["session_id"]).one()
        db.session.delete(wconv)
        db.session.commit()

    def test_low_confidence_answer_is_auto_flagged(self, client, church):
        answer = self._chat(client, church,
                            "I don't have information about the fall festival.")
        feedback = AnswerFeedback.query.filter_by(
            widget_message_id=answer["message_id"]).one()
        assert feedback.rating == "auto_flagged"
        assert feedback.status == "open"
        assert feedback.church_id == church.id
        self._cleanup(answer)

    def test_phrasing_variations_are_auto_flagged(self, client, church):
        answer = self._chat(client, church,
                            "I don't have that specific information about the WiFi password.")
        feedback = AnswerFeedback.query.filter_by(
            widget_message_id=answer["message_id"]).one()
        assert feedback.rating == "auto_flagged"
        assert feedback.status == "open"
        assert feedback.church_id == church.id
        self._cleanup(answer)

    def test_confident_answer_is_not_flagged(self, client, church):
        answer = self._chat(client, church,
                            "The fall festival is October 12 at 5pm on the lawn.")
        assert AnswerFeedback.query.filter_by(
            widget_message_id=answer["message_id"]).first() is None
        self._cleanup(answer)

    def test_visitor_feedback_upgrades_auto_flag_in_place(self, client, church):
        answer = self._chat(client, church, "I'm not sure about that event.")
        res = client.post("/api/widget/feedback", json={
            "church_id": church.id,
            "session_id": answer["session_id"],
            "message_id": answer["message_id"],
            "rating": "not_helpful",
            "reason": "incomplete",
        })
        assert res.status_code == 201
        rows = AnswerFeedback.query.filter_by(
            widget_message_id=answer["message_id"]).all()
        assert len(rows) == 1
        assert rows[0].rating == "not_helpful"
        assert rows[0].reason == "incomplete"
        assert rows[0].status == "open"
        self._cleanup(answer)

    def test_auto_flagged_appears_in_open_inbox(self, auth_client, church):
        answer = self._chat(auth_client, church,
                            "I don't know the answer to that one.")
        res = auth_client.get("/api/feedback?status=open")
        assert res.status_code == 200
        data = res.get_json()
        flagged = [i for i in data["items"]
                   if i["answer"] == "I don't know the answer to that one."]
        assert len(flagged) == 1
        assert flagged[0]["rating"] == "auto_flagged"
        assert flagged[0]["question"] == "When is the fall festival?"
        self._cleanup(answer)


class TestMultiLanguage:
    def test_widget_prompt_instructs_language_matching(self, app, church):
        from helpers import build_system_prompt
        prompt = build_system_prompt(church, widget=True)
        assert "language the visitor writes in" in prompt

    def test_staff_prompt_unchanged(self, app, church):
        from helpers import build_system_prompt
        prompt = build_system_prompt(church, staff=True)
        assert "language the visitor writes in" not in prompt
