"""Constant Contact, Text In Church (with its mock), Meta, Subsplash, metrics, and the routes."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import crypto
import streaming_sources as SS
import text_in_church_metrics as M
from connectors import runner
from connectors.constant_contact import ConstantContactConnector
from connectors.meta import MetaConnector
from connectors.subsplash import SubsplashConnector
from connectors.text_in_church import TextInChurchConnector
from models import (
    AuditLog, EmailCampaign, EmailListSnapshot, IntegrationToken, SocialPost, StreamingNumber,
    TicConnectCard, TicContact, TicMessage, db,
)
from tests.conftest import login, make_user


def resp(body, status=200, text=None):
    r = MagicMock(); r.status_code = status; r.headers = {}; r.json.return_value = body; r.text = text or json.dumps(body)
    return r


# ── Constant Contact ──────────────────────────────────────────────────────────

class FakeCC:
    def __init__(self, stats_errors=None):
        self.calls = []
        self.stats_errors = stats_errors or []

    def __call__(self, method, url, **kw):
        self.calls.append((url, kw.get("params"), kw.get("auth")))
        if "authz.constantcontact.com" in url:
            return resp({"access_token": "A", "refresh_token": "R", "expires_in": 28800})
        if url.endswith("/v3/emails"):
            return resp({"campaigns": [{"campaign_id": "c1", "name": "Fall Festival", "updated_at": "2026-09-20T14:00:00.000Z",
                                        "campaign_activities": [{"campaign_activity_id": "a1", "role": "primary_email"},
                                                                {"campaign_activity_id": "x9", "role": "permalink"}]}], "_links": {}})
        if "/reports/stats/email_campaign_activities/" in url:
            return resp({"results": [{"campaign_id": "c1", "campaign_activity_id": "a1", "stats": {
                "em_sends": 800, "em_opens": 410, "em_clicks": 63, "em_bounces": 5, "em_optouts": 2, "em_forwards": 1}}],
                "errors": self.stats_errors})
        if url.endswith("/v3/contact_lists"):
            return resp({"lists": [{"list_id": "L1", "name": "Weekly Newsletter", "membership_count": 612}, {"list_id": "L2", "name": "Youth"}]})
        if url.endswith("/v3/contacts"):
            return resp({"contacts": [], "contacts_count": 944})
        raise AssertionError(url)


@pytest.fixture
def cc(app, church, monkeypatch):
    monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_ID", "id"); monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_SECRET", "sec")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
    c = ConstantContactConnector(); c.min_interval_seconds = 0; c.request_fn = FakeCC()
    c.exchange_code("code")
    return c


class TestConstantContact:
    def test_campaign_results_and_list_sizes_are_stored(self, cc):
        run = runner.run_sync(cc)
        assert run.status == "ok", run.error
        e = EmailCampaign.query.one()
        assert (e.name, e.sends, e.opens, e.clicks, e.bounces, e.optouts) == ("Fall Festival", 800, 410, 63, 5, 2)
        snaps = {s.list_id: s.members for s in EmailListSnapshot.query}
        assert snaps == {"L1": 612, "all": 944}                  # a list with no count is skipped, not zeroed

    def test_only_primary_and_resend_activities_are_asked_about(self, cc):
        runner.run_sync(cc)
        url = next(u for u, *_ in cc.request_fn.calls if "email_campaign_activities" in u)
        assert url.endswith("/a1") and "x9" not in url

    def test_list_growth_is_one_snapshot_per_day(self, cc):
        runner.run_sync(cc); runner.run_sync(cc)
        assert EmailListSnapshot.query.count() == 2 and runner.run_sync(cc).rows_changed == 0

    def test_a_larger_list_the_same_day_updates_that_days_snapshot(self, cc):
        runner.run_sync(cc)
        base = cc.request_fn

        def bigger(method, url, **kw):
            r = base(method, url, **kw)
            if url.endswith("/v3/contacts"):
                r.json.return_value = {"contacts_count": 950}
            return r
        cc.request_fn = bigger
        runner.run_sync(cc)
        assert EmailListSnapshot.query.filter_by(list_id="all").one().members == 950

    def test_token_exchange_uses_basic_auth_and_offline_access(self, cc):
        auth = [a for u, p, a in cc.request_fn.calls if "authz" in u][0]
        assert auth == ("id", "sec") and "offline_access" in cc.authorize_redirect("s")

    def test_stat_errors_for_one_email_are_a_warning(self, cc):
        cc.request_fn = FakeCC(stats_errors=[{"error_key": "x"}])
        run = runner.run_sync(cc)
        assert run.status == "partial" and EmailCampaign.query.count() == 1

    def test_no_contact_addresses_are_requested_or_kept(self, cc):
        runner.run_sync(cc)
        for _, params, _ in cc.request_fn.calls:
            assert not params or "email" not in json.dumps(params).lower() or "include_membership_count" in json.dumps(params)
        assert not any("email_address" in r.payload for r in __import__("models").RawPayload.query)


# ── Text In Church (mock) ─────────────────────────────────────────────────────

@pytest.fixture
def tic(app, church, monkeypatch):
    monkeypatch.setenv("TEXT_IN_CHURCH_MOCK", "1")
    c = TextInChurchConnector(); c.min_interval_seconds = 0
    return c


class TestTextInChurch:
    def test_without_access_it_waits_and_says_how_to_get_it(self, app, church, monkeypatch):
        monkeypatch.delenv("TEXT_IN_CHURCH_MOCK", raising=False)
        c = TextInChurchConnector()
        h = runner.health(c)
        assert h.status == "waiting" and "support@textinchurch.com" in h.detail and runner.run_sync(c) is None

    def test_the_mock_lets_everything_run_before_access_exists(self, tic):
        run = runner.run_sync(tic, "manual")
        assert run.status == "ok", run.error
        assert TicContact.query.count() == 10 and TicMessage.query.count() >= 20 and TicConnectCard.query.count() == 3

    def test_only_name_and_status_are_kept_for_contacts(self, tic):
        runner.run_sync(tic)
        from models import RawPayload
        blob = " ".join(r.payload for r in RawPayload.query.filter_by(resource="contact"))
        assert "primary_phone" not in blob and "contact_email" not in blob and "invalid" not in blob
        assert not any(hasattr(TicContact, a) for a in ("phone", "email", "address"))

    def test_message_content_is_in_the_table_but_not_in_raw_payloads(self, tic):
        runner.run_sync(tic)
        from models import RawPayload
        assert TicMessage.query.filter(TicMessage.content.like("%youth group%")).count() >= 1
        assert not any("youth group" in r.payload for r in RawPayload.query.filter_by(resource="message"))

    def test_syncing_twice_is_idempotent(self, tic):
        runner.run_sync(tic)
        assert runner.run_sync(tic).rows_changed == 0

    def test_pagination_uses_limit_and_offset(self, tic, monkeypatch):
        import connectors.text_in_church as T
        monkeypatch.setattr(T, "PAGE", 4)
        runner.run_sync(tic)
        assert TicContact.query.count() == 10

    def test_an_api_key_is_stored_encrypted_and_used_as_a_bearer_token(self, app, church, monkeypatch):
        monkeypatch.delenv("TEXT_IN_CHURCH_MOCK", raising=False); monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        c = TextInChurchConnector(); c.min_interval_seconds = 0
        c.save_api_key("tic-key-1234567890")
        assert "tic-key" not in IntegrationToken.query.one().access_token
        seen = {}

        def fake(method, url, **kw):
            seen["h"] = kw["headers"]; seen["url"] = url
            return resp([])
        c.request_fn = fake
        assert runner.run_sync(c).status == "ok"
        assert seen["h"]["Authorization"] == "Bearer tic-key-1234567890" and seen["url"].startswith("https://api.textinchurch.com/API/1_0/")

    def test_a_rejected_key_says_to_make_a_new_one(self, app, church, monkeypatch):
        monkeypatch.delenv("TEXT_IN_CHURCH_MOCK", raising=False); monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        c = TextInChurchConnector(); c.min_interval_seconds = 0; c.save_api_key("bad-key-1234567")
        c.request_fn = lambda m, u, **k: resp({}, 401)
        run = runner.run_sync(c)
        assert run.status == "failed" and run.error_kind == "auth" and runner.health(c).status == "needs_reconnect"

    def test_webhook_token_is_required(self, tic, monkeypatch, app):
        monkeypatch.setenv("TEXT_IN_CHURCH_WEBHOOK_TOKEN", "t0k")
        with app.test_request_context("/webhooks/text_in_church?token=t0k", method="POST"):
            assert tic.verify_webhook({}, b"{}")
        with app.test_request_context("/webhooks/text_in_church?token=wrong", method="POST"):
            assert not tic.verify_webhook({}, b"{}")
        monkeypatch.delenv("TEXT_IN_CHURCH_WEBHOOK_TOKEN")
        with app.test_request_context("/webhooks/text_in_church?token=", method="POST"):
            assert not tic.verify_webhook({}, b"{}")


class TestTextInChurchMetrics:
    @pytest.fixture(autouse=True)
    def synced(self, tic):
        runner.run_sync(tic)

    def test_message_volume_splits_incoming_human_and_automated(self):
        v = M.message_volume(60)
        assert v["incoming"] == 10 and v["outgoing_automated"] == 10 and v["outgoing_human"] == 5

    def test_new_contacts_and_connect_cards(self):
        assert M.new_contacts(7) == 3 and M.new_contacts(60) == 10 and M.connect_cards(60) == 3

    def test_response_time_counts_only_human_replies(self):
        r = M.response_times(60)
        assert r["answered"] == 5 and r["unanswered"] == 5 and r["median_minutes"] > 60

    def test_automated_replies_are_not_responses(self):
        TicMessage.query.filter_by(automated=False, incoming=False).delete(); db.session.commit()
        r = M.response_times(60)
        assert r["answered"] == 0 and r["unanswered"] == 10 and r["median_minutes"] is None

    def test_guests_awaiting_followup_are_those_with_no_human_reply(self):
        names = {g["contact_id"] for g in M.guests_awaiting_followup(60)}
        assert names == {"1001", "1003", "1005", "1007", "1009"}          # odd contacts were never answered

    def test_a_very_recent_guest_is_not_flagged_yet(self):
        c = TicContact.query.filter_by(contact_id="1001").one()
        c.created_at = datetime.utcnow() - timedelta(hours=1); db.session.commit()
        assert "1001" not in {g["contact_id"] for g in M.guests_awaiting_followup(60)}

    def test_someone_who_opted_out_is_never_flagged(self):
        c = TicContact.query.filter_by(contact_id="1003").one()
        c.optout_sms = True; db.session.commit()
        assert "1003" not in {g["contact_id"] for g in M.guests_awaiting_followup(60)}

    def test_the_summary_endpoint_needs_the_text_in_church_permission_and_is_audited(self, client, church):
        login(client, make_user("f@daltonfumc.com", ["family"]))
        assert client.get("/api/text-in-church/summary").status_code == 403
        client.get("/logout")
        login(client, make_user("aa@daltonfumc.com", ["admin_assistant"]))
        r = client.get("/api/text-in-church/summary")
        assert r.status_code == 200 and "awaiting_followup" in r.get_json()
        assert AuditLog.query.filter_by(action="data.read", source="text_in_church").count() == 1


# ── Meta ──────────────────────────────────────────────────────────────────────

class FakeMeta:
    def __init__(self, fail_insights=False, bad_token=False):
        self.fail_insights, self.bad_token, self.calls = fail_insights, bad_token, []

    def __call__(self, method, url, **kw):
        self.calls.append((url, kw.get("params")))
        if self.bad_token:
            return resp({}, 400, text='{"error":{"message":"Error validating access token","type":"OAuthException","code":190}}')
        if url.endswith("/42/posts"):
            return resp({"data": [{"id": "42_1", "created_time": "2026-10-01T15:00:00+0000", "message": "Fall Festival is coming!",
                                   "permalink_url": "https://fb.example/p1", "status_type": "added_photos",
                                   "reactions": {"summary": {"total_count": 30}}, "comments": {"summary": {"total_count": 4}}, "shares": {"count": 6}}]})
        if "/insights" in url and self.fail_insights:
            return resp({}, 400, text='{"error":{"message":"(#100) The value must be a valid insights metric","code":100}}')
        if url.endswith("/42_1/insights"):
            return resp({"data": [{"name": "post_impressions_unique", "values": [{"value": 1200}]}]})
        if url.endswith("/42/live_videos"):
            return resp({"data": [{"id": "L1", "status": "VOD", "title": "Modern Service Live", "creation_time": "2026-10-04T13:25:00+0000",
                                   "live_views": 150, "video": {"id": "V1"}}]})
        if url.endswith("/V1/video_insights"):
            return resp({"data": [{"name": "total_video_views", "values": [{"value": 900}]},
                                  {"name": "total_video_view_total_time", "values": [{"value": 36000000}]}]})
        if url.endswith("/77/media"):
            return resp({"data": [{"id": "ig1", "caption": "Sunday!", "media_type": "REEL", "permalink": "https://ig.example/r", "timestamp": "2026-10-02T12:00:00+0000", "like_count": 50, "comments_count": 5}]})
        if url.endswith("/ig1/insights"):
            return resp({"data": [{"name": "reach", "values": [{"value": 800}]}, {"name": "views", "values": [{"value": 1500}]}]})
        raise AssertionError(url)


@pytest.fixture
def meta(app, church, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
    c = MetaConnector(); c.min_interval_seconds = 0; c.request_fn = FakeMeta()
    c.save_token("page-token"); runner.set_config("facebook", page_id="42", ig_user_id="77")
    return c


class TestMeta:
    def test_posts_live_videos_and_instagram_are_stored(self, meta):
        run = runner.run_sync(meta)
        assert run.status == "ok", (run.error, run.detail)
        fb = SocialPost.query.filter_by(platform="facebook", post_id="42_1").one()
        assert (fb.engagements, fb.reach, fb.kind) == (40, 1200, "added_photos")
        live = SocialPost.query.filter_by(post_id="V1").one()
        assert (live.kind, live.live_views, live.video_views, live.watch_seconds) == ("live", 150, 900, 36000)
        ig = SocialPost.query.filter_by(platform="instagram").one()
        assert (ig.engagements, ig.reach, ig.video_views, ig.kind) == (55, 800, 1500, "reel")

    def test_a_live_video_feeds_the_streaming_pane_without_inventing_a_peak(self, meta):
        runner.run_sync(meta)
        n = StreamingNumber.query.one()
        assert (n.platform, n.total_views, n.watch_minutes, n.service_label) == ("facebook", 900, 600, "Modern service")
        assert n.peak_concurrent is None                      # Meta's API has no peak concurrent figure

    def test_retired_metrics_become_one_warning_each_not_a_failure(self, meta):
        meta.request_fn = FakeMeta(fail_insights=True)
        run = runner.run_sync(meta)
        assert run.status == "partial" and any("not available from Meta" in w for w in json.loads(run.detail)["warnings"])
        assert SocialPost.query.filter_by(post_id="42_1").one().reach is None and SocialPost.query.count() == 3

    def test_an_invalid_token_means_reconnect(self, meta):
        meta.request_fn = FakeMeta(bad_token=True)
        run = runner.run_sync(meta)
        assert run.status == "failed" and run.error_kind == "auth" and runner.health(meta).status == "needs_reconnect"

    def test_without_a_page_id_it_asks_for_one(self, app, church, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        c = MetaConnector(); c.save_token("t")
        assert runner.health(c).status == "waiting" and "Page id" in runner.health(c).detail

    def test_instagram_is_skipped_when_no_account_is_configured(self, meta):
        runner.set_config("facebook", ig_user_id="")
        runner.run_sync(meta)
        assert SocialPost.query.filter_by(platform="instagram").count() == 0

    def test_syncing_twice_changes_nothing(self, meta):
        runner.run_sync(meta)
        assert runner.run_sync(meta).rows_changed == 0

    def test_the_token_is_sent_in_a_header_not_a_url(self, meta):
        runner.run_sync(meta)
        assert all("page-token" not in url for url, _ in meta.request_fn.calls)


# ── Subsplash and the source adapters ─────────────────────────────────────────

class TestSubsplashAndSources:
    def test_subsplash_is_honest_about_being_manual(self, app, church):
        c = SubsplashConnector()
        h = runner.health(c)
        assert h.status == "manual" and "public analytics" in h.detail and runner.run_sync(c) is None

    def test_the_placeholder_source_cannot_fetch_and_explains_why(self):
        s = SS.SubsplashSource()
        assert not s.available()
        with pytest.raises(SS.NotAvailable, match="CSV"):
            s.fetch(None, None)

    def test_any_adapter_persists_through_the_same_rules(self, app, church):
        from datetime import date
        rows = [SS.SourceRow(date(2026, 10, 4), "resi", total_views=200, peak_concurrent=40, external_id="r1")]
        assert SS.persist(rows)["created"] == 1
        n = StreamingNumber.query.one()
        assert n.platform == "other"                       # unknown platforms are normalized, never free text
