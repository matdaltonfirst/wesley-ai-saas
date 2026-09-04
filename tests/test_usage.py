"""Tests for per-tenant AI usage metering.

Usage data cannot be collected retroactively, so the cost of a silent failure
here is permanent: the numbers behind pricing, abuse detection, and any
before/after comparison of a retrieval change simply would not exist.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from helpers import _record_gemini_usage
from models import db, UsageDaily
from usage import STAFF, WIDGET, record_usage, usage_totals


def _metered_gemini(prompt=100, response=20, model="gemini-2.5-flash-lite"):
    """A call_gemini double that reports token counts the way the real one does."""
    def fake(question, context, history, system_instruction, usage=None, **kwargs):
        if usage is not None:
            usage.update({
                "model": model,
                "prompt_tokens": prompt,
                "response_tokens": response,
                "total_tokens": prompt + response,
            })
        return "Here is your answer."
    return fake


# ── Capturing counts off the Gemini response ─────────────────────────────────

class TestGeminiUsageCapture:
    def test_reads_the_token_counts_gemini_returns(self):
        usage = {}
        response = SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=1200, candidates_token_count=340,
            total_token_count=1540,
        ))
        _record_gemini_usage(usage, response, "gemini-2.5-flash-lite")
        assert usage == {
            "model": "gemini-2.5-flash-lite",
            "prompt_tokens": 1200,
            "response_tokens": 340,
            "total_tokens": 1540,
        }

    def test_missing_metadata_yields_zeros_not_an_error(self):
        """A renamed or absent field must never turn a good answer into a 502."""
        usage = {}
        _record_gemini_usage(usage, SimpleNamespace(), "gemini-2.5-flash-lite")
        assert usage["total_tokens"] == 0
        assert usage["model"] == "gemini-2.5-flash-lite"

    def test_total_falls_back_to_the_sum_of_its_parts(self):
        usage = {}
        response = SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=90, candidates_token_count=10,
        ))
        _record_gemini_usage(usage, response, "m")
        assert usage["total_tokens"] == 100


# ── Recording ─────────────────────────────────────────────────────────────────

class TestRecordUsage:
    def test_repeat_calls_fold_into_one_daily_row(self, church):
        for _ in range(3):
            record_usage(church.id, STAFF, {
                "model": "m", "prompt_tokens": 10,
                "response_tokens": 5, "total_tokens": 15,
            })
        rows = UsageDaily.query.filter_by(church_id=church.id).all()
        assert len(rows) == 1
        assert rows[0].calls == 3
        assert rows[0].total_tokens == 45

    def test_surfaces_are_bucketed_separately(self, church):
        record_usage(church.id, STAFF, {"model": "m", "total_tokens": 10})
        record_usage(church.id, WIDGET, {"model": "m", "total_tokens": 90})
        surfaces = {r.surface: r.total_tokens
                    for r in UsageDaily.query.filter_by(church_id=church.id)}
        assert surfaces == {STAFF: 10, WIDGET: 90}

    def test_a_call_is_recorded_even_without_token_counts(self, church):
        """Knowing a request happened matters even when the counts don't arrive."""
        record_usage(church.id, WIDGET, {})
        row = UsageDaily.query.filter_by(church_id=church.id).one()
        assert row.calls == 1
        assert row.model == "unknown"

    def test_metering_failure_never_raises(self, church):
        """record_usage is called after the answer is already saved — it must
        not be able to turn a delivered reply into an error."""
        with patch("usage.UsageDaily.query") as broken:
            broken.filter_by.side_effect = RuntimeError("database on fire")
            record_usage(church.id, STAFF, {"model": "m"})  # must not raise


# ── Aggregation ───────────────────────────────────────────────────────────────

class TestUsageTotals:
    def test_totals_split_staff_and_widget_calls(self, church):
        record_usage(church.id, STAFF, {"model": "m", "total_tokens": 100})
        record_usage(church.id, WIDGET, {"model": "m", "total_tokens": 300})
        record_usage(church.id, WIDGET, {"model": "m", "total_tokens": 200})

        totals = usage_totals([church.id])[church.id]
        assert totals["calls"] == 3
        assert totals["staff_calls"] == 1
        assert totals["widget_calls"] == 2
        assert totals["total_tokens"] == 600

    def test_rows_outside_the_window_are_excluded(self, church):
        db.session.add(UsageDaily(
            church_id=church.id, day=date.today() - timedelta(days=45),
            surface=STAFF, model="m", calls=99, total_tokens=99_000,
        ))
        db.session.commit()
        record_usage(church.id, STAFF, {"model": "m", "total_tokens": 10})

        totals = usage_totals([church.id], days=30)[church.id]
        assert totals["calls"] == 1
        assert totals["total_tokens"] == 10

    def test_empty_id_list_returns_nothing(self, church):
        record_usage(church.id, STAFF, {"model": "m", "total_tokens": 10})
        assert usage_totals([]) == {}


# ── End to end through the chat endpoints ────────────────────────────────────

class TestMeteringThroughTheEndpoints:
    def test_staff_chat_records_its_tokens(self, auth_client, church):
        with patch("routes.chat.call_gemini", side_effect=_metered_gemini(prompt=800, response=120)):
            res = auth_client.post("/api/chat", json={"question": "Draft a bulletin"})
        assert res.status_code == 200

        row = UsageDaily.query.filter_by(church_id=church.id, surface=STAFF).one()
        assert row.prompt_tokens == 800
        assert row.response_tokens == 120
        assert row.total_tokens == 920
        assert row.model == "gemini-2.5-flash-lite"

    def test_widget_chat_records_against_the_visited_church(self, client, church):
        with patch("routes.widget.call_gemini", side_effect=_metered_gemini(prompt=50, response=25)):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id, "question": "What time is worship?",
            })
        assert res.status_code == 200

        row = UsageDaily.query.filter_by(church_id=church.id, surface=WIDGET).one()
        assert row.calls == 1
        assert row.total_tokens == 75

    def test_a_failed_call_is_not_metered(self, auth_client, church):
        """Nothing was spent on an answer that never came back."""
        with patch("routes.chat.call_gemini", side_effect=Exception("503 unavailable")):
            res = auth_client.post("/api/chat", json={"question": "Draft a bulletin"})
        assert res.status_code == 503
        assert UsageDaily.query.filter_by(church_id=church.id).count() == 0


# ── Admin surfacing ───────────────────────────────────────────────────────────

class TestAdminUsageColumns:
    def test_super_admin_sees_per_church_and_platform_totals(self, app, auth_client, church, admin_user):
        record_usage(church.id, WIDGET, {"model": "m", "total_tokens": 500})
        record_usage(church.id, STAFF, {"model": "m", "total_tokens": 250})

        with patch("routes.admin.is_super_admin", return_value=True):
            res = auth_client.get("/api/admin/churches")
        assert res.status_code == 200
        data = res.get_json()

        row = next(c for c in data["churches"] if c["id"] == church.id)
        assert row["ai_calls_30d"] == 2
        assert row["ai_tokens_30d"] == 750
        assert row["ai_widget_calls_30d"] == 1
        assert data["stats"]["ai_tokens_30d"] >= 750
