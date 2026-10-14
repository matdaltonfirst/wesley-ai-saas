"""The Needs attention list: what shows, to whom, and that it never repeats itself."""

import json
from datetime import date, datetime, timedelta

import pytest

import attention as A
import checklists as C
from connectors import runner
from models import (
    AttentionSnooze, Checklist, ChecklistTask, GuestConnection, IntegrationToken, PcoEvent, Sermon, SermonPacket,
    SermonSource, StreamingNumber, db,
)
from tests.conftest import login, make_user

NOW = datetime(2026, 10, 14, 15, 0)


def keys(user, today=None):
    return [a["key"] for a in A.alerts_for(user, today)]


@pytest.fixture
def comms(church):
    return make_user("c@daltonfumc.com", ["comms"])


class TestConnectorsAlerts:
    def test_a_broken_connection_shows_once_with_what_to_do(self, church, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k"); monkeypatch.setenv("GOOGLE_CLIENT_ID", "i"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")
        db.session.add(IntegrationToken(key="youtube", access_token="x"))
        row = runner.integration_row("youtube"); row.last_error = "YouTube did not accept our sign-in. It needs to be reconnected."; row.last_error_kind = "auth"
        db.session.commit()
        admin = make_user("a@daltonfumc.com", ["admin"])
        found = [a for a in A.alerts_for(admin) if a["key"].startswith("connector:youtube")]
        assert len(found) == 1 and found[0]["severity"] == "high" and found[0]["link"] == "/integrations" and "reconnected" in found[0]["detail"]
        assert len([a for a in A.alerts_for(admin) if a["key"].startswith("connector:youtube")]) == 1      # same alert, not a new one

    def test_people_without_integration_access_do_not_see_them(self, church, monkeypatch):
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k"); monkeypatch.setenv("GOOGLE_CLIENT_ID", "i"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")
        db.session.add(IntegrationToken(key="youtube", access_token="x"))
        row = runner.integration_row("youtube"); row.last_error = "x"; row.last_error_kind = "auth"; db.session.commit()
        assert not [k for k in keys(make_user("m@daltonfumc.com", ["music"])) if k.startswith("connector")]


class TestTaskAlerts:
    def make(self, owner_role="comms", due_offset=-3, catch_up=False):
        c = Checklist(name="Fall Festival", event_date=datetime.utcnow() + timedelta(days=3), status="active"); db.session.add(c); db.session.flush()
        today = C.today_local()
        t = ChecklistTask(checklist_id=c.id, title="Publish the event page", owner_role=owner_role, due_date=today + timedelta(days=due_offset), catch_up=catch_up)
        db.session.add(t); db.session.commit()
        return c, t

    def test_the_person_whose_role_owns_an_overdue_task_sees_it(self, comms):
        c, _ = self.make()
        found = [a for a in A.alerts_for(comms) if a["key"] == f"checklist:{c.id}:overdue"]
        assert found and "Publish the event page" in found[0]["detail"] and found[0]["link"] == f"/checklists#{c.id}"

    def test_someone_with_another_role_does_not(self, church):
        c, _ = self.make()
        assert f"checklist:{c.id}:overdue" not in keys(make_user("m@daltonfumc.com", ["music"]))

    def test_an_events_manager_sees_every_overdue_checklist(self, church):
        c, _ = self.make(owner_role="music")
        assert f"checklist:{c.id}:overdue" in keys(make_user("a@daltonfumc.com", ["admin"]))

    def test_a_done_task_is_not_an_alert(self, comms):
        c, t = self.make(); t.done_at = datetime.utcnow(); db.session.commit()
        assert f"checklist:{c.id}:overdue" not in keys(comms)

    def test_several_late_tasks_on_one_checklist_are_one_alert(self, comms):
        c, _ = self.make()
        for n in range(3):
            db.session.add(ChecklistTask(checklist_id=c.id, title=f"T{n}", owner_role="comms", due_date=C.today_local() - timedelta(days=5))); db.session.commit()
        found = [a for a in A.alerts_for(comms) if a["key"].startswith(f"checklist:{c.id}")]
        assert len(found) == 1 and "4 overdue tasks" in found[0]["title"] and found[0]["severity"] == "high"


class TestReviewAlerts:
    def packet(self):
        src = SermonSource(channel_url="u", channel_id="c"); db.session.add(src); db.session.flush()
        s = Sermon(source_id=src.id, video_id="v", title="Is God too strict?", published_at=datetime.utcnow() - timedelta(days=2), status="ingested", transcript="t")
        db.session.add(s); db.session.flush()
        p = SermonPacket(sermon_id=s.id, status="ready", content=json.dumps({"titles": ["A?"], "description": "d", "social": [{"platform": "facebook", "body": "b"}]}))
        db.session.add(p); db.session.commit(); return p

    def test_approvers_are_told_a_sermon_is_waiting(self, church):
        p = self.packet()
        found = [a for a in A.alerts_for(make_user("p@daltonfumc.com", ["pastoral"])) if a["key"] == f"review:{p.id}:waiting"]
        assert found and "0 of 2 pieces approved" in found[0]["detail"]

    def test_people_who_cannot_approve_are_not_nagged_about_waiting_ones(self, church):
        p = self.packet()
        assert f"review:{p.id}:waiting" not in keys(make_user("a@daltonfumc.com", ["admin_assistant"]))

    def test_the_alert_goes_away_once_everything_is_approved(self, church):
        import review
        p = self.packet(); u = make_user("p@daltonfumc.com", ["pastoral"])
        for piece in review.ensure_reviews(p):
            review.decide(p, piece, "approved", u)
        assert f"review:{p.id}:waiting" not in keys(u)

    def test_editors_hear_about_requested_changes_with_the_reason(self, church):
        import review
        p = self.packet(); reviewer = make_user("p@daltonfumc.com", ["pastoral"]); editor = make_user("c@daltonfumc.com", ["comms"])
        review.ensure_reviews(p); review.decide(p, "social", "needs_changes", reviewer, "Too casual for Facebook.")
        found = [a for a in A.alerts_for(editor) if a["key"] == f"review:{p.id}:changes"]
        assert found and "Too casual" in found[0]["detail"]


class TestStreamingAndGuests:
    def test_missing_numbers_for_last_sunday_are_flagged_from_monday(self, church):
        assistant = make_user("a@daltonfumc.com", ["admin_assistant"])
        monday = date(2026, 10, 12)
        assert "streaming:2026-10-11" in keys(assistant, monday)

    def test_it_is_quiet_on_sunday_itself_and_once_numbers_exist(self, church):
        assistant = make_user("a@daltonfumc.com", ["admin_assistant"])
        assert not [k for k in keys(assistant, date(2026, 10, 11)) if k.startswith("streaming")]
        import streaming
        streaming.save_number(date(2026, 10, 11), "Modern service", "youtube", {"total_views": 12}, "api", by="YouTube sync")
        assert not [k for k in keys(assistant, date(2026, 10, 12)) if k.startswith("streaming")]

    def test_only_people_who_enter_numbers_are_told(self, church):
        assert not [k for k in keys(make_user("m@daltonfumc.com", ["music"]), date(2026, 10, 12)) if k.startswith("streaming")]

    def test_guests_waiting_more_than_two_days_are_one_alert_with_a_count(self, church):
        for n in range(2):
            db.session.add(GuestConnection(name=f"G{n}", email=f"g{n}@x.com", status="new", created_at=datetime.utcnow() - timedelta(days=4)))
        db.session.add(GuestConnection(name="Fresh", email="f@x.com", status="new", created_at=datetime.utcnow()))
        db.session.add(GuestConnection(name="Done", email="d@x.com", status="contacted", created_at=datetime.utcnow() - timedelta(days=9)))
        db.session.commit()
        found = [a for a in A.alerts_for(make_user("a@daltonfumc.com", ["admin_assistant"])) if a["key"] == "guests:waiting"]
        assert len(found) == 1 and found[0]["title"].startswith("2 guest connections")


class TestSnoozeAndOrder:
    def test_snoozing_hides_one_alert_for_that_person_only(self, church):
        a, b = make_user("a@daltonfumc.com", ["admin_assistant"]), make_user("b@daltonfumc.com", ["admin_assistant"])
        db.session.add(GuestConnection(name="G", email="g@x.com", status="new", created_at=datetime.utcnow() - timedelta(days=4))); db.session.commit()
        A.snooze(a, "guests:waiting", 3)
        assert "guests:waiting" not in keys(a) and "guests:waiting" in keys(b)

    def test_a_snooze_ends(self, church):
        a = make_user("a@daltonfumc.com", ["admin_assistant"])
        db.session.add(GuestConnection(name="G", email="g@x.com", status="new", created_at=datetime.utcnow() - timedelta(days=4))); db.session.commit()
        A.snooze(a, "guests:waiting", 3)
        AttentionSnooze.query.update({"until": datetime.utcnow() - timedelta(minutes=1)}); db.session.commit()
        assert "guests:waiting" in keys(a)

    def test_snoozing_is_capped_and_repeating_it_extends_instead_of_duplicating(self, church):
        a = make_user("a@daltonfumc.com", ["admin_assistant"])
        A.snooze(a, "x", 9999); A.snooze(a, "x", 1)
        assert AttentionSnooze.query.count() == 1 and AttentionSnooze.query.one().until < datetime.utcnow() + timedelta(days=2)

    def test_most_urgent_first(self, church):
        admin = make_user("a@daltonfumc.com", ["admin"])
        for n in range(6):
            db.session.add(GuestConnection(name=f"G{n}", email=f"g{n}@x.com", status="new", created_at=datetime.utcnow() - timedelta(days=4)))
        db.session.commit()
        sev = [a["severity"] for a in A.alerts_for(admin, date(2026, 10, 12))]
        assert sev == sorted(sev, key=lambda s: A.SEVERITY_ORDER[s]) and sev[0] == "high"

    def test_the_api_returns_only_my_alerts_and_snoozes_through_the_api(self, client, church):
        login(client, make_user("a@daltonfumc.com", ["admin_assistant"]))
        db.session.add(GuestConnection(name="G", email="g@x.com", status="new", created_at=datetime.utcnow() - timedelta(days=4))); db.session.commit()
        assert any(a["key"] == "guests:waiting" for a in client.get("/api/attention").get_json()["alerts"])
        assert client.post("/api/attention/snooze", json={"key": "guests:waiting", "days": 2}).status_code == 200
        assert not any(a["key"] == "guests:waiting" for a in client.get("/api/attention").get_json()["alerts"])
        assert client.post("/api/attention/snooze", json={}).status_code == 400

    def test_a_person_with_no_role_gets_nothing(self, client, church):
        login(client, make_user("n@daltonfumc.com", []))
        assert client.get("/api/attention").status_code == 403
