"""Tests for calendar feed ingestion (calendar_feed.py) and its routes."""

import io
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from models import db, ChurchCalendar, CalendarEvent, WidgetConversation
from calendar_feed import (
    fetch_calendar_events, refresh_calendar, load_calendar_chunks,
    score_calendar_chunks, CalendarFeedError,
)


def _dt(days_ahead, hour=18):
    return (datetime.utcnow() + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _ics(*event_blocks):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        + "".join(event_blocks)
        + "END:VCALENDAR\r\n"
    ).encode()


def _event(title, start, duration_hours=2, extra=""):
    end = start + timedelta(hours=duration_hours)
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{title.replace(' ', '')}@test\r\n"
        f"SUMMARY:{title}\r\n"
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"{extra}"
        "END:VEVENT\r\n"
    )


class FakeRaw:
    def __init__(self, data):
        self._buf = io.BytesIO(data)

    def read(self, n, decode_content=True):
        return self._buf.read(n)


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.raw = FakeRaw(data)


def _patch_fetch(data):
    return patch("calendar_feed.requests.get", return_value=FakeResponse(data))


_no_validate = patch("calendar_feed._validate_feed_url", lambda url: None)


class TestFetchCalendarEvents:
    def test_parses_and_sorts_future_events(self, app):
        data = _ics(
            _event("Fall Festival", _dt(10)),
            _event("Board Meeting", _dt(3), extra="LOCATION:Room 201\r\n"),
        )
        with _no_validate, _patch_fetch(data):
            events = fetch_calendar_events("https://feeds.example.org/cal.ics")
        assert [e["title"] for e in events] == ["Board Meeting", "Fall Festival"]
        assert events[0]["location"] == "Room 201"
        assert events[0]["all_day"] is False

    def test_expands_recurring_events(self, app):
        data = _ics(_event("Sunday Worship", _dt(2, hour=9),
                           extra="RRULE:FREQ=WEEKLY;COUNT=4\r\n"))
        with _no_validate, _patch_fetch(data):
            events = fetch_calendar_events("https://feeds.example.org/cal.ics")
        assert len(events) == 4
        assert all(e["title"] == "Sunday Worship" for e in events)
        assert events[1]["starts_at"] - events[0]["starts_at"] == timedelta(days=7)

    def test_skips_private_events(self, app):
        data = _ics(
            _event("Public Picnic", _dt(5)),
            _event("Counseling Session", _dt(6), extra="CLASS:PRIVATE\r\n"),
            _event("Budget Review", _dt(7), extra="CLASS:CONFIDENTIAL\r\n"),
        )
        with _no_validate, _patch_fetch(data):
            events = fetch_calendar_events("https://feeds.example.org/cal.ics")
        assert [e["title"] for e in events] == ["Public Picnic"]

    def test_all_day_events(self, app):
        day = (datetime.utcnow() + timedelta(days=14)).strftime("%Y%m%d")
        block = (
            "BEGIN:VEVENT\r\nUID:allday@test\r\nSUMMARY:Youth Retreat\r\n"
            f"DTSTART;VALUE=DATE:{day}\r\nEND:VEVENT\r\n"
        )
        with _no_validate, _patch_fetch(_ics(block)):
            events = fetch_calendar_events("https://feeds.example.org/cal.ics")
        assert events[0]["all_day"] is True

    def test_free_busy_only_feed_rejected(self, app):
        block = (
            "BEGIN:VEVENT\r\nUID:busy@test\r\n"
            f"DTSTART:{_dt(4).strftime('%Y%m%dT%H%M%S')}\r\nEND:VEVENT\r\n"
        )
        with _no_validate, _patch_fetch(_ics(block)):
            with pytest.raises(CalendarFeedError, match="free/busy"):
                fetch_calendar_events("https://feeds.example.org/cal.ics")

    def test_non_ics_content_rejected(self, app):
        with _no_validate, _patch_fetch(b"<html>Not a calendar</html>"):
            with pytest.raises(CalendarFeedError, match="not an ICS"):
                fetch_calendar_events("https://feeds.example.org/cal.ics")


class TestCalendarRoutes:
    def _add(self, auth_client, url="https://feeds.example.org/cal.ics"):
        data = _ics(_event("Fall Festival", _dt(10)))
        with _no_validate, _patch_fetch(data):
            return auth_client.post("/api/calendars", json={"url": url})

    def test_requires_auth(self, client):
        assert client.get("/api/calendars").status_code == 401

    def test_add_returns_preview(self, auth_client, church):
        res = self._add(auth_client)
        assert res.status_code == 201
        cal = res.get_json()["calendar"]
        assert cal["event_count"] == 1
        assert cal["preview"][0]["title"] == "Fall Festival"

        for cal in ChurchCalendar.query.filter_by(church_id=church.id).all():
            db.session.delete(cal)  # ORM delete so events cascade
        db.session.commit()

    def test_bad_feed_is_not_saved(self, auth_client, church):
        with _no_validate, _patch_fetch(b"nope"):
            res = auth_client.post("/api/calendars",
                                   json={"url": "https://feeds.example.org/bad.ics"})
        assert res.status_code == 400
        assert ChurchCalendar.query.filter_by(church_id=church.id).count() == 0

    def test_duplicate_and_limit(self, auth_client, church):
        assert self._add(auth_client).status_code == 201
        assert self._add(auth_client).status_code == 400  # duplicate
        for n in (2, 3):
            assert self._add(auth_client, f"https://feeds.example.org/{n}.ics").status_code == 201
        res = self._add(auth_client, "https://feeds.example.org/4.ics")
        assert res.status_code == 400
        assert "up to 3" in res.get_json()["error"]

        for cal in ChurchCalendar.query.filter_by(church_id=church.id).all():
            db.session.delete(cal)  # ORM delete so events cascade
        db.session.commit()

    def test_delete_removes_events(self, auth_client, church):
        cal_id = self._add(auth_client).get_json()["calendar"]["id"]
        assert CalendarEvent.query.filter_by(calendar_id=cal_id).count() == 1
        assert auth_client.delete(f"/api/calendars/{cal_id}").status_code == 200
        assert CalendarEvent.query.filter_by(calendar_id=cal_id).count() == 0


class TestCalendarChat:
    def _seed_events(self, church):
        cal = ChurchCalendar(church_id=church.id,
                             url="https://feeds.example.org/cal.ics")
        db.session.add(cal)
        db.session.flush()
        db.session.add(CalendarEvent(
            calendar_id=cal.id, church_id=church.id, title="Community Picnic",
            location="Front Lawn", starts_at=_dt(4), ends_at=_dt(4) + timedelta(hours=2),
        ))
        db.session.add(CalendarEvent(
            calendar_id=cal.id, church_id=church.id, title="Camp Lighthouse Kickoff",
            starts_at=_dt(9), ends_at=_dt(9) + timedelta(hours=3),
        ))
        db.session.commit()
        return cal

    def test_broad_event_question_gets_full_window(self, app, church):
        self._seed_events(church)
        chunks = load_calendar_chunks(church.id)
        scored = score_calendar_chunks("What's happening this weekend?", chunks)
        assert len(scored) == 2
        assert "Community Picnic" in scored[0][1]["content"]  # chronological

        for cal in ChurchCalendar.query.filter_by(church_id=church.id).all():
            db.session.delete(cal)  # ORM delete so events cascade
        db.session.commit()

    def test_widget_chat_cites_calendar(self, client, church):
        self._seed_events(church)
        captured = {}

        def fake_gemini(question, context, history, system_instruction):
            captured["context"] = context
            return "The Community Picnic is this week on the Front Lawn [1]."

        with patch("routes.widget.call_gemini", side_effect=fake_gemini):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "What events are coming up?",
            })
        assert res.status_code == 200
        data = res.get_json()
        assert "Event: Community Picnic" in captured["context"]
        assert data["sources"] and data["sources"][0]["title"] == "Church calendar"
        assert data["sources"][0]["type"] == "calendar"

        wconv = WidgetConversation.query.filter_by(
            church_id=church.id, session_id=data["session_id"]).first()
        db.session.delete(wconv)
        for cal in ChurchCalendar.query.filter_by(church_id=church.id).all():
            db.session.delete(cal)  # ORM delete so events cascade
        db.session.commit()
