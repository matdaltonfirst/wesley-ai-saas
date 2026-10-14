"""Checklist pages and API: permissions, ticking, editing, templates."""

from datetime import datetime, timedelta

import pytest

import checklists as C
from models import AuditLog, Checklist, ChecklistTask, ChecklistTemplate, ChecklistTemplateTask, PcoEvent, db
from tests.conftest import login, make_user


@pytest.fixture
def festival(church):
    C.seed_starter_templates()
    ev = PcoEvent(instance_id="i1", name="Fall Festival", starts_at=datetime.utcnow() + timedelta(days=40), tags="Outreach")
    db.session.add(ev); db.session.commit()
    C.generate_for_upcoming_events()
    return Checklist.query.one()


def as_(client, roles, email="x@daltonfumc.com"):
    u = make_user(email, roles); login(client, u); return u


class TestReading:
    def test_the_page_and_list_need_events_read(self, client, festival):
        as_(client, [])
        assert client.get("/checklists").status_code == 403 and client.get("/api/checklists").status_code in (401, 403)

    def test_any_staff_role_can_read_and_sees_progress(self, client, festival):
        as_(client, ["music"])
        data = client.get("/api/checklists").get_json()["checklists"]
        assert data[0]["name"] == "Fall Festival" and data[0]["done"] == 0 and data[0]["total"] == 8 and data[0]["template"] == "Community event"

    def test_visiting_the_page_as_a_manager_seeds_templates_once_but_a_reader_does_not(self, client, church):
        as_(client, ["music"])
        client.get("/checklists")
        assert ChecklistTemplate.query.count() == 0
        client.get("/logout"); as_(client, ["comms"], "c@daltonfumc.com")
        client.get("/checklists"); client.get("/checklists")
        assert ChecklistTemplate.query.count() == 4

    def test_my_tasks_are_those_of_my_role_or_assigned_to_me(self, client, festival):
        u = as_(client, ["music"])
        assert client.get("/api/checklists/mine").get_json()["tasks"] == []
        t = ChecklistTask.query.filter_by(owner_role="comms").first(); t.assigned_user_id = u.id; db.session.commit()
        mine = client.get("/api/checklists/mine").get_json()["tasks"]
        assert [m["title"] for m in mine] == [t.title] and mine[0]["checklist"] == "Fall Festival"
        client.get("/logout"); as_(client, ["comms"], "c@daltonfumc.com")
        assert len(client.get("/api/checklists/mine").get_json()["tasks"]) >= 2


class TestTicking:
    def test_the_owner_role_can_tick_and_untick_and_it_is_audited(self, client, festival):
        as_(client, ["comms"])
        t = ChecklistTask.query.filter_by(owner_role="comms").first()
        r = client.patch(f"/api/checklist-tasks/{t.id}", json={"done": True})
        assert r.status_code == 200 and r.get_json()["task"]["done"] is True and AuditLog.query.filter_by(action="checklist.task_done").count() == 1
        client.patch(f"/api/checklist-tasks/{t.id}", json={"done": False})
        assert ChecklistTask.query.get(t.id).done_at is None

    def test_someone_with_another_role_cannot_tick_it(self, client, festival):
        as_(client, ["music"])
        t = ChecklistTask.query.filter_by(owner_role="comms").first()
        assert client.patch(f"/api/checklist-tasks/{t.id}", json={"done": True}).status_code == 403
        assert ChecklistTask.query.get(t.id).done_at is None

    def test_an_assigned_person_can_tick_a_task_of_another_role(self, client, festival):
        u = as_(client, ["music"])
        t = ChecklistTask.query.filter_by(owner_role="comms").first(); t.assigned_user_id = u.id; db.session.commit()
        assert client.patch(f"/api/checklist-tasks/{t.id}", json={"done": True}).status_code == 200


class TestEditing:
    def test_changing_shape_needs_events_write(self, client, festival):
        as_(client, ["family"])                      # family can read checklists but not manage them
        t = ChecklistTask.query.filter_by(owner_role="family").first()
        assert client.patch(f"/api/checklist-tasks/{t.id}", json={"title": "x"}).status_code == 403
        assert client.delete(f"/api/checklist-tasks/{t.id}").status_code == 403
        assert client.post(f"/api/checklists/{festival.id}/tasks", json={"title": "x"}).status_code == 403
        assert client.post("/api/checklists/refresh").status_code == 403

    def test_a_manager_can_reassign_set_a_date_and_add_and_remove_tasks(self, client, festival):
        u = as_(client, ["comms"])
        t = ChecklistTask.query.first()
        r = client.patch(f"/api/checklist-tasks/{t.id}", json={"assigned_user_id": u.id, "due_date": "2026-12-01", "owner_role": "music"})
        task = r.get_json()["task"]
        assert r.status_code == 200 and task["assigned_user_id"] == u.id and task["due_date"] == "2026-12-01" and task["owner_role"] == "music"
        assert ChecklistTask.query.get(t.id).due_overridden is True
        n = client.post(f"/api/checklists/{festival.id}/tasks", json={"title": "Borrow tables", "owner_role": "admin_assistant", "due_date": "2026-11-20"})
        assert n.status_code == 200 and ChecklistTask.query.filter_by(title="Borrow tables").count() == 1
        assert client.delete(f"/api/checklist-tasks/{n.get_json()['id']}").status_code == 200

    def test_bad_input_is_refused_in_plain_words(self, client, festival):
        as_(client, ["comms"])
        t = ChecklistTask.query.first()
        assert "2026-12-24" in client.patch(f"/api/checklist-tasks/{t.id}", json={"due_date": "soon"}).get_json()["error"]
        assert client.patch(f"/api/checklist-tasks/{t.id}", json={"owner_role": "wizard"}).status_code == 400
        assert client.patch(f"/api/checklist-tasks/{t.id}", json={"assigned_user_id": 99999}).status_code == 400
        assert client.post(f"/api/checklists/{festival.id}/tasks", json={"title": "  "}).status_code == 400

    def test_a_manager_can_make_a_checklist_for_a_calendar_event_or_by_hand_but_not_twice(self, client, festival):
        as_(client, ["comms"])
        youth = ChecklistTemplate.query.filter_by(name="Youth or kids event").one()
        by_hand = client.post("/api/checklists", json={"template_id": youth.id, "name": "Lock-in", "event_date": "2026-12-05"})
        assert by_hand.status_code == 200 and Checklist.query.filter_by(name="Lock-in", source="manual").count() == 1
        db.session.add(PcoEvent(instance_id="i2", name="Choir Retreat", starts_at=datetime.utcnow() + timedelta(days=20))); db.session.commit()
        listed = client.get("/api/checklists/events").get_json()["events"]
        assert [e["instance_id"] for e in listed] == ["i2"]            # i1 already has one
        ok = client.post("/api/checklists", json={"template_id": youth.id, "instance_id": "i2"})
        again = client.post("/api/checklists", json={"template_id": youth.id, "instance_id": "i2"})
        assert ok.status_code == 200 and again.status_code == 409

    def test_cancelling_a_checklist_takes_it_off_the_upcoming_list(self, client, festival):
        as_(client, ["comms"])
        assert client.patch(f"/api/checklists/{festival.id}", json={"status": "cancelled"}).status_code == 200
        assert client.get("/api/checklists").get_json()["checklists"] == []
        assert len(client.get("/api/checklists?scope=all").get_json()["checklists"]) == 1


class TestTemplates:
    def test_any_reader_can_see_them_only_managers_can_change_them(self, client, church):
        C.seed_starter_templates()
        as_(client, ["music"])
        assert len(client.get("/api/checklist-templates").get_json()["templates"]) == 4
        t = ChecklistTemplate.query.first()
        assert client.put(f"/api/checklist-templates/{t.id}", json={"name": "x"}).status_code == 403
        assert client.delete(f"/api/checklist-templates/{t.id}").status_code == 403
        assert client.post("/api/checklist-templates", json={"name": "x"}).status_code == 403

    def test_a_manager_can_edit_replace_tasks_create_and_delete(self, client, church):
        C.seed_starter_templates(); as_(client, ["comms"])
        t = ChecklistTemplate.query.filter_by(name="Community event").one()
        r = client.put(f"/api/checklist-templates/{t.id}", json={"name": "Community event", "keywords": "festival, fair", "active": True,
                       "tasks": [{"title": "Book the lawn", "owner_role": "admin_assistant", "days_before": 30}, {"title": "Thank everyone", "owner_role": "", "days_before": -1}]})
        assert r.status_code == 200
        assert [x.title for x in ChecklistTemplateTask.query.filter_by(template_id=t.id).order_by(ChecklistTemplateTask.position)] == ["Book the lawn", "Thank everyone"]
        made = client.post("/api/checklist-templates", json={"name": "Wedding", "keywords": "wedding", "tasks": [{"title": "Meet the couple", "owner_role": "pastoral", "days_before": 60}]})
        assert made.status_code == 200
        assert client.delete(f"/api/checklist-templates/{made.get_json()['id']}").status_code == 200

    def test_invalid_templates_are_refused(self, client, church):
        C.seed_starter_templates(); as_(client, ["comms"])
        t = ChecklistTemplate.query.first()
        base = {"name": "Ok", "tasks": [{"title": "T", "owner_role": "comms", "days_before": 3}]}
        assert client.put(f"/api/checklist-templates/{t.id}", json=dict(base, name="")).status_code == 400
        assert client.put(f"/api/checklist-templates/{t.id}", json=dict(base, name="Youth or kids event" if t.name != "Youth or kids event" else "Community event")).status_code == 400
        assert client.put(f"/api/checklist-templates/{t.id}", json={"name": "Ok", "tasks": [{"title": "T", "owner_role": "wizard"}]}).status_code == 400
        assert client.put(f"/api/checklist-templates/{t.id}", json={"name": "Ok", "tasks": [{"title": "T", "days_before": "lots"}]}).status_code == 400
        assert client.put(f"/api/checklist-templates/{t.id}", json={"name": "Ok", "tasks": [{"title": "T", "days_before": 9999}]}).status_code == 400
        assert ChecklistTemplateTask.query.filter_by(template_id=t.id).count() > 0          # a refused edit changes nothing

    def test_deleting_a_template_keeps_the_checklists_made_from_it(self, client, festival):
        as_(client, ["comms"])
        t = ChecklistTemplate.query.get(festival.template_id)
        assert client.delete(f"/api/checklist-templates/{t.id}").status_code == 200
        kept = Checklist.query.get(festival.id)
        assert kept is not None and kept.template_id is None and ChecklistTask.query.filter_by(checklist_id=kept.id).count() == 8

    def test_changes_are_audited(self, client, church):
        C.seed_starter_templates(); as_(client, ["comms"])
        client.post("/api/checklist-templates", json={"name": "Wedding", "tasks": []})
        assert AuditLog.query.filter_by(action="checklist.template_create").count() == 1
