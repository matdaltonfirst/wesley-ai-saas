"""Tests for the multi-denominational theology architecture.

Isolation is asserted deterministically — against assembled prompts, retrieval
candidates, and citations — never against live model output.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from denominations import (
    DEFAULT_DENOMINATION, ISOLATION_QUESTIONS, PROFILES, LocalPracticeError,
    church_profile, denomination_options, foreign_markers,
    get_denomination_profile, is_valid_denomination, load_denomination_chunks,
    score_denomination_chunks, validate_local_practices,
    validate_statement_of_faith,
)
from helpers import build_system_prompt
from models import Church, QnAPair, User, WidgetConversation, db

ALL_KEYS = ("umc", "sbc", "gmc", "non_denominational", "custom")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_denomination(church, key):
    church.denomination = key
    church.denomination_profile_version = get_denomination_profile(key).version
    db.session.commit()


def _make_church(name, denomination=None, **kwargs):
    """A second tenant, created directly so cross-tenant leakage can be tested."""
    c = Church(
        name=name,
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
        billing_exempt=True,
        **kwargs,
    )
    if denomination:
        c.denomination = denomination
    db.session.add(c)
    db.session.commit()
    db.session.refresh(c)
    return c


def _drop_church(c):
    QnAPair.query.filter_by(church_id=c.id).delete()
    User.query.filter_by(church_id=c.id).delete()
    Church.query.filter_by(id=c.id).delete()
    db.session.commit()


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
        church_id=church.id, session_id=data["session_id"]).first()
    if wconv:
        db.session.delete(wconv)
        db.session.commit()
    captured["sources"] = data["sources"]
    return captured


def _assert_no_foreign_markers(text, key, label=""):
    lowered = (text or "").lower()
    for marker in foreign_markers(key):
        assert marker not in lowered, f"{label}: {key} leaked marker {marker!r}"


def _flat(text):
    """Collapse whitespace so assertions survive prompt line wrapping."""
    return " ".join((text or "").split())


# ── 1. Existing churches default to UMC ───────────────────────────────────────

class TestDefaults:
    def test_existing_church_defaults_to_umc(self, church):
        assert church.denomination == "umc"
        assert DEFAULT_DENOMINATION == "umc"

    def test_new_church_row_without_denomination_defaults_to_umc(self):
        c = _make_church("Legacy Church")
        try:
            assert c.denomination == "umc"
            assert church_profile(c).key == "umc"
        finally:
            _drop_church(c)

    def test_missing_key_falls_back_to_umc(self):
        """A row predating the column is a legacy United Methodist church."""
        assert get_denomination_profile(None).key == "umc"
        assert get_denomination_profile("").key == "umc"

    def test_unrecognised_key_fails_safe_to_no_theology(self):
        """A present-but-unknown key must never inherit another denomination."""
        for bad in ("presbyterian", "umc ", "UMC", 7):
            profile = get_denomination_profile(bad)
            assert profile.key == "non_denominational", bad
            assert profile.sections == ()


# ── 2 & 13. Selection is saved and validated ──────────────────────────────────

class TestDenominationSettingsApi:
    def test_get_theology_returns_profile_and_options(self, auth_client, church):
        res = auth_client.get("/api/church/theology")
        assert res.status_code == 200
        data = res.get_json()
        assert data["denomination"] == "umc"
        assert data["profile"]["display_name"] == "United Methodist Church"
        assert data["profile"]["content_status"] == "reviewed"
        keys = {o["key"] for o in data["options"]}
        assert keys == set(ALL_KEYS)
        # Friendly names, never bare internal keys, are what the UI shows.
        assert all(o["display_name"] and o["display_name"] != o["key"]
                   for o in data["options"])

    def test_save_valid_denomination(self, auth_client, church):
        res = auth_client.post("/api/church/theology/denomination", json={
            "denomination": "sbc", "confirm": True,
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["changed"] is True
        assert data["denomination"] == "sbc"
        db.session.refresh(church)
        assert church.denomination == "sbc"
        assert church.denomination_profile_version == PROFILES["sbc"].version
        assert church.denomination_updated_at is not None
        _set_denomination(church, "umc")

    def test_change_requires_explicit_confirmation(self, auth_client, church):
        res = auth_client.post("/api/church/theology/denomination", json={
            "denomination": "gmc",
        })
        assert res.status_code == 400
        assert res.get_json()["confirmation_required"] is True
        db.session.refresh(church)
        assert church.denomination == "umc"

    def test_resaving_same_denomination_needs_no_confirmation(self, auth_client, church):
        res = auth_client.post("/api/church/theology/denomination", json={
            "denomination": "umc",
        })
        assert res.status_code == 200
        assert res.get_json()["changed"] is False

    @pytest.mark.parametrize("bad", ["", "UMC", "episcopal", "umc; drop table", 7, None, ["umc"]])
    def test_invalid_denomination_keys_rejected(self, auth_client, church, bad):
        res = auth_client.post("/api/church/theology/denomination", json={
            "denomination": bad, "confirm": True,
        })
        assert res.status_code == 400
        db.session.refresh(church)
        assert church.denomination == "umc"

    def test_registry_validation_helper(self):
        for key in ALL_KEYS:
            assert is_valid_denomination(key)
        for bad in ("", "UMC", "baptist", None, 3, ["umc"]):
            assert not is_valid_denomination(bad)

    def test_options_expose_profile_status(self):
        by_key = {o["key"]: o for o in denomination_options()}
        assert by_key["umc"]["awaiting_content"] is False
        assert by_key["sbc"]["awaiting_content"] is True
        assert by_key["gmc"]["awaiting_content"] is True


# ── 3. Authorization ──────────────────────────────────────────────────────────

class TestDenominationAuthorization:
    def test_anonymous_cannot_read_or_write(self, client):
        assert client.get("/api/church/theology").status_code == 401
        assert client.post("/api/church/theology/denomination", json={
            "denomination": "sbc", "confirm": True}).status_code == 401
        assert client.post("/api/church/theology/local-practices", json={
            "local_practices": {}}).status_code == 401

    def test_staff_role_cannot_change_denomination(self, client, church):
        staff = User(
            email="denom_staff@example.com",
            password_hash=generate_password_hash("staffpass1", method="pbkdf2:sha256"),
            church_id=church.id,
            role="staff",
        )
        db.session.add(staff)
        db.session.commit()
        try:
            login = client.post("/api/auth/login", json={
                "email": "denom_staff@example.com", "password": "staffpass1"})
            assert login.status_code == 200

            res = client.post("/api/church/theology/denomination", json={
                "denomination": "sbc", "confirm": True})
            assert res.status_code == 403

            res = client.post("/api/church/theology/local-practices", json={
                "local_practices": {"communion_frequency": "Monthly"}})
            assert res.status_code == 403

            # Reading is allowed, and reports that this user may not manage it.
            res = client.get("/api/church/theology")
            assert res.status_code == 200
            assert res.get_json()["can_manage"] is False

            db.session.refresh(church)
            assert church.denomination == "umc"
        finally:
            db.session.delete(staff)
            db.session.commit()


# ── 4, 5, 7, 8, 9, 18. Prompt isolation, staff and widget ─────────────────────

class TestPromptIsolation:
    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_staff_chat_loads_only_selected_denomination(self, auth_client, church, key):
        _set_denomination(church, key)
        captured = _staff_prompt(auth_client, "Do you baptize infants?")
        prompt = captured["prompt"]
        profile = PROFILES[key]
        assert f"profile key {key}" in prompt
        assert profile.display_name in prompt
        # Exactly one profile block is present.
        assert prompt.count("--- Denominational Profile:") == 1
        _assert_no_foreign_markers(prompt, key, "staff prompt")
        _assert_no_foreign_markers(captured["context"], key, "staff context")
        _set_denomination(church, "umc")

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_widget_chat_loads_only_selected_denomination(self, client, church, key):
        _set_denomination(church, key)
        captured = _widget_prompt(client, church, "Who may receive Communion?")
        prompt = captured["prompt"]
        assert f"profile key {key}" in prompt
        assert prompt.count("--- Denominational Profile:") == 1
        _assert_no_foreign_markers(prompt, key, "widget prompt")
        _assert_no_foreign_markers(captured["context"], key, "widget context")
        _set_denomination(church, "umc")

    def test_sbc_church_receives_no_umc_prompt_text_or_chunks(self, auth_client, client, church):
        _set_denomination(church, "sbc")
        staff = _staff_prompt(auth_client, "Do you baptize infants?")
        widget = _widget_prompt(client, church, "Do you baptize infants?")
        for label, captured in (("staff", staff), ("widget", widget)):
            prompt = captured["prompt"].lower()
            for banned in ("united methodist", "book of discipline",
                           "general conference", "prevenient grace",
                           "global methodist"):
                assert banned not in prompt, f"{label}: {banned}"
            assert "united methodist beliefs" not in captured["context"].lower()
            assert not any(s.get("type") == "denomination" for s in captured["sources"])
        # No UMC chunk can even become a retrieval candidate.
        for question in ISOLATION_QUESTIONS:
            assert score_denomination_chunks(question, "sbc") == []
        _set_denomination(church, "umc")

    def test_gmc_church_receives_no_umc_or_sbc_chunks(self, auth_client, church):
        _set_denomination(church, "gmc")
        captured = _staff_prompt(auth_client, "How is a pastor selected?")
        prompt = captured["prompt"].lower()
        for banned in ("united methodist", "book of discipline", "general conference",
                       "southern baptist", "baptist faith and message",
                       "cooperative program"):
            assert banned not in prompt, banned
        assert load_denomination_chunks("gmc") == []
        for question in ISOLATION_QUESTIONS:
            assert score_denomination_chunks(question, "gmc") == []
        _set_denomination(church, "umc")

    def test_non_denominational_church_gets_no_assumed_theology(self, auth_client, church):
        _set_denomination(church, "non_denominational")
        captured = _staff_prompt(auth_client, "What does the church teach about salvation?")
        prompt = captured["prompt"]
        lowered = prompt.lower()
        for banned in ("united methodist", "book of discipline", "prevenient grace",
                       "southern baptist", "global methodist"):
            assert banned not in lowered, banned
        # No denominational knowledge chunks at all, and the prompt says to defer.
        assert load_denomination_chunks("non_denominational") == []
        assert "non-denominational churches differ" in lowered
        assert "do not assume any tradition's theology" in lowered
        _set_denomination(church, "umc")

    def test_public_and_staff_prompts_use_the_same_profile(self, app, church):
        for key in ALL_KEYS:
            _set_denomination(church, key)
            profile = PROFILES[key]
            staff = build_system_prompt(church, staff=True)
            widget = build_system_prompt(church, widget=True)
            block = profile.prompt_block()
            assert block in staff, key
            assert block in widget, key
        _set_denomination(church, "umc")

    def test_platform_prompt_is_withheld_when_it_names_another_denomination(
        self, app, church
    ):
        from models import SystemPrompt
        row = SystemPrompt.query.get(1)
        original = row.content
        row.content = (
            "You are Wesley, grounded in Wesleyan theology and the Book of "
            "Discipline of The United Methodist Church."
        )
        db.session.commit()
        try:
            _set_denomination(church, "umc")
            umc_prompt = build_system_prompt(church, widget=True)
            assert "Book of Discipline of The United Methodist Church" in umc_prompt

            _set_denomination(church, "sbc")
            sbc_prompt = build_system_prompt(church, widget=True)
            assert "united methodist" not in sbc_prompt.lower()
            assert "book of discipline" not in sbc_prompt.lower()
            # The neutral identity line still anchors the public prompt.
            assert "ministry assistant for a local church" in sbc_prompt
        finally:
            row.content = original
            db.session.commit()
            _set_denomination(church, "umc")


# ── 6 & 16. UMC behaviour and citations preserved ─────────────────────────────

class TestUmcBackwardCompatibility:
    def test_umc_identity_and_current_facts_survive(self, app, church):
        _set_denomination(church, "umc")
        for kwargs in ({"widget": True}, {"staff": True}):
            prompt = build_system_prompt(church, **kwargs)
            assert "United Methodist Church" in prompt
            assert "2020/2024 Book of Discipline is the current one" in prompt
            assert "Never quote Book of Discipline paragraph numbers" in prompt
            assert 'claim that you will "learn,"' in prompt
            assert "Wesleyan-Arminian perspective" in prompt

    def test_umc_chunks_unchanged_in_shape_and_count(self):
        chunks = load_denomination_chunks("umc")
        assert len(chunks) == len(PROFILES["umc"].sections) == 10
        assert all(c["type"] == "denomination" for c in chunks)
        assert all(c["source"].startswith("United Methodist beliefs: ") for c in chunks)
        assert all(c["location"].startswith("https://") for c in chunks)

    def test_legacy_umc_facts_module_still_works(self):
        import umc_facts
        assert len(umc_facts.SECTIONS) == 10
        assert umc_facts.load_denomination_chunks() == load_denomination_chunks("umc")
        scored = umc_facts.score_denomination_chunks("Do you baptize infants?")
        assert scored and "Baptism" in scored[0][1]["source"]

    def test_doctrine_questions_retrieve_right_umc_sections(self):
        cases = [
            ("What is your stance on homosexuality?", "Marriage and human sexuality"),
            ("Who can take communion at your church?", "Holy Communion"),
            ("Do you baptize infants?", "Baptism"),
            ("Can women be pastors in your church?", "Clergy and ordination"),
        ]
        for question, expected in cases:
            scored = score_denomination_chunks(question, "umc")
            assert scored, question
            titles = " | ".join(c["source"] for _, c in scored)
            assert expected in titles, f"{question} -> {titles}"

    def test_denomination_sources_still_produce_citations(self, client, church):
        _set_denomination(church, "umc")
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


# ── 10 & 17. Authority order, conflict and uncertainty instructions ───────────

class TestAuthorityAndConflictRules:
    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_authority_order_and_conflict_rules_in_both_prompts(self, app, church, key):
        _set_denomination(church, key)
        for kwargs in ({"widget": True}, {"staff": True}):
            prompt = _flat(build_system_prompt(church, **kwargs))
            assert "--- Authority and Conflict Rules ---" in prompt
            assert "Pastor-approved local practice and approved Q&A" in prompt
            assert "The selected denominational profile below." in prompt
            assert "never rewrites objective denominational facts" in prompt
            assert "you must NOT say the denomination" in prompt
            assert "recommend contacting church leadership" in prompt
            assert "never blend positions" in prompt
            assert "Handling uncertainty in this profile:" in prompt
        _set_denomination(church, "umc")

    def test_authority_order_ranks_local_above_denomination(self, app, church):
        prompt = build_system_prompt(church, widget=True)
        local_pos = prompt.index("Pastor-approved local practice and approved Q&A")
        denom_pos = prompt.index("The selected denominational profile below.")
        model_pos = prompt.index("Your own general knowledge")
        assert local_pos < denom_pos < model_pos

    def test_approved_qna_keeps_verbatim_precedence(self, app, church):
        pair = QnAPair(
            church_id=church.id,
            question="Do you baptize infants?",
            answer="Yes — talk to Pastor Dana to schedule one.",
            is_active=True,
        )
        db.session.add(pair)
        db.session.commit()
        try:
            for kwargs in ({"widget": True}, {"staff": True}):
                prompt = build_system_prompt(church, **kwargs)
                assert "--- Approved Q&A — Always Use These Answers Exactly ---" in prompt
                assert "Yes — talk to Pastor Dana to schedule one." in prompt
                qna_pos = prompt.index("--- Approved Q&A")
                denom_pos = prompt.index("--- Denominational Profile:")
                # Approved Q&A is stated after the profile so it reads last, and
                # the authority order says it wins.
                assert denom_pos < qna_pos
        finally:
            db.session.delete(pair)
            db.session.commit()

    def test_awaiting_content_profiles_forbid_definitive_claims(self, app, church):
        for key in ("sbc", "gmc"):
            _set_denomination(church, key)
            prompt = build_system_prompt(church, widget=True)
            assert "awaiting reviewed, approved theological content" in prompt
            assert "Do not make definitive claims about what this denomination" in prompt
        _set_denomination(church, "umc")


# ── 11, 12, 14, 15. Local practice storage and tenancy ────────────────────────

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
        prompt = build_system_prompt(church, widget=True)
        assert "--- Approved Local Church Practice (pastor-approved) ---" in prompt
        assert "First Sunday of each month" in prompt
        assert "does not perform same-sex weddings" in prompt
        assert "It is not a statement of denominational teaching." in prompt
        assert "Topics to refer to a pastor: Grief; Divorce" in prompt

        church.local_practices = None
        db.session.commit()

    def test_statement_of_faith_is_local_teaching(self, auth_client, church):
        _set_denomination(church, "non_denominational")
        res = auth_client.post("/api/church/theology/local-practices", json={
            "statement_of_faith": "We believe the Bible is God's word.",
        })
        assert res.status_code == 200
        db.session.refresh(church)
        prompt = build_system_prompt(church, widget=True)
        assert "We believe the Bible is God's word." in prompt
        assert "not a denominational claim" in prompt
        church.statement_of_faith = None
        _set_denomination(church, "umc")

    def test_local_practices_only_reach_their_own_church(self, app, church):
        other = _make_church("Second Church", denomination="sbc")
        other.local_practices = json.dumps({
            "communion_frequency": "Weekly at Second Church",
            "preferred_clergy_title": "Brother",
        })
        other.statement_of_faith = "Second Church statement of faith text."
        db.session.commit()
        try:
            mine = build_system_prompt(church, widget=True)
            assert "Weekly at Second Church" not in mine
            assert "Second Church statement of faith text." not in mine
            assert "Second Church" not in mine

            theirs = build_system_prompt(other, widget=True)
            assert "Weekly at Second Church" in theirs
            assert church.name not in theirs
        finally:
            _drop_church(other)

    def test_cross_tenant_qna_cannot_leak(self, app, church):
        other = _make_church("Third Church", denomination="gmc")
        pair = QnAPair(
            church_id=other.id,
            question="Who is your pastor?",
            answer="Third Church's pastor is Rev. Elsewhere.",
            is_active=True,
        )
        db.session.add(pair)
        db.session.commit()
        try:
            mine = build_system_prompt(church, widget=True)
            assert "Rev. Elsewhere" not in mine
            theirs = build_system_prompt(other, widget=True)
            assert "Rev. Elsewhere" in theirs
        finally:
            _drop_church(other)

    def test_reading_theology_is_scoped_to_own_church(self, auth_client, church):
        other = _make_church("Fourth Church", denomination="sbc")
        other.local_practices = json.dumps({"preferred_clergy_title": "Elder"})
        db.session.commit()
        try:
            data = auth_client.get("/api/church/theology").get_json()
            assert data["denomination"] == church.denomination
            assert data["local_practices"].get("preferred_clergy_title") != "Elder"
        finally:
            _drop_church(other)

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

    def test_changing_denomination_preserves_local_content_and_changes_behaviour(
        self, auth_client, church
    ):
        pair = QnAPair(
            church_id=church.id,
            question="How do I join?",
            answer="Talk to the office and we'll walk you through it.",
            is_active=True,
        )
        db.session.add(pair)
        db.session.commit()
        auth_client.post("/api/church/theology/local-practices", json={
            "local_practices": {"communion_frequency": "Weekly"},
            "statement_of_faith": "Our local statement.",
        })
        try:
            before = build_system_prompt(church, widget=True)
            assert "United Methodist Church" in before
            assert score_denomination_chunks("Do you baptize infants?",
                                             church.denomination)

            res = auth_client.post("/api/church/theology/denomination", json={
                "denomination": "sbc", "confirm": True,
            })
            assert res.status_code == 200
            db.session.refresh(church)

            # Local content preserved verbatim.
            assert church.statement_of_faith == "Our local statement."
            assert json.loads(church.local_practices)["communion_frequency"] == "Weekly"
            assert QnAPair.query.filter_by(church_id=church.id).count() == 1

            after = build_system_prompt(church, widget=True)
            assert "Weekly" in after
            assert "Our local statement." in after
            assert "Talk to the office" in after
            # Prompt and retrieval behaviour both changed.
            assert "united methodist" not in after.lower()
            assert "Southern Baptist Convention" in after
            assert score_denomination_chunks("Do you baptize infants?",
                                             church.denomination) == []
        finally:
            db.session.delete(pair)
            church.local_practices = None
            church.statement_of_faith = None
            db.session.commit()
            _set_denomination(church, "umc")


# ── 19. Reusable isolation matrix ─────────────────────────────────────────────

class TestIsolationMatrix:
    def test_matrix_covers_the_required_questions(self):
        for question in (
            "Do you baptize infants?",
            "Who may receive Communion?",
            "Can women serve as pastors?",
            "How is a pastor selected?",
            "What authority does the denomination have over this congregation?",
            "What does the church teach about salvation?",
            "What does the church teach about marriage?",
            "Can someone be rebaptized?",
        ):
            assert question in ISOLATION_QUESTIONS

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_retrieval_never_crosses_profiles(self, key):
        for question in ISOLATION_QUESTIONS:
            for _, chunk in score_denomination_chunks(question, key):
                assert chunk["denomination"] == key
                _assert_no_foreign_markers(chunk["content"], key, question)
                _assert_no_foreign_markers(chunk["source"], key, question)

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_profile_prompt_blocks_never_cross(self, key):
        _assert_no_foreign_markers(PROFILES[key].prompt_block(), key, "prompt_block")

    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_doctrinal_matrix_over_assembled_prompts(self, app, church, key):
        """Every matrix question, for every profile, over the real prompt path."""
        _set_denomination(church, key)
        staff = build_system_prompt(church, staff=True)
        widget = build_system_prompt(church, widget=True)
        for prompt, label in ((staff, "staff"), (widget, "widget")):
            _assert_no_foreign_markers(prompt, key, label)
        for question in ISOLATION_QUESTIONS:
            scored = score_denomination_chunks(question, key)
            assert all(c["denomination"] == key for _, c in scored)
        _set_denomination(church, "umc")

    def test_every_profile_declares_its_evaluation_questions(self):
        for key in ALL_KEYS:
            profile = PROFILES[key]
            assert len(profile.evaluation_questions) >= 8
            assert profile.version
            assert profile.short_description
            assert profile.uncertainty_instructions.strip()

    def test_profiles_awaiting_content_carry_no_knowledge(self):
        for key, profile in PROFILES.items():
            if profile.awaiting_content:
                assert profile.sections == (), key
                assert profile.source_urls == (), key


# ── Settings UI surface ───────────────────────────────────────────────────────

class TestTheologySettingsPanel:
    def test_dashboard_renders_theology_panel_for_admins(self, auth_client, church):
        res = auth_client.get("/dashboard")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert 'data-panel="theology"' in html
        assert 'id="panel-theology"' in html
        assert "Theology &amp; Affiliation" in html
        assert "'theology'" in html          # registered as a valid panel
        assert "/api/church/theology" in html
        assert "Approved Theological Q&amp;A" in html
