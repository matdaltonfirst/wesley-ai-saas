"""The connector framework: backoff, tokens, runs, health, idempotent upserts."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import requests

import crypto
from connectors import runner
from connectors.base import Connector, OAuthConnector, StaticTokenConnector, SyncContext
from connectors.errors import AuthError, ConnectorError, NotConfigured, RateLimited, UpstreamError
from connectors.http import HttpClient
from models import (
    AuditLog, Integration, IntegrationToken, RawPayload, SyncRun, YoutubeVideo, db,
)


def resp(status=200, body=None, headers=None, text=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = body if body is not None else {}
    r.text = text if text is not None else json.dumps(body or {})
    return r


def client(*responses, **kw):
    seq = list(responses)
    sleeps = []

    def fake(method, url, **kwargs):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    c = HttpClient("Test", request_fn=fake, sleep=sleeps.append, **kw)
    c.sleeps = sleeps
    return c


# ── HTTP client ───────────────────────────────────────────────────────────────

class TestHttpClient:
    def test_success_returns_json(self):
        assert client(resp(200, {"a": 1})).get("http://x") == {"a": 1}

    def test_server_errors_are_retried_with_growing_backoff(self):
        c = client(resp(503), resp(502), resp(200, {"ok": True}), base_delay=1.0)
        assert c.get("http://x") == {"ok": True}
        assert len(c.sleeps) == 2 and c.sleeps[1] > c.sleeps[0] * 0.9      # grows (jittered)

    def test_retry_after_is_honored_but_capped(self):
        c = client(resp(429, headers={"Retry-After": "7"}), resp(429, headers={"Retry-After": "99999"}), resp(200, {}))
        c.get("http://x")
        assert c.sleeps == [7.0, 60.0]

    def test_gives_up_after_max_attempts_with_a_plain_message(self):
        c = client(*[resp(503)] * 3, max_attempts=3)
        with pytest.raises(UpstreamError, match="problem on its side"):
            c.get("http://x")

    def test_persistent_429_is_a_rate_limit_error(self):
        with pytest.raises(RateLimited):
            client(*[resp(429)] * 2, max_attempts=2).get("http://x")

    def test_network_failures_are_retried(self):
        c = client(requests.ConnectionError("down"), resp(200, {"ok": 1}))
        assert c.get("http://x") == {"ok": 1}

    def test_client_errors_are_not_retried_and_say_what_happened(self):
        c = client(resp(400, text="bad field"))
        with pytest.raises(UpstreamError, match="400"):
            c.get("http://x")
        assert c.calls == 1

    def test_a_401_triggers_one_refresh_then_succeeds(self):
        refreshed = []
        c = client(resp(401), resp(200, {"ok": 1}), on_unauthorized=lambda h: refreshed.append(1) or {**h, "Authorization": "new"})
        assert c.get("http://x", headers={"Authorization": "old"}) == {"ok": 1} and refreshed == [1]

    def test_a_second_401_means_reconnect(self):
        c = client(resp(401), resp(401), on_unauthorized=lambda h: h)
        with pytest.raises(AuthError, match="reconnected"):
            c.get("http://x")

    def test_unreadable_responses_are_an_upstream_error(self):
        bad = resp(200); bad.json.side_effect = ValueError("not json")
        with pytest.raises(UpstreamError, match="could not read"):
            client(bad).get("http://x")

    def test_calls_are_paced(self):
        c = client(resp(200, {}), resp(200, {}), min_interval=2.0)
        c.get("http://x"); c.get("http://x")
        assert c.sleeps and c.sleeps[0] > 1.5


# ── Encryption ────────────────────────────────────────────────────────────────

class TestCrypto:
    def test_round_trip_and_ciphertext_differs(self, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k1")
        token = crypto.encrypt("secret-token")
        assert token != "secret-token" and token.startswith("enc:") and crypto.decrypt(token) == "secret-token"

    def test_a_changed_key_is_reported_in_plain_words(self, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k1")
        token = crypto.encrypt("x")
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k2")
        with pytest.raises(crypto.CryptoError, match="Reconnect"):
            crypto.decrypt(token)

    def test_already_encrypted_values_are_not_double_encrypted(self, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k1")
        once = crypto.encrypt("x")
        assert crypto.encrypt(once) == once

    def test_stored_tokens_are_ciphertext_in_the_database(self, app, church, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k1")
        c = FakeStatic(); c.save_token("plaintext-secret", label="acct")
        raw = IntegrationToken.query.filter_by(key="fake_static").one().access_token
        assert "plaintext-secret" not in raw and c.access_token() == "plaintext-secret"


# ── OAuth ─────────────────────────────────────────────────────────────────────

class FakeOAuth(OAuthConnector):
    key = "fake_oauth"
    label = "Fake"
    authorize_url = "https://auth.example/authorize"
    token_url = "https://auth.example/token"
    scopes = ["read", "offline"]
    client_id_env = "FAKE_ID"
    client_secret_env = "FAKE_SECRET"
    interval_minutes = 30

    def sync(self, ctx):
        data = ctx.client.get("https://api.example/things", headers=self.auth_headers())
        for t in data["things"]:
            ctx.store_raw("thing", t["id"], t)
            ctx.note("things", fetched=1, changed=int(ctx.upsert(YoutubeVideo, {"video_id": t["id"]}, {"title": t["name"]})))


class FakeStatic(StaticTokenConnector):
    key = "fake_static"
    label = "Static"

    def sync(self, ctx):
        pass


@pytest.fixture
def oauth(app, church, monkeypatch):
    monkeypatch.setenv("FAKE_ID", "cid"); monkeypatch.setenv("FAKE_SECRET", "csecret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-key")
    return FakeOAuth()


class TestOAuth:
    def test_authorize_url_carries_state_scopes_and_redirect(self, oauth):
        url = oauth.authorize_redirect("STATE123")
        assert "state=STATE123" in url and "scope=read+offline" in url and "client_id=cid" in url
        assert "%2Fintegrations%2Ffake_oauth%2Fcallback" in url

    def test_code_exchange_stores_encrypted_tokens(self, oauth):
        oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
        row = oauth.exchange_code("code", user_id=7)
        assert row.connected_by_id == 7 and "A1" not in row.access_token and crypto.decrypt(row.refresh_token) == "R1"

    def test_an_expiring_token_is_refreshed_and_a_rotated_refresh_token_is_kept(self, oauth):
        calls = []

        def fake(method, url, **kw):
            calls.append(kw["data"]["grant_type"])
            return resp(200, {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600})
        oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 30})
        oauth.exchange_code("c")
        oauth.request_fn = fake
        assert oauth.access_token() == "A2" and calls == ["refresh_token"]
        assert crypto.decrypt(oauth.token_row().refresh_token) == "R2"

    def test_a_refresh_that_returns_no_new_refresh_token_keeps_the_old_one(self, oauth):
        oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 30})
        oauth.exchange_code("c")
        oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A2", "expires_in": 3600})
        oauth.access_token()
        assert crypto.decrypt(oauth.token_row().refresh_token) == "R1"

    def test_a_failed_refresh_asks_for_reconnection(self, oauth):
        oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 30})
        oauth.exchange_code("c")
        oauth.request_fn = lambda m, u, **k: resp(400, text="invalid_grant")
        with pytest.raises(AuthError, match="connecting again"):
            oauth.access_token()

    def test_basic_auth_for_providers_that_require_it(self, oauth):
        oauth.basic_auth_for_token = True
        seen = {}

        def fake(method, url, **kw):
            seen.update(kw)
            return resp(200, {"access_token": "A", "expires_in": 60})
        oauth.request_fn = fake
        oauth.exchange_code("c")
        assert seen["auth"] == ("cid", "csecret") and "client_secret" not in seen["data"]

    def test_not_connected_is_a_clear_error(self, oauth):
        with pytest.raises(AuthError, match="not connected"):
            oauth.access_token()

    def test_disconnect_removes_the_tokens(self, oauth):
        oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A", "expires_in": 60})
        oauth.exchange_code("c"); oauth.disconnect()
        assert not oauth.connected()


# ── Runner: runs, idempotence, errors, health ─────────────────────────────────

def connect(oauth):
    oauth.request_fn = lambda m, u, **k: resp(200, {"access_token": "A", "refresh_token": "R", "expires_in": 7200})
    oauth.exchange_code("c")


def serve(oauth, things):
    oauth.request_fn = lambda m, u, **k: resp(200, {"things": things})


class TestRunner:
    def test_a_good_sync_records_the_run_and_the_data(self, oauth):
        connect(oauth); serve(oauth, [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}])
        run = runner.run_sync(oauth, "manual")
        assert run.status == "ok" and (run.rows_fetched, run.rows_changed) == (2, 2) and run.trigger == "manual"
        assert run.finished_at >= run.started_at
        assert YoutubeVideo.query.count() == 2 and RawPayload.query.count() == 2
        assert Integration.query.filter_by(key="fake_oauth").one().last_success_at is not None
        assert json.loads(run.detail)["resources"]["things"] == {"fetched": 2, "changed": 2}

    def test_syncing_twice_is_idempotent(self, oauth):
        connect(oauth); serve(oauth, [{"id": "a", "name": "A"}])
        runner.run_sync(oauth); second = runner.run_sync(oauth)
        assert second.rows_fetched == 1 and second.rows_changed == 0 and YoutubeVideo.query.count() == 1

    def test_a_changed_upstream_value_updates_in_place(self, oauth):
        connect(oauth); serve(oauth, [{"id": "a", "name": "Old"}]); runner.run_sync(oauth)
        serve(oauth, [{"id": "a", "name": "New"}]); run = runner.run_sync(oauth)
        assert run.rows_changed == 1 and YoutubeVideo.query.one().title == "New" and YoutubeVideo.query.count() == 1

    def test_raw_payloads_are_kept_apart_and_replaced_not_piled_up(self, oauth):
        connect(oauth); serve(oauth, [{"id": "a", "name": "Old"}]); runner.run_sync(oauth)
        serve(oauth, [{"id": "a", "name": "New"}]); runner.run_sync(oauth)
        raw = RawPayload.query.one()
        assert json.loads(raw.payload)["name"] == "New" and raw.integration == "fake_oauth"

    def test_a_failure_is_recorded_in_plain_language_and_leaves_old_data(self, oauth):
        connect(oauth); serve(oauth, [{"id": "a", "name": "A"}]); runner.run_sync(oauth)
        oauth.request_fn = lambda m, u, **k: resp(500)
        oauth.min_interval_seconds = 0
        import connectors.http as h
        run = runner.run_sync(oauth, client=HttpClient("Fake", request_fn=oauth.request_fn, sleep=lambda s: None, max_attempts=2))
        assert run.status == "failed" and "problem on its side" in run.error and run.error_kind == "upstream"
        assert YoutubeVideo.query.count() == 1
        assert runner.health(oauth).status == "failing"

    def test_a_crash_never_leaks_the_traceback_to_staff(self, oauth):
        connect(oauth)
        oauth.sync = lambda ctx: (_ for _ in ()).throw(KeyError("secret internal detail"))
        run = runner.run_sync(oauth)
        assert run.status == "failed" and "secret internal detail" not in run.error and run.error == runner.UNEXPECTED

    def test_a_failed_sync_rolls_back_its_partial_writes(self, oauth):
        connect(oauth)

        def half(ctx):
            ctx.upsert(YoutubeVideo, {"video_id": "x"}, {"title": "half"})
            raise UpstreamError("boom")
        oauth.sync = half
        runner.run_sync(oauth)
        assert YoutubeVideo.query.count() == 0

    def test_warnings_make_a_run_partial_not_failed(self, oauth):
        connect(oauth)
        oauth.sync = lambda ctx: (ctx.note("x", fetched=3, changed=2), ctx.warn("one video had no date"))
        run = runner.run_sync(oauth)
        assert run.status == "partial" and json.loads(run.detail)["warnings"] == ["one video had no date"]
        assert runner.health(oauth).status == "healthy"

    def test_unconfigured_disconnected_and_disabled_connectors_do_not_run(self, oauth, monkeypatch):
        assert runner.run_sync(oauth) is None                          # not connected
        connect(oauth)
        runner.integration_row("fake_oauth").enabled = False; db.session.commit()
        assert runner.run_sync(oauth) is None and SyncRun.query.count() == 0
        runner.integration_row("fake_oauth").enabled = True; db.session.commit()
        monkeypatch.delenv("FAKE_ID")
        assert runner.run_sync(oauth) is None

    def test_runs_are_audited_without_content(self, oauth):
        connect(oauth); serve(oauth, [{"id": "a", "name": "A"}]); runner.run_sync(oauth)
        entry = AuditLog.query.filter_by(action="sync.run").one()
        assert entry.source == "fake_oauth" and json.loads(entry.detail)["fetched"] == 1

    def test_a_waiting_connector_does_not_run(self, oauth):
        connect(oauth)
        oauth.waiting_for_access = lambda: "Waiting for the provider to enable API access."
        assert runner.run_sync(oauth) is None


class TestHealth:
    def test_not_set_up_names_the_missing_settings(self, app, church, monkeypatch):
        monkeypatch.delenv("FAKE_ID", raising=False)
        h = runner.health(FakeOAuth())
        assert h.status == "not_set_up" and "FAKE_ID" in h.detail

    def test_not_connected(self, oauth):
        assert runner.health(oauth).status == "not_set_up" and runner.health(oauth).action == "Connect"

    def test_connected_but_never_synced(self, oauth):
        connect(oauth)
        assert runner.health(oauth).status == "waiting"

    def test_healthy_then_stale(self, oauth):
        connect(oauth); serve(oauth, []); runner.run_sync(oauth)
        assert runner.health(oauth).status == "healthy"
        runner.integration_row("fake_oauth").last_success_at = datetime.utcnow() - timedelta(hours=3); db.session.commit()
        assert runner.health(oauth).status == "stale"

    def test_an_auth_failure_says_reconnect(self, oauth):
        connect(oauth)
        oauth.sync = lambda ctx: (_ for _ in ()).throw(AuthError("Fake did not accept our sign-in. It needs to be reconnected."))
        runner.run_sync(oauth)
        h = runner.health(oauth)
        assert h.status == "needs_reconnect" and h.action == "Reconnect" and "reconnected" in h.detail

    def test_a_good_run_clears_a_previous_error(self, oauth):
        connect(oauth)
        oauth.sync = lambda ctx: (_ for _ in ()).throw(UpstreamError("down"))
        runner.run_sync(oauth)
        oauth.sync = lambda ctx: None
        runner.run_sync(oauth)
        assert runner.health(oauth).status == "healthy" and runner.integration_row("fake_oauth").last_error is None

    def test_off(self, oauth):
        runner.integration_row("fake_oauth").enabled = False; db.session.commit()
        assert runner.health(oauth).status == "off"

    def test_waiting_for_access_is_reported(self, oauth):
        connect(oauth)
        oauth.waiting_for_access = lambda: "Waiting for API access from the provider."
        assert runner.health(oauth).status == "waiting"


class TestContext:
    def test_upsert_reports_change_exactly(self, app, church):
        ctx = SyncContext(FakeOAuth(), MagicMock(), None)
        assert ctx.upsert(YoutubeVideo, {"video_id": "v"}, {"title": "T"}) is True
        db.session.commit()
        assert ctx.upsert(YoutubeVideo, {"video_id": "v"}, {"title": "T"}) is False
        assert ctx.upsert(YoutubeVideo, {"video_id": "v"}, {"title": "T2"}) is True

    def test_warnings_are_capped(self, app, church):
        ctx = SyncContext(FakeOAuth(), MagicMock(), None)
        for i in range(50):
            ctx.warn("w%d" % i)
        assert len(ctx.warnings) == 20

    def test_registry_lists_every_connector_key_once(self):
        from connectors import keys
        assert len(keys()) == len(set(keys())) == 6
