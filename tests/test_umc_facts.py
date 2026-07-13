"""Tests for the platform UMC denominational knowledge layer."""

from unittest.mock import patch

from models import db, WidgetConversation
from umc_facts import load_denomination_chunks, score_denomination_chunks, SECTIONS


class TestDenominationChunks:
    def test_all_sections_become_chunks(self):
        chunks = load_denomination_chunks()
        assert len(chunks) == len(SECTIONS)
        assert all(c["type"] == "denomination" for c in chunks)
        assert all(c["location"].startswith("https://") for c in chunks)

    def test_doctrine_questions_retrieve_right_sections(self):
        cases = [
            ("What is your stance on homosexuality?", "Marriage and human sexuality"),
            ("Who can take communion at your church?", "Holy Communion"),
            ("Do you baptize infants?", "Baptism"),
            ("Can women be pastors in your church?", "Clergy and ordination"),
        ]
        for question, expected in cases:
            scored = score_denomination_chunks(question)
            assert scored, question
            titles = " | ".join(c["source"] for _, c in scored)
            assert expected in titles, f"{question} -> {titles}"

    def test_widget_chat_cites_denomination_source(self, client, church):
        captured = {}

        def fake_gemini(question, context, history, system_instruction):
            captured["context"] = context
            return "United Methodists practice an open table [1]."

        with patch("routes.widget.call_gemini", side_effect=fake_gemini):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "Who is allowed to receive communion?",
            })
        assert res.status_code == 200
        data = res.get_json()
        assert "open table" in captured["context"]
        assert any(s["type"] == "denomination" for s in data["sources"])

        wconv = WidgetConversation.query.filter_by(
            church_id=church.id, session_id=data["session_id"]).first()
        db.session.delete(wconv)
        db.session.commit()
