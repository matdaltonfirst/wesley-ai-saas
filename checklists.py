"""Event checklists: templates in, one checklist per event out.

How it works, in plain terms:

* A **template** is a named list of tasks for one kind of event ("Community event"). Each task
  has an owner role and a number of days before the event it is due.
* A template matches an event when any of its **keywords** appears in the event's name or tags.
* ``generate_for_upcoming_events`` looks at Planning Center events in the next few weeks and makes
  one checklist per (event, template). It is safe to run as often as you like: running it twice
  never makes a second checklist, an event that moves moves its open tasks' due dates with it, and
  an event that disappears from Planning Center cancels its checklist.
* Tasks a person has touched are respected: a done task stays done, and a due date someone typed
  is never overwritten by a moved event.

Nothing here sends anything. A checklist is a list people work through.
"""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from models import (
    Checklist, ChecklistTask, ChecklistTemplate, ChecklistTemplateTask, PcoEvent, db,
)
from organization import get_org

log = logging.getLogger("wesley")

HORIZON_DAYS = 60            # only events this far ahead get a checklist
MAX_NEW_PER_RUN = 25         # a runaway template cannot flood the page in one pass
PAST_GRACE_DAYS = 7          # open tasks keep showing for a week after the event

# Starter templates. A person edits these on the Checklists page; they are a place to start from,
# not a statement of how Dalton First does things. They are only created when there are none.
STARTER_TEMPLATES = [
    {
        "name": "Community event",
        "description": "Festivals, outreach days, open houses and anything the public is invited to.",
        "keywords": "festival, community, outreach, open house, fair, trunk or treat, block party",
        "tasks": [
            ("Confirm date, time and space on the church calendar", "admin_assistant", 42, ""),
            ("Send the communications request (flyer, graphic, video)", "admin", 35, "Use the request form so it lands in the queue."),
            ("Recruit and confirm volunteers", "family", 28, ""),
            ("Publish the event page and Facebook event", "comms", 21, "Draft first; a person publishes."),
            ("Order supplies and food", "admin_assistant", 14, ""),
            ("Put it in the bulletin and announcements", "comms", 10, ""),
            ("Walk the space and confirm setup plan", "admin", 3, ""),
            ("Send thank-yous and count attendance", "admin_assistant", -2, "Record the number in Planning Center."),
        ],
    },
    {
        "name": "Youth or kids event",
        "description": "Events for children and students, including camps and VBS.",
        "keywords": "youth, kids, children, student, vbs, camp, nursery, sunday school",
        "tasks": [
            ("Confirm date, space and leaders", "family", 42, ""),
            ("Confirm safe-sanctuary requirements are met for every leader", "family", 35, "Training and background checks are tracked in Planning Center, not here."),
            ("Open registration and set capacity", "family", 28, ""),
            ("Send the communications request", "family", 28, ""),
            ("Send the family announcement draft for approval", "comms", 14, ""),
            ("Collect permission slips and medical forms", "family", 7, ""),
            ("Confirm supplies, snacks and allergies plan", "family", 3, ""),
            ("Follow up with families and note attendance", "family", -2, ""),
        ],
    },
    {
        "name": "Special worship service",
        "description": "Christmas Eve, Easter, Ash Wednesday, homecoming and similar.",
        "keywords": "christmas eve, easter, ash wednesday, good friday, maundy, advent, homecoming, candlelight, baptism, confirmation",
        "tasks": [
            ("Choose the service plan and book musicians", "music", 42, ""),
            ("Reserve the sanctuary and confirm no calendar conflicts", "admin_assistant", 35, ""),
            ("Send the communications request (graphics, slides, video)", "comms", 28, ""),
            ("Write the order of service and assign liturgists", "pastoral", 21, ""),
            ("Schedule volunteers (ushers, greeters, sound, livestream)", "admin_assistant", 14, ""),
            ("Draft the invitation post and email for approval", "comms", 14, ""),
            ("Rehearsal and tech check", "music", 3, ""),
            ("Print bulletins and confirm the livestream plan", "admin_assistant", 2, ""),
            ("Record the numbers and thank the team", "admin_assistant", -2, "Enter streaming numbers on the Streaming page."),
        ],
    },
    {
        "name": "Dinner or fundraiser",
        "description": "Dinners, sales, banquets and any event that raises money.",
        "keywords": "dinner, fundraiser, banquet, bake sale, sale, benefit, auction, supper",
        "tasks": [
            ("Confirm date, space and the purpose of the funds", "admin", 42, ""),
            ("Set up how money will be collected and counted", "admin_assistant", 35, "Giving details are handled by the finance team, not in this app."),
            ("Recruit cooks, servers and set-up crew", "admin_assistant", 28, ""),
            ("Send the communications request (tickets, flyer, signup)", "comms", 28, ""),
            ("Open signup or ticket sales", "admin_assistant", 21, ""),
            ("Order food and supplies", "admin_assistant", 10, ""),
            ("Confirm headcount with the kitchen", "admin_assistant", 3, ""),
            ("Count proceeds, thank volunteers and report results", "admin", -3, ""),
        ],
    },
]


# ── Templates ────────────────────────────────────────────────────────────────

def seed_starter_templates() -> int:
    """Create the starter templates if there are none. Returns how many were made."""
    if ChecklistTemplate.query.count():
        return 0
    for spec in STARTER_TEMPLATES:
        t = ChecklistTemplate(name=spec["name"], description=spec["description"], keywords=spec["keywords"], active=True)
        db.session.add(t)
        db.session.flush()
        for pos, (title, role, days, notes) in enumerate(spec["tasks"]):
            db.session.add(ChecklistTemplateTask(template_id=t.id, position=pos, title=title, owner_role=role,
                                                 days_before=days, notes=notes or None))
    db.session.commit()
    return len(STARTER_TEMPLATES)


def keywords_of(template) -> list:
    return [k.strip().lower() for k in (template.keywords or "").split(",") if k.strip()]


def template_matches(template, name: str, tags: str = "") -> bool:
    """True when a keyword appears in the event's name or tags. A template with no keywords matches nothing."""
    haystack = f"{name or ''} {tags or ''}".lower()
    return any(k in haystack for k in keywords_of(template))


def local_date(dt: datetime) -> date:
    """The church's calendar date for a stored (naive UTC) moment."""
    try:
        zone = ZoneInfo(get_org().timezone or "America/New_York")
    except Exception:
        zone = ZoneInfo("America/New_York")
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone).date()


def today_local(now: datetime = None) -> date:
    return local_date(now or datetime.utcnow())


# ── Building checklists ──────────────────────────────────────────────────────

def _add_tasks(checklist, template, event_date, today: date = None):
    """Copy the template's tasks. A task whose natural due date has already passed (the event was added
    late) is due today and marked catch-up, so planning a near event does not start with a pile of overdue work."""
    event_day = local_date(event_date) if event_date else None
    today = today or today_local()
    for tt in ChecklistTemplateTask.query.filter_by(template_id=template.id).order_by(ChecklistTemplateTask.position):
        due = (event_day - timedelta(days=tt.days_before)) if event_day else None
        catch_up = bool(due and due < today and event_day >= today)
        db.session.add(ChecklistTask(
            checklist_id=checklist.id, template_task_id=tt.id, position=tt.position, title=tt.title, notes=tt.notes,
            owner_role=tt.owner_role, days_before=tt.days_before, due_date=today if catch_up else due, catch_up=catch_up,
        ))


def create_checklist(template, name: str, event_date, instance_id: str = None, source: str = "manual", user=None, today: date = None):
    """Make a checklist from a template. Returns None if this event already has one from this template."""
    if instance_id and Checklist.query.filter_by(instance_id=instance_id, template_id=template.id).first():
        return None
    c = Checklist(template_id=template.id, instance_id=instance_id, name=name[:300], event_date=event_date,
                  source=source, created_by_id=getattr(user, "id", None))
    db.session.add(c)
    db.session.flush()
    _add_tasks(c, template, event_date, today)
    db.session.commit()
    return c


def generate_for_upcoming_events(now: datetime = None, horizon_days: int = HORIZON_DAYS, max_new: int = MAX_NEW_PER_RUN) -> dict:
    """Make checklists for upcoming events, and keep existing ones in step with Planning Center."""
    now = now or datetime.utcnow()
    stats = {"created": 0, "moved": 0, "cancelled": 0, "renamed": 0}
    templates = ChecklistTemplate.query.filter_by(active=True).all()

    events = (PcoEvent.query.filter(PcoEvent.starts_at.isnot(None), PcoEvent.starts_at >= now,
                                    PcoEvent.starts_at <= now + timedelta(days=horizon_days),
                                    PcoEvent.removed_at.is_(None)).order_by(PcoEvent.starts_at).all())
    for ev in events:
        if stats["created"] >= max_new:
            break
        for t in templates:
            if template_matches(t, ev.name, ev.tags) and stats["created"] < max_new:
                if create_checklist(t, ev.name, ev.starts_at, instance_id=ev.instance_id, source="auto", today=today_local(now)):
                    stats["created"] += 1

    # Keep existing checklists in step with their event.
    for c in Checklist.query.filter(Checklist.instance_id.isnot(None), Checklist.status == "active").all():
        ev = PcoEvent.query.filter_by(instance_id=c.instance_id).first()
        if ev is None or ev.removed_at is not None:
            if c.event_date is None or c.event_date >= now:     # a past event is simply over, not cancelled
                c.status = "cancelled"
                stats["cancelled"] += 1
            continue
        if ev.name != c.name:
            c.name = ev.name[:300]
            stats["renamed"] += 1
        if ev.starts_at and c.event_date != ev.starts_at:
            c.event_date = ev.starts_at
            day = local_date(ev.starts_at)
            for task in ChecklistTask.query.filter_by(checklist_id=c.id):
                if task.done_at is None and not task.due_overridden:
                    task.due_date = day - timedelta(days=task.days_before)
                    task.catch_up = False
            stats["moved"] += 1
    db.session.commit()
    return stats


# ── Working a checklist ──────────────────────────────────────────────────────

def progress(checklist) -> dict:
    tasks = ChecklistTask.query.filter_by(checklist_id=checklist.id).all()
    done = sum(1 for t in tasks if t.done_at)
    return {"total": len(tasks), "done": done}


def can_work_task(user, task, can_write: bool) -> bool:
    """Who may tick a task: anyone with events.write, the person it is assigned to, or anyone holding its owner role."""
    if can_write:
        return True
    if task.assigned_user_id and task.assigned_user_id == getattr(user, "id", None):
        return True
    return bool(task.owner_role and task.owner_role in set(getattr(user, "roles", ()) or ()))


def set_done(task, user, done: bool) -> None:
    task.done_at = datetime.utcnow() if done else None
    task.done_by_id = getattr(user, "id", None) if done else None
    db.session.commit()


def open_overdue_tasks(today: date = None) -> list:
    """Open tasks past their due date on active checklists whose event is not long over.

    Catch-up tasks (made late) only count once the event is a week away or less."""
    today = today or today_local()
    cutoff = datetime.utcnow() - timedelta(days=PAST_GRACE_DAYS)
    soon = datetime.utcnow() + timedelta(days=7)
    rows = (db.session.query(ChecklistTask, Checklist)
            .join(Checklist, Checklist.id == ChecklistTask.checklist_id)
            .filter(Checklist.status == "active", ChecklistTask.done_at.is_(None),
                    ChecklistTask.due_date.isnot(None), ChecklistTask.due_date < today)
            .filter((Checklist.event_date.is_(None)) | (Checklist.event_date >= cutoff))
            .filter((ChecklistTask.catch_up.is_(False)) | (Checklist.event_date <= soon))
            .order_by(ChecklistTask.due_date).all())
    return rows
