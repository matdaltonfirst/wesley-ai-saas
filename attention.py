"""The "Needs attention" list: things that are actually wrong or overdue, for the person looking.

Alerts are worked out from current data every time, never stored, so the same problem is the same
alert (it has a stable key) however many syncs happen. A person can snooze one for a while. Each
person sees only the alerts their permissions allow, and an alert says what to do about it.

Nothing here sends anything. It is a list on the home page.
"""

from datetime import date, datetime, timedelta

import checklists as C
from models import AttentionSnooze, GuestConnection, StreamingNumber, db
from permissions import can

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
GUEST_WAIT_DAYS = 2
MAX_SNOOZE_DAYS = 30


def _alert(key, severity, title, detail, link, action):
    return {"key": key, "severity": severity, "title": title, "detail": detail, "link": link, "action": action}


def _connector_alerts(user):
    import connectors
    from connectors import runner
    out = []
    for c in connectors.all_connectors():
        h = runner.health(c)
        if h.status in ("needs_reconnect", "failing"):
            out.append(_alert(f"connector:{c.key}:{h.status}", "high", f"{c.label} is not syncing",
                              h.detail or h.headline, "/integrations", "Open Integrations"))
        elif h.status == "stale":
            out.append(_alert(f"connector:{c.key}:stale", "medium", f"{c.label} has not synced recently",
                              h.detail or h.headline, "/integrations", "Open Integrations"))
    return out


def _task_alerts(user, today):
    write = can("events.write", user)
    roles = set(user.roles)
    grouped = {}
    for task, checklist in C.open_overdue_tasks(today):
        mine = task.assigned_user_id == user.id or (task.owner_role and task.owner_role in roles)
        if write or mine:
            grouped.setdefault(checklist.id, {"checklist": checklist, "tasks": []})["tasks"].append(task)
    out = []
    for g in grouped.values():
        names = "; ".join(t.title for t in g["tasks"][:3]) + ("; and more" if len(g["tasks"]) > 3 else "")
        n = len(g["tasks"])
        out.append(_alert(f"checklist:{g['checklist'].id}:overdue", "high" if n >= 3 else "medium",
                          f"{n} overdue task{'s' if n != 1 else ''} for {g['checklist'].name}", names,
                          f"/checklists#{g['checklist'].id}", "Open the checklist"))
    return out


def _review_alerts(user):
    import review
    out = []
    for q in review.waiting_for_approval():
        sermon, rows = q["sermon"], q["rows"]
        approved = sum(1 for r in rows.values() if r.status == "approved")
        if q["status"] == "needs_changes" and can("sunday.write", user):
            notes = "; ".join(f"{review.PIECES[p][0]}: {r.note}" for p, r in rows.items() if r.status == "needs_changes" and r.note)
            out.append(_alert(f"review:{q['packet'].id}:changes", "medium", f"Changes requested on '{sermon.title}'",
                              notes or "A reviewer asked for changes.", "/review", "Open the review queue"))
        elif can("sunday.approve", user):
            out.append(_alert(f"review:{q['packet'].id}:waiting", "medium", f"'{sermon.title}' is waiting for review",
                              f"{approved} of {len(rows)} pieces approved.", "/review", "Open the review queue"))
    return out


def _streaming_alerts(today):
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    if today <= last_sunday:                      # on Sunday itself the numbers are not in yet
        return []
    have = (StreamingNumber.query.filter(StreamingNumber.service_date >= last_sunday,
                                         StreamingNumber.service_date < last_sunday + timedelta(days=1),
                                         StreamingNumber.total_views.isnot(None)).first())
    if have:
        return []
    return [_alert(f"streaming:{last_sunday.isoformat()}", "medium", f"No streaming numbers for {last_sunday.strftime('%B %-d')}",
                   "No platform has reported views for that Sunday yet. Check Integrations, or enter them by hand.",
                   "/streaming", "Open streaming numbers")]


def _guest_alerts():
    cutoff = datetime.utcnow() - timedelta(days=GUEST_WAIT_DAYS)
    n = GuestConnection.query.filter(GuestConnection.status == "new", GuestConnection.created_at <= cutoff).count()
    if not n:
        return []
    return [_alert("guests:waiting", "high" if n >= 5 else "medium", f"{n} guest connection{'s' if n != 1 else ''} waiting more than {GUEST_WAIT_DAYS} days",
                   "These people reached out through the website and nobody has marked them contacted.",
                   "/dashboard#guest-connections", "Open guest connections")]


def alerts_for(user, today: date = None) -> list:
    """Everything this person should look at, most urgent first, minus what they snoozed."""
    today = today or C.today_local()
    found = []
    if can("integrations.read", user):
        found += _connector_alerts(user)
    if can("events.read", user):
        found += _task_alerts(user, today)
    if can("sunday.read", user):
        found += _review_alerts(user)
    if can("streaming.write", user):
        found += _streaming_alerts(today)
    if can("guests.read", user):
        found += _guest_alerts()
    hidden = {s.alert_key for s in AttentionSnooze.query.filter(AttentionSnooze.user_id == user.id,
                                                              AttentionSnooze.until > datetime.utcnow())}
    found = [a for a in found if a["key"] not in hidden]
    return sorted(found, key=lambda a: (SEVERITY_ORDER[a["severity"]], a["title"]))


def snooze(user, key: str, days: int = 7) -> None:
    days = max(1, min(int(days or 7), MAX_SNOOZE_DAYS))
    until = datetime.utcnow() + timedelta(days=days)
    row = AttentionSnooze.query.filter_by(user_id=user.id, alert_key=key[:120]).first()
    if row is None:
        row = AttentionSnooze(user_id=user.id, alert_key=key[:120], until=until)
        db.session.add(row)
    row.until = until
    db.session.commit()
