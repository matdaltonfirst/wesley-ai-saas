"""Tests for the theology layer: one Wesleyan United Methodist profile plus
this congregation's own approved practice.

Asserted deterministically against assembled prompts, retrieval candidates and
citations, never against live model output.
"""

from unittest.mock import patch

import pytest

import denominations
from denominations import (
    PROFILE, LocalPracticeError, load_denomination_chunks,
    score_denomination_chunks, validate_local_practices,
    validate_statement_of_faith,
)
from helpers import build_system_prompt
from models import QnAPair, WidgetConversation, db


# ── The one profile ───────────────────────────────────────────────────────────

class TestSingleProfile:
    def test_the_profile_is_united_methodist_and_reviewed(self):
        assert PROFILE.key == "umc"
        assert PROFILE.display_name == "United Methodist Church"
        assert not PROFILE.awaiting_content
        assert len(PROFILE.sections) == len(load_denomination_chunks()) > 0

    def test_there_is_nothing_to_select(self):
        """Other denominations and the selector were removed, not hidden."""
        for gone in ("get_denomination_profile", "denomination_options",
                     "is_valid_denomination", "PROFILES", "church_profile"):
            assert not hasattr(denominations, gone), gone
        for module in ("sbc", "gmc", "non_denominational", "custom", "registry", "matrix"):
            with pytest.raises(ImportError):
                __import__("denominations." + module)

    def test_the_organization_has_no_denomination_column(self, church):
        assert not hasattr(church, "denomination")


class TestTheologyApi:
    def test_get_returns_the_profile_and_no_options(self, auth_client, church):
        data = auth_client.get("/api/church/theology").get_json()
        assert data["profile"]["key"] == "umc"
        assert "options" not in data and "denomination" not in data
        assert data["can_manage"] is True

    def test_the_denomination_endpoint_is_gone(self, auth_client, church):
        res = auth_client.post("/api/church/theology/denomination",
                               json={"denomination": "sbc", "confirm": True})
        assert res.status_code == 404

    def test_anonymous_cannot_read_or_write(self, client, church):
        assert client.get("/api/church/theology").status_code == 401
        assert client.post("/api/church/theology/local-practices", json={}).status_code == 401

    def test_staff_role_cannot_change_local_practices(self, client, church):
        from werkzeug.security import generate_password_hash
        from models import User
        db.session.add(User(email="staff@daltonfumc.com", role="staff",
                            password_hash=generate_password_hash("SecureTestPass1!", method="pbkdf2:sha256")))
        db.session.commit()
        client.post("/api/auth/login", json={
            "email": "staff@daltonfumc.com", "password": "SecureTestPass1!"})
        assert client.get("/api/church/theology").status_code == 200
        res = client.post("/api/church/theology/local-practices",
                          json={"local_practices": {"preferred_clergy_title": "Pastor"}})
        assert res.status_code == 403


# ── Prompts and retrieval ─────────────────────────────────────────────────────

def _staff_prompt(auth_client, question="What does the church teach?"):
    """Run staff chat and return the system instruction it assembled."""
    captured = {}

    def fake(q, context, history, system_instruction, **kwargs):
        captured["context"] = context
        captured["prompt"] = system_instruction
        return "Answer [1]."

    with patch("routes.chat.call_gemini", side_effect=fake):
        res = auth_client.post("/api/chat", json={"question": question})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    from models import Conversation
    conv = Conversation.query.get(data["conversation_id"])
    if conv:
        db.session.delete(conv)
        db.session.commit()
    captured["sources"] = data["sources"]
    return captured


def _widget_prompt(client, church, question="What does the church teach?",
                   answer="Answer [1]."):
    """Run public widget chat and return the system instruction it assembled."""
    captured = {}

    def fake(q, context, history, system_instruction, **kwargs):
        captured["context"] = context
        captured["prompt"] = system_instruction
        return answer

    with patch("routes.widget.call_gemini", side_effect=fake):
        res = client.post("/api/widget/chat", json={
            "church_id": church.id, "question": question,
        })
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    wconv = WidgetConversation.query.filter_by(
        session_id=data["session_id"]).first()
    if wconv:
        db.session.delete(wconv)
        db.session.commit()
    captured["sources"] = data["sources"]
    return captured


def _flat(text):
    """Collapse whitespace so assertions survive prompt line wrapping."""
    return " ".join((text or "").split())


class TestUmcPrompt:
    def test_umc_identity_and_current_facts_in_both_prompts(self, app, church):
        for kwargs in ({"widget": True}, {"staff": True}):
            prompt = build_system_prompt(**kwargs)
            assert "United Methodist Church" in prompt
            assert "2020/2024 Book of Discipline is the current one" in prompt
            assert "Never quote Book of Discipline paragraph numbers" in prompt
            assert 'claim that you will "learn,"' in prompt
            assert "Wesleyan-Arminian perspective" in prompt

    def test_public_and_staff_prompts_use_the_same_profile(self, app, church):
        staff = build_system_prompt(staff=True)
        widget = build_system_prompt(widget=True)
        assert PROFILE.prompt_block() in staff
        assert PROFILE.prompt_block() in widget

    def test_no_other_denomination_is_named_as_ours(self, app, church):
        prompt = _flat(build_system_prompt(widget=True)).lower()
        for other in ("southern baptist", "global methodist", "baptist faith and message"):
            assert other not in prompt

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

    def test_denomination_sources_still_produce_citations(self, client, church):
        captured = _widget_prompt(
            client, church,
            "Who is allowed to receive communion?",
            answer="United Methodists practice an open table [1].",
        )
        assert "open table" in captured["context"]
        denom_sources = [s for s in captured["sources"] if s["type"] == "denomination"]
        assert denom_sources
        assert denom_sources[0]["title"].startswith("United Methodist beliefs: ")
        assert denom_sources[0]["url"].startswith("https://")

    def test_every_evaluation_question_is_declared(self):
        assert len(PROFILE.evaluation_questions) >= 8


class TestAuthorityAndConflictRules:
    def test_authority_order_and_conflict_rules_in_both_prompts(self, app, church):
        for kwargs in ({"widget": True}, {"staff": True}):
            prompt = _flat(build_system_prompt(**kwargs))
            assert "--- Authority and Conflict Rules ---" in prompt
            assert "Pastor-approved local practice and approved Q&A" in prompt
            assert "The selected denominational profile below." in prompt
            assert "never rewrites objective denominational facts" in prompt
            assert "you must NOT say the denomination" in prompt
            assert "recommend contacting church leadership" in prompt
            assert "never blend positions" in prompt
            assert "Handling uncertainty in this profile:" in prompt

    def test_authority_order_ranks_local_above_denomination(self, app, church):
        prompt = build_system_prompt(widget=True)
        local_pos = prompt.index("Pastor-approved local practice and approved Q&A")
        denom_pos = prompt.index("The selected denominational profile below.")
        model_pos = prompt.index("Your own general knowledge")
        assert local_pos < denom_pos < model_pos

    def test_approved_qna_keeps_verbatim_precedence(self, app, church):
        pair = QnAPair(
            question="Do you baptize infants?",
            answer="Yes, talk to Pastor Dana to schedule one.",
            is_active=True,
        )
        db.session.add(pair)
        db.session.commit()
        for kwargs in ({"widget": True}, {"staff": True}):
            prompt = build_system_prompt(**kwargs)
            assert "--- Approved Q&A" in prompt
            assert "Yes, talk to Pastor Dana to schedule one." in prompt
            # Stated after the profile so it reads last; the authority order
            # says it wins.
            assert prompt.index("--- Denominational Profile:") < prompt.index("--- Approved Q&A")


# ── Local practice ────────────────────────────────────────────────────────────

class TestLocalPractices:
    def test_save_and_render_local_practices(self, auth_client, church):
        res = auth_client.post("/api/church/theology/local-practices", json={
            "local_practices": {
                "preferred_clergy_title": "Pastor",
                "communion_frequency": "First Sunday of each month",
                "marriage_inquiry_handling": (
                    "This congregation's pastor does not perform same-sex weddings."
                ),
                "pastor_referral_topics": ["Grief", "Divorce", ""],
            },
            "statement_of_faith": "",
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["local_practices"]["preferred_clergy_title"] == "Pastor"
        assert data["local_practices"]["pastor_referral_topics"] == ["Grief", "Divorce"]

        db.session.refresh(church)
        prompt = build_system_prompt(widget=True)
        assert "--- Approved Local Church Practice (pastor-approved) ---" in prompt
        assert "First Sunday of each month" in prompt
        assert "does not perform same-sex weddings" in prompt
        assert "It is not a statement of denominational teaching." in prompt
        assert "Topics to refer to a pastor: Grief; Divorce" in prompt

        church.local_practices = None
        db.session.commit()

    def test_statement_of_faith_is_local_teaching(self, auth_client, church):
        res = auth_client.post("/api/church/theology/local-practices", json={
            "statement_of_faith": "We believe the Bible is God's word.",
        })
        assert res.status_code == 200
        db.session.refresh(church)
        prompt = build_system_prompt(widget=True)
        assert "We believe the Bible is God's word." in prompt
        church.statement_of_faith = None




    @pytest.mark.parametrize("payload", [
        {"unknown_field": "x"},
        {"preferred_clergy_title": 12},
        {"preferred_clergy_title": "P" * 81},
        {"baptism_practice": "B" * 601},
        {"pastor_referral_topics": "not a list"},
        {"pastor_referral_topics": ["ok", 5]},
        {"pastor_referral_topics": ["x" * 121]},
        {"pastor_referral_topics": ["t"] * 21},
    ])
    def test_invalid_local_settings_rejected(self, auth_client, church, payload):
        res = auth_client.post("/api/church/theology/local-practices", json={
            "local_practices": payload,
        })
        assert res.status_code == 400
        assert res.get_json()["error"]
        db.session.refresh(church)
        assert church.local_practices in (None, "")

    def test_oversized_statement_of_faith_rejected(self, auth_client, church):
        res = auth_client.post("/api/church/theology/local-practices", json={
            "statement_of_faith": "x" * 6001,
        })
        assert res.status_code == 400
        db.session.refresh(church)
        assert church.statement_of_faith in (None, "")

    def test_non_object_local_practices_rejected(self, auth_client, church):
        res = auth_client.post("/api/church/theology/local-practices", json={
            "local_practices": ["communion_frequency"],
        })
        assert res.status_code == 400

    def test_validator_unit_behaviour(self):
        cleaned = validate_local_practices({
            "preferred_clergy_title": "  Pastor  ",
            "communion_frequency": "",
            "pastor_referral_topics": [" Grief ", ""],
        })
        assert cleaned == {
            "preferred_clergy_title": "Pastor",
            "pastor_referral_topics": ["Grief"],
        }
        assert validate_local_practices(None) == {}
        assert validate_statement_of_faith(None) == ""
        with pytest.raises(LocalPracticeError):
            validate_local_practices({"nope": "x"})
        with pytest.raises(LocalPracticeError):
            validate_statement_of_faith(42)



class TestTheologySettingsPanel:
    def test_dashboard_renders_theology_panel_for_admins(self, auth_client, church):
        res = auth_client.get("/dashboard")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert 'data-panel="theology"' in html
        assert 'id="panel-theology"' in html
        assert "United Methodist Profile" in html
        assert "/api/church/theology" in html
        assert "Approved Theological Q&amp;A" in html

    def test_the_dashboard_has_no_denomination_selector(self, auth_client, church):
        html = auth_client.get("/dashboard").get_data(as_text=True)
        assert "thDenomSelect" not in html
        assert "Denominational affiliation" not in html
