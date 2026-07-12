"""Calendar feed ingestion — fetch ICS feeds, expand events, build chat context.

Any calendar that publishes an iCal/ICS URL works (Google Calendar, Planning
Center, Outlook, Apple, church website plugins). Feeds are refreshed nightly
and immediately when added; recurring events are expanded into concrete
occurrences so the bot can answer "what's happening this Saturday?".
"""

import ipaddress
import logging
import socket
from datetime import datetime, date, timedelta
from urllib.parse import urlparse

import requests
import icalendar
import recurring_ical_events

from models import db, ChurchCalendar, CalendarEvent

log = logging.getLogger("wesley")

FETCH_TIMEOUT_S    = 20
MAX_FEED_BYTES     = 5 * 1024 * 1024
EXPAND_DAYS        = 90    # how far ahead recurring events are expanded
CONTEXT_DAYS       = 45    # how far ahead the bot is told about events
MAX_CONTEXT_EVENTS = 25
FEED_UA = "Mozilla/5.0 (compatible; WesleyAI-Calendar/1.0; +https://wesleyai.co/bot)"


class CalendarFeedError(ValueError):
    """A feed problem the church admin can act on (shown in the dashboard)."""


def _validate_feed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise CalendarFeedError("Enter a valid http(s) calendar feed URL.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise CalendarFeedError("That address could not be found. Check the URL.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise CalendarFeedError("That address is not a public calendar feed.")


def _wall_time(value):
    """Normalize an ICS DTSTART/DTEND to (naive local wall-clock datetime, all_day)."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None), False
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day), True
    return None, False


def fetch_calendar_events(url: str) -> list[dict]:
    """Download and parse an ICS feed into event dicts for the next EXPAND_DAYS.

    Skips events marked PRIVATE/CONFIDENTIAL, and raises CalendarFeedError for
    unusable feeds (unreachable, not ICS, or free/busy-only sharing).
    """
    _validate_feed_url(url)

    try:
        resp = requests.get(
            url, timeout=FETCH_TIMEOUT_S, headers={"User-Agent": FEED_UA}, stream=True,
        )
    except requests.RequestException as exc:
        raise CalendarFeedError(f"Could not reach the calendar feed ({type(exc).__name__}).")
    if resp.status_code != 200:
        raise CalendarFeedError(f"The calendar feed returned HTTP {resp.status_code}.")
    data = resp.raw.read(MAX_FEED_BYTES + 1, decode_content=True)
    if len(data) > MAX_FEED_BYTES:
        raise CalendarFeedError("The calendar feed is too large (over 5 MB).")

    try:
        cal = icalendar.Calendar.from_ical(data)
    except Exception:
        raise CalendarFeedError(
            "That URL is not an ICS calendar feed. Look for the \"iCal\" or "
            "\"ICS\" address in your calendar's sharing settings."
        )

    window_start = date.today()
    window_end = window_start + timedelta(days=EXPAND_DAYS)
    try:
        occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
    except Exception as exc:
        raise CalendarFeedError(f"Could not read events from the feed: {exc}")

    events, skipped_untitled = [], 0
    for occ in occurrences:
        klass = str(occ.get("CLASS", "")).upper()
        if klass in ("PRIVATE", "CONFIDENTIAL"):
            continue
        title = str(occ.get("SUMMARY", "")).strip()
        if not title:
            skipped_untitled += 1
            continue
        starts_at, all_day = _wall_time(occ.get("DTSTART").dt if occ.get("DTSTART") else None)
        if starts_at is None:
            continue
        ends_at, _ = _wall_time(occ.get("DTEND").dt if occ.get("DTEND") else None)
        events.append({
            "title": title[:500],
            "location": (str(occ.get("LOCATION", "")).strip() or None),
            "description": (str(occ.get("DESCRIPTION", "")).strip()[:1000] or None),
            "starts_at": starts_at,
            "ends_at": ends_at,
            "all_day": all_day,
        })

    if not events and skipped_untitled:
        raise CalendarFeedError(
            "This feed only shares free/busy times, not event details. Change the "
            "calendar's sharing settings to include full event information."
        )

    events.sort(key=lambda e: e["starts_at"])
    return events


def refresh_calendar(cal: ChurchCalendar) -> bool:
    """Re-fetch one feed, replacing its stored events. Returns True on success;
    on failure the previous events are kept and the error is recorded."""
    cal.last_fetched_at = datetime.utcnow()
    try:
        events = fetch_calendar_events(cal.url)
    except CalendarFeedError as exc:
        cal.last_error = str(exc)[:500]
        db.session.commit()
        return False
    except Exception as exc:  # unexpected — log fully, show a generic message
        log.error("Calendar refresh failed for calendar_id=%d: %s", cal.id, exc)
        cal.last_error = "Unexpected error while refreshing this feed."
        db.session.commit()
        return False

    CalendarEvent.query.filter_by(calendar_id=cal.id).delete()
    for event in events:
        db.session.add(CalendarEvent(calendar_id=cal.id, church_id=cal.church_id, **event))
    cal.event_count = len(events)
    cal.last_error = None
    db.session.commit()
    return True


def refresh_all_calendars() -> int:
    """Nightly job body: refresh every feed. Returns how many succeeded."""
    ok = 0
    for cal in ChurchCalendar.query.all():
        try:
            ok += refresh_calendar(cal)
        except Exception as exc:
            db.session.rollback()
            log.error("Calendar refresh crashed for calendar_id=%d: %s", cal.id, exc)
    return ok


# ── Chat context ─────────────────────────────────────────────────────────────

def _format_when(event) -> str:
    start, end = event.starts_at, event.ends_at
    day = start.strftime("%A, %B %-d, %Y")
    if event.all_day:
        if end and (end - start) > timedelta(days=1):
            last = end - timedelta(days=1)  # ICS all-day DTEND is exclusive
            return f"{day} through {last.strftime('%A, %B %-d, %Y')}"
        return day
    when = f"{day} at {start.strftime('%-I:%M %p')}"
    if end and end > start:
        if end.date() == start.date():
            when += f" to {end.strftime('%-I:%M %p')}"
        else:
            when += f" through {end.strftime('%A, %B %-d at %-I:%M %p')}"
    return when


def event_dict(event) -> dict:
    """JSON shape used by the dashboard preview list."""
    return {
        "id": event.id,
        "title": event.title,
        "when": _format_when(event),
        "location": event.location or "",
        "all_day": event.all_day,
    }


_EVENT_INTENT_WORDS = (
    "event", "happening", "coming up", "going on", "calendar", "schedule",
    "this week", "this weekend", "this month", "tonight", "today", "tomorrow",
    "saturday", "sunday", "activities",
)


def score_calendar_chunks(question: str, chunks: list[dict]) -> list[tuple[int, dict]]:
    """Score upcoming-event chunks for a question.

    Broad event questions ("what's happening this weekend?") get the whole
    upcoming window in date order — keyword scoring would miss events whose
    titles share no words with the question. Specific questions ("when is
    camp lighthouse?") fall through to normal keyword relevance.
    """
    from documents import find_relevant_chunks

    if not chunks:
        return []
    lowered = question.lower()
    if any(word in lowered for word in _EVENT_INTENT_WORDS):
        return [(1, chunk) for chunk in chunks]
    return find_relevant_chunks(question, chunks, top_n=10)


def load_calendar_chunks(church_id: int) -> list[dict]:
    """Upcoming events as citable retrieval chunks, soonest first."""
    now = datetime.utcnow()
    events = (
        CalendarEvent.query
        .filter(
            CalendarEvent.church_id == church_id,
            CalendarEvent.starts_at >= now - timedelta(days=1),
            CalendarEvent.starts_at <= now + timedelta(days=CONTEXT_DAYS),
        )
        .order_by(CalendarEvent.starts_at)
        .limit(MAX_CONTEXT_EVENTS)
        .all()
    )
    chunks = []
    for event in events:
        lines = [f"Event: {event.title}", f"When: {_format_when(event)}"]
        if event.location:
            lines.append(f"Where: {event.location}")
        if event.description:
            lines.append(event.description)
        chunks.append({
            "content": "\n".join(lines),
            "source": (event.calendar.label if event.calendar else None) or "Church calendar",
            "location": "",
            "type": "calendar",
        })
    return chunks
