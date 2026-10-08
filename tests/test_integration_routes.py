"""The Integrations page, connect flows, sync-now, and webhook intake."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import connectors
from connectors import runner
from models import AuditLog, IntegrationToken, PcoConnection, SyncRun, db
from tests.conftest import login, make_user


class TestPage:
    def test_it_lists_every_connector_with_plain_language_health(self, auth_client):
        data = auth_client.get("/api/integrations").get_json()
        by = {i["key"]: i for i in data["integrations"]}
        assert set(by) == {"planning_center", "youtube", "facebook", "constant_contact", "text_in_church", "subsplash"}
        assert by["subsplash"]["status"] == "manual" and by["text_in_church"]["status"] == "waiting"
        assert by["youtube"]["status"] == "not_set_up"
        for i in by.values():
            assert i["headline"] and isinstance(i["runs"], list)

    def test_an_error_shown_to_staff_is_a_sentence_not_a_traceback(self, auth_client, app):
        runner.integration_row("youtube")
        row = runner.integration_row("youtube"); row.last_error = "YouTube did not accept our sign-in. It needs to be reconnected."
        row.last_error_kind = "auth"; db.session.commit()
        db.session.add(IntegrationToken(key="youtube", access_token="x")); db.session.commit()
        with patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "i", "GOOGLE_CLIENT_SECRET": "s"}):
            by = {i["key"]: i for i in auth_client.get("/api/integrations").get_json()["integrations"]}
        assert by["youtube"]["status"] == "needs_reconnect" and "reconnected" in by["youtube"]["detail"]
        assert "Traceback" not in json.dumps(by["youtube"])

    def test_a_partial_runs_warnings_are_sent_to_the_page(self, auth_client, app):
        db.session.add(SyncRun(integration="facebook", status="partial", trigger="manual", finished_at=datetime.utcnow(),
                               detail=json.dumps({"resources": {}, "warnings": ["Live video insights not available from Meta right now: (#100) bad metric"]})))
        db.session.commit()
        by = {i["key"]: i for i in auth_client.get("/api/integrations").get_json()["integrations"]}
        assert by["facebook"]["runs"][0]["warnings"] == ["Live video insights not available from Meta right now: (#100) bad metric"]
        assert by["youtube"]["runs"] == []

    def test_read_access_without_manage_hides_nothing_but_allows_nothing(self, client, church):
        login(client, make_user("c@daltonfumc.com", ["comms"]))        # comms: integrations.read only
        assert client.get("/integrations").status_code == 200
        assert client.get("/api/integrations").get_json()["can_manage"] is False
        assert client.post("/api/integrations/youtube/sync").status_code == 403
        assert client.post("/api/integrations/subsplash/enabled", json={"enabled": False}).status_code == 403
        assert client.post("/api/integrations/facebook/connect", json={"token": "t", "page_id": "1"}).status_code == 403

    def test_music_cannot_see_integrations(self, client, church):
        login(client, make_user("m@daltonfumc.com", ["music"]))
        assert client.get("/integrations").status_code == 403 and client.get("/api/integrations").status_code == 403


class TestConnect:
    def test_the_oauth_redirect_carries_state_and_the_callback_checks_it(self, auth_client, monkeypatch):
        monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_ID", "id"); monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_SECRET", "s")
        r = auth_client.get("/integrations/constant_contact/connect")
        assert r.status_code == 302 and "authz.constantcontact.com" in r.headers["Location"]
        bad = auth_client.get("/integrations/constant_contact/callback?state=forged&code=x")
        assert "error=state" in bad.headers["Location"] and IntegrationToken.query.count() == 0

    def test_a_full_connect_round_trip_stores_encrypted_tokens_and_is_audited(self, auth_client, monkeypatch):
        monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_ID", "id"); monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_SECRET", "s")
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        loc = auth_client.get("/integrations/constant_contact/connect").headers["Location"]
        state = loc.split("state=")[1].split("&")[0]
        c = connectors.get("constant_contact")
        c.request_fn = lambda m, u, **k: MagicMock(status_code=200, headers={}, json=lambda: {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}, text="")
        try:
            r = auth_client.get(f"/integrations/constant_contact/callback?state={state}&code=abc")
        finally:
            c.request_fn = None
        assert "connected=constant_contact" in r.headers["Location"]
        assert "AT" not in IntegrationToken.query.one().access_token
        assert AuditLog.query.filter_by(action="integration.connect", source="constant_contact").count() == 1

    def test_a_denied_consent_connects_nothing(self, auth_client, monkeypatch):
        monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_ID", "id"); monkeypatch.setenv("CONSTANT_CONTACT_CLIENT_SECRET", "s")
        state = auth_client.get("/integrations/constant_contact/connect").headers["Location"].split("state=")[1].split("&")[0]
        r = auth_client.get(f"/integrations/constant_contact/callback?state={state}&error=access_denied")
        assert "error=denied" in r.headers["Location"] and IntegrationToken.query.count() == 0

    def test_an_unconfigured_connector_explains_what_is_missing(self, auth_client, monkeypatch):
        monkeypatch.delenv("CONSTANT_CONTACT_CLIENT_ID", raising=False)
        r = auth_client.get("/integrations/constant_contact/connect")
        assert r.status_code == 400 and "CONSTANT_CONTACT_CLIENT_ID" in r.get_json()["error"]

    def test_facebook_token_and_ids_are_validated_and_stored_encrypted(self, auth_client, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        assert auth_client.post("/api/integrations/facebook/connect", json={"token": "t", "page_id": "not-numeric"}).status_code == 400
        ok = auth_client.post("/api/integrations/facebook/connect", json={"token": "EAAB-secret", "page_id": "123", "ig_user_id": "456"})
        assert ok.status_code == 200
        assert "EAAB-secret" not in IntegrationToken.query.filter_by(key="facebook").one().access_token
        assert runner.config_of("facebook") == {"page_id": "123", "ig_user_id": "456"}

    def test_the_text_in_church_key_is_stored_encrypted_and_never_returned(self, auth_client, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        assert auth_client.post("/api/integrations/text_in_church/key", json={"api_key": "short"}).status_code == 400
        assert auth_client.post("/api/integrations/text_in_church/key", json={"api_key": "tic-key-0123456789"}).status_code == 200
        assert "tic-key-0123456789" not in json.dumps(auth_client.get("/api/integrations").get_json())

    def test_disconnect_and_turn_off(self, auth_client, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
        auth_client.post("/api/integrations/facebook/connect", json={"token": "tok-12345", "page_id": "1"})
        assert auth_client.post("/api/integrations/facebook/disconnect").status_code == 200
        assert IntegrationToken.query.count() == 0
        assert auth_client.post("/api/integrations/youtube/enabled", json={"enabled": False}).status_code == 200
        assert runner.health(connectors.get("youtube")).status == "off"
        assert auth_client.post("/api/integrations/youtube/enabled", json={"enabled": "no"}).status_code == 400

    def test_unknown_integrations_are_a_404(self, auth_client):
        assert auth_client.post("/api/integrations/nope/sync").status_code == 404
        assert auth_client.get("/integrations/nope/connect").status_code == 404


class TestSyncNow:
    def test_an_unready_integration_says_why_instead_of_pretending(self, auth_client):
        r = auth_client.post("/api/integrations/youtube/sync")
        assert r.status_code == 409 and r.get_json()["status"] == "not_set_up"

    def test_sync_now_runs_and_records_a_manual_run(self, auth_client, monkeypatch):
        monkeypatch.setenv("TEXT_IN_CHURCH_MOCK", "1")
        r = auth_client.post("/api/integrations/text_in_church/sync")
        assert r.status_code == 200 and r.get_json()["status"] == "ok"
        assert SyncRun.query.one().trigger == "manual"


class TestWebhooks:
    def _pco_sig(self, body, secret):
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_an_unsigned_or_wrongly_signed_webhook_is_refused_and_logged(self, client, church):
        with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", "s3"):
            assert client.post("/webhooks/planning_center", data=b"{}").status_code == 401
            assert client.post("/webhooks/planning_center", data=b"{}", headers={"X-PCO-Webhooks-Authenticity": "bad"}).status_code == 401
        assert AuditLog.query.filter_by(action="webhook.rejected").count() == 2

    def test_a_signed_webhook_triggers_a_sync_and_never_trusts_the_payload(self, client, church, monkeypatch):
        monkeypatch.setenv("PCO_CLIENT_ID", "i"); monkeypatch.setenv("PCO_CLIENT_SECRET", "s")
        db.session.add(PcoConnection(access_token="a", refresh_token="r", token_expires_at=datetime.utcnow() + timedelta(hours=1),
                                      scope="people calendar services groups check_ins publishing registrations"))
        db.session.commit()
        pc = connectors.get("planning_center")
        called = []
        body = json.dumps({"data": [{"attributes": {"name": "calendar.v2.events.event_instance.created", "payload": "{\"evil\": \"data\"}"}}]}).encode()
        with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", "s3"), patch.object(type(pc), "sync", lambda self, ctx: called.append(1)):
            r = client.post("/webhooks/planning_center", data=body, headers={"X-PCO-Webhooks-Authenticity": self._pco_sig(body, "s3")})
        assert r.status_code == 200 and called == [1]
        assert SyncRun.query.one().trigger == "webhook"

    def test_connectors_without_webhooks_and_unknown_ones_are_404(self, client, church):
        assert client.post("/webhooks/subsplash", data=b"{}").status_code == 404
        assert client.post("/webhooks/nope", data=b"{}").status_code == 404

    def test_oversized_bodies_are_refused(self, client, church):
        with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", "s3"):
            assert client.post("/webhooks/planning_center", data=b"x" * 1_200_000).status_code == 401

    def test_webhooks_need_no_login_and_no_csrf_token(self, app, client, church):
        app.config["FORCE_CSRF"] = True
        try:
            with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", "s3"):
                assert client.post("/webhooks/planning_center", data=b"{}").status_code == 401   # refused for the signature, not for CSRF
        finally:
            app.config["FORCE_CSRF"] = False
