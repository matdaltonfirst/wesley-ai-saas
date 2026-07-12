"""Calendar feed routes: connect, preview, refresh, and remove ICS feeds."""

import logging

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from models import db, ChurchCalendar, CalendarEvent
from calendar_feed import refresh_calendar, event_dict, CalendarFeedError
from helpers import iso_utc

log = logging.getLogger("wesley")

calendars_bp = Blueprint("calendars", __name__)

MAX_CALENDARS_PER_CHURCH = 3
PREVIEW_EVENT_LIMIT = 15


def _calendar_dict(cal, with_preview=False):
    data = {
        "id": cal.id,
        "url": cal.url,
        "label": cal.label,
        "event_count": cal.event_count,
        "last_fetched_at": iso_utc(cal.last_fetched_at),
        "last_error": cal.last_error,
    }
    if with_preview:
        upcoming = (
            CalendarEvent.query
            .filter_by(calendar_id=cal.id)
            .order_by(CalendarEvent.starts_at)
            .limit(PREVIEW_EVENT_LIMIT)
            .all()
        )
        data["preview"] = [event_dict(e) for e in upcoming]
    return data


@calendars_bp.route("/api/calendars")
@login_required
def list_calendars():
    cals = (
        ChurchCalendar.query
        .filter_by(church_id=current_user.church_id)
        .order_by(ChurchCalendar.created_at)
        .all()
    )
    return jsonify({"calendars": [_calendar_dict(c, with_preview=True) for c in cals]})


@calendars_bp.route("/api/calendars", methods=["POST"])
@login_required
def add_calendar():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    label = (data.get("label") or "").strip()[:200] or "Church calendar"

    if not url or len(url) > 1000:
        return jsonify({"error": "Enter a calendar feed URL."}), 400

    # Google Calendar shows an easy-to-copy HTML link next to the ICS one —
    # rewrite the common mistake instead of rejecting it.
    if "calendar.google.com" in url and "/embed?" in url:
        return jsonify({"error": "That is the embed link. In Google Calendar "
                        "settings, copy the address ending in .ics instead."}), 400

    existing = ChurchCalendar.query.filter_by(church_id=current_user.church_id)
    if existing.filter_by(url=url).first():
        return jsonify({"error": "That calendar is already connected."}), 400
    if existing.count() >= MAX_CALENDARS_PER_CHURCH:
        return jsonify({"error": f"You can connect up to {MAX_CALENDARS_PER_CHURCH} calendars."}), 400

    cal = ChurchCalendar(church_id=current_user.church_id, url=url, label=label)
    db.session.add(cal)
    db.session.flush()

    if not refresh_calendar(cal):
        error = cal.last_error or "Could not read that calendar feed."
        db.session.delete(cal)
        db.session.commit()
        return jsonify({"error": error}), 400

    log.info("Calendar connected: church_id=%d calendar_id=%d (%d events)",
             cal.church_id, cal.id, cal.event_count)
    return jsonify({"ok": True, "calendar": _calendar_dict(cal, with_preview=True)}), 201


@calendars_bp.route("/api/calendars/<int:cal_id>/refresh", methods=["POST"])
@login_required
def refresh_calendar_now(cal_id):
    cal = ChurchCalendar.query.filter_by(
        id=cal_id, church_id=current_user.church_id
    ).first()
    if not cal:
        return jsonify({"error": "Calendar not found."}), 404
    refresh_calendar(cal)
    return jsonify({"ok": cal.last_error is None,
                    "calendar": _calendar_dict(cal, with_preview=True)})


@calendars_bp.route("/api/calendars/<int:cal_id>", methods=["DELETE"])
@login_required
def delete_calendar(cal_id):
    cal = ChurchCalendar.query.filter_by(
        id=cal_id, church_id=current_user.church_id
    ).first()
    if not cal:
        return jsonify({"error": "Calendar not found."}), 404
    db.session.delete(cal)
    db.session.commit()
    return jsonify({"ok": True})
