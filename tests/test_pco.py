"""Tests for the Planning Center integration (pco.py + routes)."""

from datetime import datetime, timedelta
from unittest.mock import patch

from models import db, PcoConnection, GuestConnection
import pco


def _connect_church(church, **overrides):
    conn = PcoConnection(
        church_id=church.id,
        access_token="tok",
        refresh_token="ref",
        token_expires_at=datetime.utcnow() + timedelta(hours=1),
        organization_name="Grace Community Church",
        **overrides,
    )
    db.session.add(conn)
    db.session.commit()
    return conn


def _cleanup(church):
    PcoConnection.query.filter_by(church_id=church.id).delete()
    GuestConnection.query.filter_by(church_id=church.id).delete()
    db.session.commit()


class FakePcoApi:
    """Routes pco.api requests to canned JSON responses and records calls."""

    def __init__(self, responses):
        self.responses = responses  # {(method, path): json}
        self.calls = []

    def __call__(self, method, url, headers=None, json=None, params=None, timeout=None):
        path = url.replace(pco.PCO_API_BASE, "")
        self.calls.append((method, path, json, params))
        body = self.responses.get((method, path), {})

        class R:
            status_code = 200 if (method, path) in self.responses else 404
            content = b"{}"

            def json(self_inner):
                return body if isinstance(body, dict) else {}
        return R()


class TestStatusAndSettings:
    def test_status_requires_auth(self, client):
        assert client.get("/api/pco/status").status_code == 401

    def test_status_unconfigured(self, auth_client):
        res = auth_client.get("/api/pco/status")
        assert res.status_code == 200
        assert res.get_json() == {"configured": False, "connected": False}

    def test_status_connected(self, auth_client, church):
        _connect_church(church, workflow_id="42", workflow_name="Website Leads")
        with patch("pco.PCO_CLIENT_ID", "cid"), patch("pco.PCO_CLIENT_SECRET", "sec"):
            res = auth_client.get("/api/pco/status")
        data = res.get_json()
        assert data["connected"] is True
        assert data["organization_name"] == "Grace Community Church"
        assert data["workflow_name"] == "Website Leads"
        _cleanup(church)

    def test_connect_unconfigured_rejected(self, auth_client):
        assert auth_client.get("/pco/connect").status_code == 400

    def test_connect_redirects_to_pco(self, auth_client):
        with patch("pco.PCO_CLIENT_ID", "cid"), patch("pco.PCO_CLIENT_SECRET", "sec"):
            res = auth_client.get("/pco/connect")
        assert res.status_code == 302
        assert res.headers["Location"].startswith(
            "https://api.planningcenteronline.com/oauth/authorize"
        )
        assert "scope=people" in res.headers["Location"]

    def test_callback_rejects_bad_state(self, auth_client):
        res = auth_client.get("/pco/callback?code=abc&state=forged")
        assert res.status_code == 400

    def test_settings_update(self, auth_client, church):
        _connect_church(church)
        res = auth_client.post("/api/pco/settings", json={
            "auto_sync": False, "workflow_id": "7", "workflow_name": "Follow Up",
        })
        assert res.status_code == 200
        conn = PcoConnection.query.filter_by(church_id=church.id).one()
        assert conn.auto_sync is False
        assert conn.workflow_id == "7"
        _cleanup(church)

    def test_disconnect(self, auth_client, church):
        _connect_church(church)
        assert auth_client.post("/api/pco/disconnect").status_code == 200
        assert PcoConnection.query.filter_by(church_id=church.id).first() is None
        _cleanup(church)


class TestSyncGuestConnection:
    def _guest(self, church, email="pat@example.org"):
        gc = GuestConnection(
            church_id=church.id, name="Pat Q Visitor", email=email,
            phone="555-0100", interest_area="New to Church",
            opening_message="Do you have a young adults group?",
        )
        db.session.add(gc)
        db.session.commit()
        return gc

    def test_existing_person_found_by_email(self, app, church):
        _connect_church(church)
        gc = self._guest(church)
        fake = FakePcoApi({
            ("GET", "/people/v2/emails"): {"data": [
                {"relationships": {"person": {"data": {"type": "Person", "id": "999"}}}}
            ]},
            ("GET", "/people/v2/note_categories"): {"data": [
                {"id": "5", "attributes": {"name": "Wesley AI"}}
            ]},
            ("POST", "/people/v2/people/999/notes"): {"data": {"id": "n1"}},
        })
        with patch("pco.requests.request", fake):
            assert pco.sync_guest_connection(gc) is True
        assert gc.pco_person_id == "999"
        assert gc.pco_sync_error is None
        # No person creation happened
        assert ("POST", "/people/v2/people", None, None) not in [
            (c[0], c[1], None, None) for c in fake.calls if c[1] == "/people/v2/people"
        ]
        _cleanup(church)

    def test_new_person_created_with_contact_note_and_workflow(self, app, church):
        _connect_church(church, workflow_id="42")
        gc = self._guest(church, email="new@example.org")
        fake = FakePcoApi({
            ("GET", "/people/v2/emails"): {"data": []},
            ("POST", "/people/v2/people"): {"data": {"id": "1234"}},
            ("POST", "/people/v2/people/1234/emails"): {"data": {"id": "e1"}},
            ("POST", "/people/v2/people/1234/phone_numbers"): {"data": {"id": "p1"}},
            ("GET", "/people/v2/note_categories"): {"data": []},
            ("POST", "/people/v2/note_categories"): {"data": {"id": "9"}},
            ("POST", "/people/v2/people/1234/notes"): {"data": {"id": "n1"}},
            ("POST", "/people/v2/workflows/42/cards"): {"data": {"id": "c1"}},
        })
        with patch("pco.requests.request", fake):
            assert pco.sync_guest_connection(gc) is True

        assert gc.pco_person_id == "1234"
        paths = [c[1] for c in fake.calls]
        assert "/people/v2/people/1234/emails" in paths
        assert "/people/v2/people/1234/phone_numbers" in paths
        assert "/people/v2/workflows/42/cards" in paths
        person_call = next(c for c in fake.calls if c[1] == "/people/v2/people")
        assert person_call[2]["data"]["attributes"] == {
            "first_name": "Pat", "last_name": "Q Visitor",
        }
        note_call = next(c for c in fake.calls if c[1] == "/people/v2/people/1234/notes")
        assert "young adults group" in note_call[2]["data"]["attributes"]["note"]
        _cleanup(church)

    def test_failure_recorded_for_retry(self, app, church):
        _connect_church(church)
        gc = self._guest(church)
        fake = FakePcoApi({})  # every call 404s
        with patch("pco.requests.request", fake):
            assert pco.sync_guest_connection(gc) is False
        assert gc.pco_person_id is None
        assert gc.pco_sync_error
        _cleanup(church)

    def test_manual_sync_route(self, auth_client, church):
        _connect_church(church)
        gc = self._guest(church)
        fake = FakePcoApi({
            ("GET", "/people/v2/emails"): {"data": [
                {"relationships": {"person": {"data": {"type": "Person", "id": "77"}}}}
            ]},
            ("GET", "/people/v2/note_categories"): {"data": [
                {"id": "5", "attributes": {"name": "Wesley AI"}}
            ]},
            ("POST", "/people/v2/people/77/notes"): {"data": {"id": "n1"}},
        })
        with patch("pco.requests.request", fake):
            res = auth_client.post(f"/api/guest-connection/{gc.id}/sync-pco")
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        assert data["pco_person_id"] == "77"
        assert "planningcenteronline.com" in data["pco_url"]
        _cleanup(church)

    def test_no_connection_returns_error(self, auth_client, church):
        gc = self._guest(church)
        res = auth_client.post(f"/api/guest-connection/{gc.id}/sync-pco")
        assert res.status_code == 400
        _cleanup(church)
