"""The Planning Center connector, using response shapes copied from the real API."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from connectors import runner
from connectors.planning_center import PlanningCenterConnector
from models import (
    PcoCheckinCount, PcoConnection, PcoEpisode, PcoEvent, PcoGroup, PcoServicePlan, PcoSignup,
    RawPayload, db,
)

FUTURE = (datetime.utcnow() + timedelta(days=5)).replace(microsecond=0)


def iso(dt):
    return dt.isoformat() + "Z"


def resp(body, status=200):
    r = MagicMock(); r.status_code = status; r.headers = {}; r.json.return_value = body; r.text = json.dumps(body)
    return r


def event_instance(i=190848221, name="Modern Service", start=FUTURE, tags=("359647", "359642")):
    return {"type": "EventInstance", "id": str(i), "attributes": {
        "all_day_event": False, "ends_at": iso(start + timedelta(hours=1)), "location": "Dalton FUMC - 500 S Thornton Ave",
        "name": name, "starts_at": iso(start), "updated_at": "2025-06-19T16:49:35Z"},
        "relationships": {"event": {"data": {"type": "Event", "id": "17121261"}},
                          "tags": {"data": [{"type": "Tag", "id": t} for t in tags]}}}


CAL = lambda items: {"data": items, "included": [
    {"type": "Event", "id": "17121261", "attributes": {"name": "Modern Service", "updated_at": "2026-07-12T01:08:41Z", "visible_in_church_center": True}},
    {"type": "Tag", "id": "359647", "attributes": {"name": "Services"}},
    {"type": "Tag", "id": "359642", "attributes": {"name": "Modern Service"}}], "links": {}}


class FakePco:
    def __init__(self, calendar=None, fail=None):
        self.calendar = calendar if calendar is not None else [event_instance()]
        self.fail = fail or {}
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append((url, kw.get("params")))
        for fragment, status in self.fail.items():
            if fragment in url:
                return resp({"errors": [{"detail": "nope"}]}, status)
        if "/calendar/v2/event_instances" in url:
            return resp(CAL(self.calendar))
        if url.endswith("/services/v2/service_types"):
            return resp({"data": [{"id": "11", "attributes": {"name": "Modern Worship"}}], "links": {}})
        if "/services/v2/service_types/11/plans" in url:
            if kw["params"]["filter"] == "future":
                return resp({"data": [{"id": "900", "attributes": {"title": None, "series_title": "Neighbors", "sort_date": iso(FUTURE),
                                                                    "plan_people_count": 14, "needed_positions_count": 2}}], "links": {}})
            return resp({"data": [], "links": {}})
        if url.endswith("/groups/v2/groups"):
            return resp({"data": [{"type": "Group", "id": "2724742", "attributes": {"archived_at": None, "memberships_count": 17, "name": "Seasons"},
                                   "relationships": {"group_type": {"data": {"type": "GroupType", "id": "unique"}}}}],
                         "included": [{"type": "GroupType", "id": "unique", "attributes": {"name": "Unique Groups"}}], "links": {}})
        if url.endswith("/check-ins/v2/event_times"):
            return resp({"data": [{"type": "EventTime", "id": "63178440", "attributes": {"guest_count": 3, "name": None, "regular_count": 40,
                                                                                             "starts_at": iso(FUTURE), "total_count": 55, "volunteer_count": 12},
                                   "relationships": {"event": {"data": {"type": "Event", "id": "877194"}}}}],
                         "included": [{"type": "Event", "id": "877194", "attributes": {"name": "Traditional Worship"}}], "links": {}})
        if url.endswith("/publishing/v2/episodes"):
            return resp({"data": [{"id": "77", "attributes": {"title": "WHO IS MY NEIGHBOR?", "published_live_at": iso(FUTURE - timedelta(days=9))}}], "links": {}})
        if "/episode_statistics" in url:
            return resp({"data": [{"attributes": {"views": 321}}]})
        if url.endswith("/registrations/v2/signups"):
            return resp({"data": [{"type": "Signup", "id": "2905496", "attributes": {"archived": False, "close_at": None, "maximum_capacity": 100, "name": "Fall Festival", "open_at": "2026-09-01T12:00:00Z"}}], "links": {}})
        if "/signups/2905496/attendees" in url:
            return resp({"data": [], "meta": {"total_count": 64, "count": 0}})
        raise AssertionError("unexpected " + url)


@pytest.fixture
def pc(app, church, monkeypatch):
    monkeypatch.setenv("PCO_CLIENT_ID", "cid"); monkeypatch.setenv("PCO_CLIENT_SECRET", "cs")
    with patch("pco.PCO_TOKEN_ENCRYPTION_KEY", "k"), patch("connectors.planning_center.PCO_SCOPES", "people calendar services groups check_ins publishing registrations"), \
         patch("pco.PCO_CLIENT_ID", "cid"), patch("pco.PCO_CLIENT_SECRET", "cs"):
        import pco
        db.session.add(PcoConnection(access_token=pco.encrypt_token("tok"), refresh_token=pco.encrypt_token("ref"),
                                     token_expires_at=datetime.utcnow() + timedelta(hours=1),
                                     scope="people calendar services groups check_ins publishing registrations"))
        db.session.commit()
        c = PlanningCenterConnector()
        c.min_interval_seconds = 0
        c.request_fn = FakePco()
        yield c


class TestSync:
    def test_all_six_resources_sync(self, pc):
        run = runner.run_sync(pc, "manual")
        assert run.status == "ok", run.error
        r = json.loads(run.detail)["resources"]
        assert set(r) == {"calendar", "services", "groups", "check_ins", "publishing", "registrations"}
        ev = PcoEvent.query.one()
        assert ev.name == "Modern Service" and ev.tags == "Services,Modern Service" and ev.visible_in_church_center is True
        assert ev.location.startswith("Dalton FUMC") and ev.removed_at is None
        plan = PcoServicePlan.query.one()
        assert (plan.series, plan.positions_total, plan.positions_needed, plan.service_type) == ("Neighbors", 14, 2, "Modern Worship")
        g = PcoGroup.query.one()
        assert (g.name, g.members_count, g.group_type) == ("Seasons", 17, "Unique Groups")
        c = PcoCheckinCount.query.one()
        assert (c.event_name, c.check_ins, c.guests, c.regulars, c.volunteers) == ("Traditional Worship", 55, 3, 40, 12)
        assert PcoEpisode.query.one().views == 321
        s = PcoSignup.query.one()
        assert (s.name, s.capacity, s.attendee_count) == ("Fall Festival", 100, 64)

    def test_syncing_twice_changes_nothing(self, pc):
        runner.run_sync(pc)
        assert runner.run_sync(pc).rows_changed == 0

    def test_a_cancelled_future_event_is_marked_removed_not_deleted(self, pc):
        runner.run_sync(pc)
        pc.request_fn = FakePco(calendar=[])
        runner.run_sync(pc)
        assert PcoEvent.query.one().removed_at is not None
        pc.request_fn = FakePco(calendar=[event_instance()])
        runner.run_sync(pc)
        assert PcoEvent.query.one().removed_at is None            # reinstated

    def test_a_moved_event_updates_in_place(self, pc):
        runner.run_sync(pc)
        later = FUTURE + timedelta(days=2)
        pc.request_fn = FakePco(calendar=[event_instance(start=later)])
        runner.run_sync(pc)
        assert PcoEvent.query.count() == 1 and PcoEvent.query.one().starts_at == later

    def test_raw_payloads_are_kept_separately(self, pc):
        runner.run_sync(pc)
        assert {r.resource for r in RawPayload.query} >= {"event_instance", "plan", "group", "event_time", "episode", "signup"}

    def test_one_resource_failing_does_not_stop_the_others(self, pc):
        pc.request_fn = FakePco(fail={"/publishing/": 403})
        run = runner.run_sync(pc)
        assert run.status == "partial" and "Publishing" in json.loads(run.detail)["warnings"][0]
        assert PcoEvent.query.count() == 1 and PcoGroup.query.count() == 1 and PcoEpisode.query.count() == 0

    def test_missing_scopes_skip_those_resources_with_a_clear_warning(self, pc):
        PcoConnection.query.one().scope = "people calendar groups"
        db.session.commit()
        run = runner.run_sync(pc)
        warnings = json.loads(run.detail)["warnings"]
        assert any("Services skipped" in w for w in warnings) and PcoEvent.query.count() == 1 and PcoServicePlan.query.count() == 0

    def test_a_guest_only_connection_waits_and_explains(self, pc):
        PcoConnection.query.one().scope = "people"
        db.session.commit()
        assert runner.run_sync(pc) is None
        h = runner.health(pc)
        assert h.status == "waiting" and "Reconnect" in h.detail and "calendar" in h.detail

    def test_no_person_data_is_requested_for_checkins_or_registrations(self, pc):
        runner.run_sync(pc)
        urls = [u for u, _ in pc.request_fn.calls]
        assert not any("/people/" in u or "/check_ins?" in u or u.endswith("/check_ins") for u in urls)
        att = [(u, p) for u, p in pc.request_fn.calls if "/attendees" in u]
        assert att and all(p == {"per_page": 1} for _, p in att)          # a count only
        for resource in ("event_time", "signup"):
            blob = " ".join(r.payload for r in RawPayload.query.filter_by(resource=resource))
            assert "first_name" not in blob and "last_name" not in blob

    def test_giving_is_never_requested(self, pc):
        import pco
        assert "giving" not in pco.PCO_SCOPES.split()
        assert not any("/giving/" in u for u, _ in (runner.run_sync(pc) and pc.request_fn.calls))

    def test_pagination_follows_links_next(self, pc):
        pages = [{"data": [event_instance(1)], "included": [], "links": {"next": "https://api.planningcenteronline.com/calendar/v2/event_instances?offset=100"}},
                 {"data": [event_instance(2, start=FUTURE + timedelta(days=1))], "included": [], "links": {}}]
        base = pc.request_fn

        def fake(method, url, **kw):
            if "/calendar/v2/event_instances" in url:
                return resp(pages.pop(0))
            return base(method, url, **kw)
        pc.request_fn = fake
        runner.run_sync(pc)
        assert PcoEvent.query.count() == 2


class TestAuthAndWebhook:
    def test_a_401_triggers_a_token_refresh(self, pc):
        import pco
        calls = {"n": 0}
        base = pc.request_fn

        def fake(method, url, **kw):
            if "/calendar/v2/event_instances" in url:
                calls["n"] += 1
                if calls["n"] == 1:
                    return resp({}, 401)
            return base(method, url, **kw)
        pc.request_fn = fake
        with patch.object(pco, "_refresh_tokens") as refresh:
            run = runner.run_sync(pc)
        assert refresh.called and run.status == "ok"

    def _sig(self, body, secret="s3cret"):
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_a_valid_signature_is_accepted(self, pc):
        body = b'{"data":[]}'
        with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", "s3cret"):
            assert pc.verify_webhook({"X-PCO-Webhooks-Authenticity": self._sig(body)}, body)

    @pytest.mark.parametrize("headers", [{}, {"X-PCO-Webhooks-Authenticity": "deadbeef"}])
    def test_bad_or_missing_signatures_are_rejected(self, pc, headers):
        with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", "s3cret"):
            assert not pc.verify_webhook(headers, b'{"data":[]}')

    def test_without_a_configured_secret_every_webhook_is_rejected(self, pc):
        with patch("connectors.planning_center.PCO_WEBHOOK_SECRET", ""):
            assert not pc.verify_webhook({"X-PCO-Webhooks-Authenticity": self._sig(b"x", "")}, b"x")
