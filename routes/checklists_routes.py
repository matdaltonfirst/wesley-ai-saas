"""Event checklists: the page and its API.

Reading needs events.read. Changing the shape of anything (templates, adding or removing tasks,
changing a due date or an assignee) needs events.write. Ticking a task off is allowed for anyone who
holds the task's owner role or is assigned to it, so the people doing the work can mark it done.
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

import checklists as C
from audit import log_event
from helpers import iso_utc
from models import (
    Checklist, ChecklistTask, ChecklistTemplate, ChecklistTemplateTask, PcoEvent, User, db,
)
from organization import get_org
from permissions import ROLES, can, require

checklists_bp = Blueprint("checklists", __name__)

MAX_TASKS = 60


def _who(user_id) -> str:
    u = User.query.get(user_id) if user_id else None
    return (u.display_name or u.email) if u else ""


def _task_dict(t, can_write: bool) -> dict:
    today = C.today_local()
    return {
        "id": t.id, "title": t.title, "notes": t.notes or "", "owner_role": t.owner_role or "",
        "owner_label": ROLES.get(t.owner_role, ""), "assigned_user_id": t.assigned_user_id,
        "assigned_name": _who(t.assigned_user_id), "due_date": t.due_date.isoformat() if t.due_date else None,
        "done": bool(t.done_at), "done_at": iso_utc(t.done_at), "done_by": _who(t.done_by_id),
        "overdue": bool(t.due_date and not t.done_at and t.due_date < today and not t.catch_up),
        "catch_up": bool(t.catch_up), "can_work": C.can_work_task(current_user, t, can_write),
    }


def _summary(c) -> dict:
    p = C.progress(c)
    next_due = (db.session.query(db.func.min(ChecklistTask.due_date))
                .filter(ChecklistTask.checklist_id == c.id, ChecklistTask.done_at.is_(None)).scalar())
    return {"id": c.id, "name": c.name, "event_date": iso_utc(c.event_date), "status": c.status, "source": c.source,
            "template": (ChecklistTemplate.query.get(c.template_id).name if c.template_id else ""),
            "done": p["done"], "total": p["total"], "next_due": next_due.isoformat() if next_due else None}


@checklists_bp.route("/checklists")
@require("events.read")
def checklists_page():
    if can("events.write"):
        C.seed_starter_templates()
    return render_template("checklists.html", church_name=get_org().name, user_email=current_user.email,
                           can_write=can("events.write"), roles=[{"key": k, "label": v} for k, v in ROLES.items()])


@checklists_bp.route("/api/checklists")
@require("events.read")
def list_checklists():
    scope = request.args.get("scope", "upcoming")
    q = Checklist.query
    if scope == "upcoming":
        q = q.filter(Checklist.status == "active",
                     (Checklist.event_date.is_(None)) | (Checklist.event_date >= datetime.utcnow() - timedelta(days=C.PAST_GRACE_DAYS)))
    rows = q.order_by(Checklist.event_date.is_(None), Checklist.event_date).limit(200).all()
    return jsonify({"checklists": [_summary(c) for c in rows]})


@checklists_bp.route("/api/checklists/mine")
@require("events.read")
def my_tasks():
    """Open tasks this person may work: assigned to them, or owned by a role they hold."""
    roles = set(current_user.roles)
    rows = (db.session.query(ChecklistTask, Checklist).join(Checklist, Checklist.id == ChecklistTask.checklist_id)
            .filter(Checklist.status == "active", ChecklistTask.done_at.is_(None))
            .filter((ChecklistTask.assigned_user_id == current_user.id) | (ChecklistTask.owner_role.in_(roles or {"-"})))
            .order_by(ChecklistTask.due_date.is_(None), ChecklistTask.due_date).limit(100).all())
    write = can("events.write")
    return jsonify({"tasks": [dict(_task_dict(t, write), checklist_id=c.id, checklist=c.name,
                                   event_date=iso_utc(c.event_date)) for t, c in rows]})


@checklists_bp.route("/api/checklists/<int:checklist_id>")
@require("events.read")
def checklist_detail(checklist_id):
    c = Checklist.query.get(checklist_id)
    if not c:
        return jsonify({"error": "Not found."}), 404
    write = can("events.write")
    tasks = ChecklistTask.query.filter_by(checklist_id=c.id).order_by(ChecklistTask.position, ChecklistTask.id).all()
    return jsonify(dict(_summary(c), tasks=[_task_dict(t, write) for t in tasks]))


@checklists_bp.route("/api/checklists/events")
@require("events.write")
def events_without_checklists():
    """Upcoming Planning Center events that have no checklist yet, for starting one by hand."""
    now = datetime.utcnow()
    made = {c.instance_id for c in Checklist.query.filter(Checklist.instance_id.isnot(None))}
    evs = (PcoEvent.query.filter(PcoEvent.starts_at >= now, PcoEvent.starts_at <= now + timedelta(days=C.HORIZON_DAYS),
                                 PcoEvent.removed_at.is_(None)).order_by(PcoEvent.starts_at).limit(100).all())
    return jsonify({"events": [{"instance_id": e.instance_id, "name": e.name, "starts_at": iso_utc(e.starts_at)}
                               for e in evs if e.instance_id not in made]})


@checklists_bp.route("/api/checklists", methods=["POST"])
@require("events.write")
def create_checklist():
    data = request.get_json(silent=True) or {}
    template = ChecklistTemplate.query.get(data.get("template_id") or 0)
    if not template:
        return jsonify({"error": "Choose a template."}), 400
    instance_id = (data.get("instance_id") or "").strip() or None
    if instance_id:
        ev = PcoEvent.query.filter_by(instance_id=instance_id).first()
        if not ev:
            return jsonify({"error": "That event was not found in Planning Center."}), 404
        name, when = ev.name, ev.starts_at
    else:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Give the event a name."}), 400
        try:
            when = datetime.strptime(str(data.get("event_date") or ""), "%Y-%m-%d") + timedelta(hours=15)
        except ValueError:
            return jsonify({"error": "Use a date like 2026-12-24."}), 400
    c = C.create_checklist(template, name, when, instance_id=instance_id, source="manual", user=current_user)
    if c is None:
        return jsonify({"error": "That event already has a checklist from this template."}), 409
    log_event("checklist.create", source=f"checklist:{c.id}", detail={"template": template.name})
    return jsonify({"ok": True, "id": c.id})


@checklists_bp.route("/api/checklists/<int:checklist_id>", methods=["PATCH"])
@require("events.write")
def update_checklist(checklist_id):
    c = Checklist.query.get(checklist_id)
    if not c:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    if data.get("status") in ("active", "cancelled", "done"):
        c.status = data["status"]
    if data.get("name"):
        c.name = str(data["name"])[:300]
    db.session.commit()
    log_event("checklist.update", source=f"checklist:{c.id}", detail={"status": c.status})
    return jsonify({"ok": True})


@checklists_bp.route("/api/checklists/refresh", methods=["POST"])
@require("events.write")
def refresh():
    stats = C.generate_for_upcoming_events()
    log_event("checklist.refresh", detail=stats)
    return jsonify({"ok": True, "stats": stats})


@checklists_bp.route("/api/checklists/<int:checklist_id>/tasks", methods=["POST"])
@require("events.write")
def add_task(checklist_id):
    c = Checklist.query.get(checklist_id)
    if not c:
        return jsonify({"error": "Not found."}), 404
    if ChecklistTask.query.filter_by(checklist_id=c.id).count() >= MAX_TASKS:
        return jsonify({"error": "That checklist already has a lot of tasks."}), 400
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Give the task a title."}), 400
    role = data.get("owner_role") or None
    if role and role not in ROLES:
        return jsonify({"error": "Unknown role."}), 400
    due = _parse_date(data.get("due_date"))
    if data.get("due_date") and due is None:
        return jsonify({"error": "Use a date like 2026-12-24."}), 400
    last = db.session.query(db.func.max(ChecklistTask.position)).filter_by(checklist_id=c.id).scalar() or 0
    t = ChecklistTask(checklist_id=c.id, title=title[:300], owner_role=role, due_date=due, due_overridden=bool(due),
                      position=last + 1, notes=(data.get("notes") or None))
    db.session.add(t)
    db.session.commit()
    log_event("checklist.task_add", source=f"checklist:{c.id}")
    return jsonify({"ok": True, "id": t.id})


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError:
        return None


@checklists_bp.route("/api/checklist-tasks/<int:task_id>", methods=["PATCH"])
@require("events.read")
def update_task(task_id):
    t = ChecklistTask.query.get(task_id)
    if not t:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    write = can("events.write")
    if "done" in data:
        if not C.can_work_task(current_user, t, write):
            return jsonify({"error": "That task belongs to someone else."}), 403
        C.set_done(t, current_user, bool(data["done"]))
        log_event("checklist.task_done" if data["done"] else "checklist.task_reopen", source=f"task:{t.id}")
    structural = {"title", "notes", "owner_role", "assigned_user_id", "due_date"} & set(data)
    if structural:
        if not write:
            return jsonify({"error": "You do not have permission to do that."}), 403
        if "title" in data and str(data["title"]).strip():
            t.title = str(data["title"]).strip()[:300]
        if "notes" in data:
            t.notes = (str(data["notes"]).strip()[:500] or None)
        if "owner_role" in data:
            if data["owner_role"] and data["owner_role"] not in ROLES:
                return jsonify({"error": "Unknown role."}), 400
            t.owner_role = data["owner_role"] or None
        if "assigned_user_id" in data:
            uid = data["assigned_user_id"]
            if uid and not User.query.filter_by(id=uid, active=True).first():
                return jsonify({"error": "That person was not found."}), 400
            t.assigned_user_id = uid or None
        if "due_date" in data:
            due = _parse_date(data["due_date"])
            if data["due_date"] and due is None:
                return jsonify({"error": "Use a date like 2026-12-24."}), 400
            t.due_date, t.due_overridden, t.catch_up = due, bool(due), False
        db.session.commit()
        log_event("checklist.task_edit", source=f"task:{t.id}", detail={"fields": sorted(structural)})
    return jsonify({"ok": True, "task": _task_dict(t, write)})


@checklists_bp.route("/api/checklist-tasks/<int:task_id>", methods=["DELETE"])
@require("events.write")
def delete_task(task_id):
    t = ChecklistTask.query.get(task_id)
    if not t:
        return jsonify({"error": "Not found."}), 404
    db.session.delete(t)
    db.session.commit()
    log_event("checklist.task_delete", source=f"task:{task_id}")
    return jsonify({"ok": True})


@checklists_bp.route("/api/checklists/people")
@require("events.read")
def people():
    users = User.query.filter_by(active=True).order_by(User.email).all()
    return jsonify({"people": [{"id": u.id, "name": u.display_name or u.email} for u in users]})


# ── Templates ────────────────────────────────────────────────────────────────

def _template_dict(t) -> dict:
    tasks = ChecklistTemplateTask.query.filter_by(template_id=t.id).order_by(ChecklistTemplateTask.position).all()
    return {"id": t.id, "name": t.name, "description": t.description or "", "keywords": t.keywords or "", "active": t.active,
            "tasks": [{"title": x.title, "owner_role": x.owner_role or "", "days_before": x.days_before, "notes": x.notes or ""} for x in tasks]}


@checklists_bp.route("/api/checklist-templates")
@require("events.read")
def list_templates():
    return jsonify({"templates": [_template_dict(t) for t in ChecklistTemplate.query.order_by(ChecklistTemplate.name)]})


def _apply_template(t, data):
    """Validate and write a template from the page's form. Returns an error string or None."""
    name = (data.get("name") or "").strip()
    if not name:
        return "Give the template a name."
    clash = ChecklistTemplate.query.filter(ChecklistTemplate.name == name, ChecklistTemplate.id != (t.id or 0)).first()
    if clash:
        return "Another template already has that name."
    tasks = data.get("tasks") or []
    if len(tasks) > MAX_TASKS:
        return "That is a lot of tasks for one template."
    clean = []
    for raw in tasks:
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        role = raw.get("owner_role") or None
        if role and role not in ROLES:
            return f"Unknown role on '{title}'."
        try:
            days = int(raw.get("days_before") or 0)
        except (TypeError, ValueError):
            return f"'{title}' needs a whole number of days."
        if not -365 <= days <= 365:
            return f"'{title}' has an unreasonable number of days."
        clean.append((title[:300], role, days, (str(raw.get("notes") or "").strip()[:500] or None)))
    t.name, t.description = name[:120], (str(data.get("description") or "").strip()[:300] or None)
    t.keywords = str(data.get("keywords") or "").strip()[:500]
    t.active = bool(data.get("active", True))
    db.session.add(t)
    db.session.flush()
    ChecklistTemplateTask.query.filter_by(template_id=t.id).delete()
    for pos, (title, role, days, notes) in enumerate(clean):
        db.session.add(ChecklistTemplateTask(template_id=t.id, position=pos, title=title, owner_role=role, days_before=days, notes=notes))
    db.session.commit()
    return None


@checklists_bp.route("/api/checklist-templates", methods=["POST"])
@require("events.write")
def create_template():
    t = ChecklistTemplate(name="")
    err = _apply_template(t, request.get_json(silent=True) or {})
    if err:
        db.session.rollback()
        return jsonify({"error": err}), 400
    log_event("checklist.template_create", source=f"template:{t.id}")
    return jsonify({"ok": True, "id": t.id})


@checklists_bp.route("/api/checklist-templates/<int:template_id>", methods=["PUT"])
@require("events.write")
def update_template(template_id):
    t = ChecklistTemplate.query.get(template_id)
    if not t:
        return jsonify({"error": "Not found."}), 404
    err = _apply_template(t, request.get_json(silent=True) or {})
    if err:
        db.session.rollback()
        return jsonify({"error": err}), 400
    log_event("checklist.template_edit", source=f"template:{t.id}")
    return jsonify({"ok": True})


@checklists_bp.route("/api/checklist-templates/<int:template_id>", methods=["DELETE"])
@require("events.write")
def delete_template(template_id):
    t = ChecklistTemplate.query.get(template_id)
    if not t:
        return jsonify({"error": "Not found."}), 404
    # Checklists already made from it keep their tasks; they just lose the link.
    Checklist.query.filter_by(template_id=t.id).update({"template_id": None})
    ChecklistTemplateTask.query.filter_by(template_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()
    log_event("checklist.template_delete", source=f"template:{template_id}")
    return jsonify({"ok": True})
