"""The checklist engine: templates, matching, generation, and keeping up with Planning Center."""

from datetime import datetime, timedelta

import pytest

import checklists as C
from models import Checklist, ChecklistTask, ChecklistTemplate, ChecklistTemplateTask, PcoEvent, db

NOW = datetime(2026, 10, 14, 15, 0)          # a Wednesday afternoon, UTC


def event(instance, name, days_ahead, tags="", removed=False):
    ev = PcoEvent(instance_id=instance, name=name, starts_at=NOW + timedelta(days=days_ahead, hours=1), tags=tags,
                  removed_at=NOW if removed else None)
    db.session.add(ev); db.session.commit()
    return ev


@pytest.fixture
def templates(church):
    C.seed_starter_templates()
    return {t.name: t for t in ChecklistTemplate.query}


class TestTemplates:
    def test_four_starter_templates_are_made_once(self, church):
        assert C.seed_starter_templates() == 4
        assert C.seed_starter_templates() == 0 and ChecklistTemplate.query.count() == 4

    def test_a_person_who_deleted_every_template_is_not_given_them_back_by_a_page_load(self, church):
        C.seed_starter_templates()
        ChecklistTemplateTask.query.delete(); ChecklistTemplate.query.delete(); db.session.commit()
        # seeding only happens on first use of an empty install, from the page, never from the engine
        assert C.generate_for_upcoming_events(now=NOW)["created"] == 0 and ChecklistTemplate.query.count() == 0

    def test_matching_looks_in_the_name_and_the_tags_and_ignores_case(self, templates):
        fest = templates["Community event"]
        assert C.template_matches(fest, "Fall FESTIVAL on the lawn") and C.template_matches(fest, "Saturday", "Outreach, Kids")
        assert not C.template_matches(fest, "Choir rehearsal", "Music")

    def test_a_template_without_keywords_matches_nothing(self, church):
        t = ChecklistTemplate(name="Blank", keywords=""); db.session.add(t); db.session.commit()
        assert not C.template_matches(t, "Anything at all", "every tag")

    def test_every_starter_task_uses_a_real_role(self, templates):
        from permissions import ROLES
        for t in ChecklistTemplateTask.query:
            assert t.owner_role in ROLES, t.title


class TestGeneration:
    def test_a_matching_event_gets_a_checklist_with_due_dates_counted_back(self, templates):
        ev = event("i1", "Fall Festival", 60 - 20)           # 40 days away, so nothing is late
        stats = C.generate_for_upcoming_events(now=NOW)
        c = Checklist.query.filter_by(instance_id="i1").one()
        assert stats["created"] == 1 and c.name == "Fall Festival" and c.source == "auto"
        event_day = C.local_date(ev.starts_at)
        by_title = {t.title: t for t in ChecklistTask.query.filter_by(checklist_id=c.id)}
        t = by_title["Order supplies and food"]
        assert t.due_date == event_day - timedelta(days=14) and t.owner_role == "admin_assistant" and not t.catch_up
        assert by_title["Send thank-yous and count attendance"].due_date == event_day + timedelta(days=2)

    def test_running_it_again_never_makes_a_second_checklist(self, templates):
        event("i1", "Fall Festival", 40)
        C.generate_for_upcoming_events(now=NOW)
        again = C.generate_for_upcoming_events(now=NOW)
        assert again["created"] == 0 and Checklist.query.count() == 1
        assert ChecklistTask.query.count() == ChecklistTemplateTask.query.filter_by(template_id=templates["Community event"].id).count()

    def test_events_that_do_not_match_or_are_too_far_off_or_over_get_nothing(self, templates):
        event("a", "Choir rehearsal", 10, tags="Music")
        event("b", "Fall Festival", 120)                      # beyond the horizon
        event("c", "Fall Festival", -3)                       # already happened
        event("d", "Fall Festival", 20, removed=True)         # cancelled in Planning Center
        assert C.generate_for_upcoming_events(now=NOW)["created"] == 0 and Checklist.query.count() == 0

    def test_an_inactive_template_is_ignored(self, templates):
        templates["Community event"].active = False; db.session.commit()
        event("i1", "Fall Festival", 40)
        assert C.generate_for_upcoming_events(now=NOW)["created"] == 0

    def test_one_event_can_match_two_templates(self, templates):
        event("i1", "Youth Fall Festival", 40)
        C.generate_for_upcoming_events(now=NOW)
        assert {c.template_id for c in Checklist.query} == {templates["Community event"].id, templates["Youth or kids event"].id}

    def test_a_cap_stops_a_flood_in_one_pass(self, templates):
        for n in range(10):
            event(f"e{n}", "Fall Festival", 30 + n)
        assert C.generate_for_upcoming_events(now=NOW, max_new=3)["created"] == 3
        assert C.generate_for_upcoming_events(now=NOW, max_new=3)["created"] == 3     # the rest follow on later runs


class TestStayingInStep:
    def test_a_moved_event_moves_open_tasks_but_not_done_or_typed_dates(self, templates):
        ev = event("i1", "Fall Festival", 40)
        C.generate_for_upcoming_events(now=NOW)
        tasks = {t.title: t for t in ChecklistTask.query}
        done = tasks["Order supplies and food"]; done.done_at = NOW; typed = tasks["Recruit and confirm volunteers"]
        typed.due_overridden = True; typed.due_date = C.local_date(ev.starts_at) - timedelta(days=5); typed_date = typed.due_date
        done_date = done.due_date; db.session.commit()
        ev.starts_at = ev.starts_at + timedelta(days=7); db.session.commit()
        stats = C.generate_for_upcoming_events(now=NOW)
        moved_day = C.local_date(ev.starts_at)
        tasks = {t.title: t for t in ChecklistTask.query}
        assert stats["moved"] == 1 and Checklist.query.one().event_date == ev.starts_at
        assert tasks["Walk the space and confirm setup plan"].due_date == moved_day - timedelta(days=3)
        assert tasks["Order supplies and food"].due_date == done_date and tasks["Recruit and confirm volunteers"].due_date == typed_date

    def test_an_event_that_vanishes_from_planning_center_cancels_its_checklist(self, templates):
        ev = event("i1", "Fall Festival", 40)
        C.generate_for_upcoming_events(now=NOW)
        ev.removed_at = NOW; db.session.commit()
        stats = C.generate_for_upcoming_events(now=NOW)
        assert stats["cancelled"] == 1 and Checklist.query.one().status == "cancelled"

    def test_a_past_event_that_is_gone_is_over_not_cancelled(self, templates):
        ev = event("i1", "Fall Festival", 40)
        C.generate_for_upcoming_events(now=NOW)
        ev.removed_at = NOW; db.session.commit()
        C.generate_for_upcoming_events(now=NOW + timedelta(days=60))        # the event date has passed
        assert Checklist.query.one().status == "active"

    def test_a_renamed_event_renames_its_checklist(self, templates):
        ev = event("i1", "Fall Festival", 40)
        C.generate_for_upcoming_events(now=NOW)
        ev.name = "Fall Festival and Chili Cookoff"; db.session.commit()
        assert C.generate_for_upcoming_events(now=NOW)["renamed"] == 1 and "Chili" in Checklist.query.one().name


class TestCatchUp:
    def test_a_late_added_event_is_not_born_overdue(self, templates):
        event("i1", "Fall Festival", 10)                      # the 42, 35, 28, 21 and 14 day tasks are already past
        C.generate_for_upcoming_events(now=NOW)
        today = C.today_local(NOW)
        late = [t for t in ChecklistTask.query if t.catch_up]
        assert len(late) == 5 and all(t.due_date == today for t in late)
        assert not any(t.catch_up for t in ChecklistTask.query if t.days_before < 10)
        # a day later they are still not "overdue" while the event is more than a week off
        rows = C.open_overdue_tasks(today=today + timedelta(days=1))
        assert not any(t.catch_up for t, _ in rows)            # only the task genuinely due today shows as late

    def test_catch_up_tasks_count_as_overdue_once_the_event_is_a_week_away(self, templates):
        ev = event("i1", "Fall Festival", 10)
        C.generate_for_upcoming_events(now=NOW)
        # time passes: it is now 5 days before the event (utcnow is what the overdue query compares with)
        ev_day = C.local_date(ev.starts_at)
        import checklists
        real = checklists.datetime
        class Later(real):
            @classmethod
            def utcnow(cls): return ev.starts_at - timedelta(days=5)
        checklists.datetime = Later
        try:
            rows = C.open_overdue_tasks(today=ev_day - timedelta(days=4))
        finally:
            checklists.datetime = real
        assert rows and all(t.catch_up or t.due_date < ev_day - timedelta(days=4) for t, _ in rows)
        assert any(t.catch_up for t, _ in rows)


class TestWorkingTasks:
    class U:
        def __init__(self, id, roles): self.id, self.roles = id, roles

    def test_who_may_tick_a_task(self, templates):
        event("i1", "Fall Festival", 40); C.generate_for_upcoming_events(now=NOW)
        task = ChecklistTask.query.filter_by(owner_role="comms").first()
        assert C.can_work_task(self.U(1, {"comms"}), task, can_write=False)           # holds the owner role
        assert not C.can_work_task(self.U(2, {"music"}), task, can_write=False)
        assert C.can_work_task(self.U(3, {"music"}), task, can_write=True)            # events.write
        task.assigned_user_id = 2
        assert C.can_work_task(self.U(2, {"music"}), task, can_write=False)           # assigned to them

    def test_ticking_records_who_and_when_and_unticking_clears_it(self, templates):
        event("i1", "Fall Festival", 40); C.generate_for_upcoming_events(now=NOW)
        task = ChecklistTask.query.first()
        C.set_done(task, self.U(7, set()), True)
        assert task.done_at is not None and task.done_by_id == 7
        C.set_done(task, self.U(7, set()), False)
        assert task.done_at is None and task.done_by_id is None
