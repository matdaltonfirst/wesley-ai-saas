"""Tests for sermon ingestion (sermons.py + routes)."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from models import db, SermonSource, Sermon, WidgetConversation
import sermons as sermon_lib
from sermons import (
    resolve_channel, ingest_sermon, check_source, load_sermon_chunks,
    score_sermon_chunks, _parse_distilled, SermonError,
)


def _source(church, **overrides):
    src = SermonSource(
        church_id=church.id, channel_url="https://youtube.com/@testchurch",
        channel_id="UCabc123", channel_title="Test Church", **overrides,
    )
    db.session.add(src)
    db.session.commit()
    return src


def _sermon(church, src, video_id="vid1", status="ingested", days_ago=3, **overrides):
    fields = dict(
        source_id=src.id, church_id=church.id, video_id=video_id,
        title="Grace Like Rain", published_at=datetime.utcnow() - timedelta(days=days_ago),
        status=status, summary="A sermon about prevenient grace and God's pursuit of us.",
        main_points="Grace goes before us\nGrace meets us in failure",
        scriptures="Ephesians 2:1-10", series="Amazing Grace",
    )
    fields.update(overrides)
    s = Sermon(**fields)
    db.session.add(s)
    db.session.commit()
    return s


def _cleanup(church):
    for src in SermonSource.query.filter_by(church_id=church.id).all():
        db.session.delete(src)  # cascades to sermons
    db.session.commit()


_DISTILLED = {
    "summary": "A sermon about grace.",
    "main_points": ["Point one", "Point two"],
    "scriptures": ["John 3:16"],
    "series": "Amazing Grace",
}


class TestChannelResolution:
    def test_handle_url(self, app):
        with patch("sermons._yt_get", return_value={"items": [{
            "id": "UCxyz", "snippet": {"title": "Dalton First UMC"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}},
        }]}) as mock_get:
            info = resolve_channel("https://youtube.com/@daltonfumc")
        assert info["channel_id"] == "UCxyz"
        assert mock_get.call_args.kwargs.get("forHandle") == "daltonfumc"

    def test_channel_id_url(self, app):
        with patch("sermons._yt_get", return_value={"items": [{
            "id": "UCabc", "snippet": {"title": "X"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
        }]}) as mock_get:
            info = resolve_channel("https://www.youtube.com/channel/UCabc")
        assert info["channel_id"] == "UCabc"
        assert mock_get.call_args.kwargs.get("id") == "UCabc"

    def test_garbage_url_rejected(self, app):
        with pytest.raises(SermonError):
            resolve_channel("https://vimeo.com/12345")

    def test_unknown_channel_rejected(self, app):
        with patch("sermons._yt_get", return_value={"items": []}):
            with pytest.raises(SermonError, match="Could not find"):
                resolve_channel("https://youtube.com/@nope")


class TestDistillParsing:
    def test_plain_json(self):
        assert _parse_distilled(json.dumps(_DISTILLED))["series"] == "Amazing Grace"

    def test_fenced_json(self):
        raw = "```json\n" + json.dumps(_DISTILLED) + "\n```"
        assert _parse_distilled(raw)["summary"] == "A sermon about grace."


class TestIngestSermon:
    def test_captions_path(self, app, church):
        src = _source(church)
        sermon = _sermon(church, src, status="pending",
                         summary=None, main_points=None, scriptures=None, series=None)
        with patch("sermons.fetch_captions", return_value="Grace grace grace " * 50), \
             patch("sermons.distill_sermon", return_value=_DISTILLED):
            assert ingest_sermon(sermon) is True
        assert sermon.status == "ingested"
        assert sermon.summary == "A sermon about grace."
        assert sermon.main_points == "Point one\nPoint two"
        assert sermon.scriptures == "John 3:16"
        _cleanup(church)

    def test_non_sermon_video_marked_failed(self, app, church):
        src = _source(church)
        sermon = _sermon(church, src, status="pending", summary=None)
        with patch("sermons.fetch_captions", return_value=None), \
             patch("sermons.distill_sermon", return_value={"summary": None}):
            assert ingest_sermon(sermon) is False
        assert sermon.status == "failed"
        assert "No sermon content" in sermon.error
        _cleanup(church)

    def test_distill_error_recorded(self, app, church):
        src = _source(church)
        sermon = _sermon(church, src, status="pending", summary=None)
        with patch("sermons.fetch_captions", return_value=None), \
             patch("sermons.distill_sermon", side_effect=RuntimeError("model exploded")):
            assert ingest_sermon(sermon) is False
        assert sermon.status == "failed"
        assert "model exploded" in sermon.error
        _cleanup(church)


class TestCheckSource:
    def test_only_new_videos_ingested(self, app, church):
        src = _source(church)
        _sermon(church, src, video_id="old1")
        videos = [
            {"video_id": "old1", "title": "Old", "published_at": datetime.utcnow()},
            {"video_id": "new1", "title": "New", "published_at": datetime.utcnow()},
        ]
        with patch("sermons.list_recent_videos", return_value=videos), \
             patch("sermons.fetch_captions", return_value="words " * 100), \
             patch("sermons.distill_sermon", return_value=_DISTILLED):
            count = check_source(src)
        assert count == 1
        assert Sermon.query.filter_by(church_id=church.id).count() == 2
        _cleanup(church)


class TestSermonChunks:
    def test_sermon_intent_gets_recent_first(self, app, church):
        src = _source(church)
        _sermon(church, src, video_id="a", days_ago=10, title="Older Message")
        _sermon(church, src, video_id="b", days_ago=2, title="Newest Message")
        chunks = load_sermon_chunks(church.id)
        scored = score_sermon_chunks("What was Sunday's sermon about?", chunks)
        assert len(scored) == 2
        assert "Newest Message" in scored[0][1]["content"]
        assert "most recent sermon" in scored[0][1]["content"]
        _cleanup(church)

    def test_failed_sermons_excluded(self, app, church):
        src = _source(church)
        _sermon(church, src, video_id="bad", status="failed")
        assert load_sermon_chunks(church.id) == []
        _cleanup(church)

    def test_widget_chat_cites_sermon(self, client, church):
        src = _source(church)
        _sermon(church, src, video_id="xyz", title="Grace Like Rain")
        captured = {}

        def fake_gemini(question, context, history, system_instruction):
            captured["context"] = context
            return "Sunday's message was about God's grace [1]."

        with patch("routes.widget.call_gemini", side_effect=fake_gemini):
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "What was the sermon about this week?",
            })
        assert res.status_code == 200
        data = res.get_json()
        assert "Grace Like Rain" in captured["context"]
        assert data["sources"][0]["type"] == "sermon"
        assert "youtube.com" in data["sources"][0]["url"]

        wconv = WidgetConversation.query.filter_by(
            church_id=church.id, session_id=data["session_id"]).first()
        db.session.delete(wconv)
        _cleanup(church)


class TestSermonRoutes:
    def test_status_requires_auth(self, client):
        assert client.get("/api/sermons/status").status_code == 401

    def test_status_unconfigured(self, auth_client):
        res = auth_client.get("/api/sermons/status")
        assert res.get_json() == {"configured": False, "connected": False}

    def test_connect_and_status(self, auth_client, church):
        with patch("sermons.YOUTUBE_API_KEY", "key"), \
             patch("routes.sermons_routes.sermon_lib.resolve_channel",
                   return_value={"channel_id": "UCx", "title": "Test Church",
                                 "uploads_playlist": "UUx"}), \
             patch("routes.sermons_routes._run_in_background"):
            res = auth_client.post("/api/sermons/source",
                                   json={"url": "https://youtube.com/@testchurch"})
            assert res.status_code == 201

            res = auth_client.get("/api/sermons/status")
            data = res.get_json()
        assert data["connected"] is True
        assert data["channel_title"] == "Test Church"
        _cleanup(church)

    def test_second_channel_rejected(self, auth_client, church):
        _source(church)
        with patch("sermons.YOUTUBE_API_KEY", "key"):
            res = auth_client.post("/api/sermons/source",
                                   json={"url": "https://youtube.com/@another"})
        assert res.status_code == 400
        _cleanup(church)

    def test_paste_transcript_ingests(self, auth_client, church):
        src = _source(church)
        sermon = _sermon(church, src, status="failed", summary=None)
        with patch("sermons.distill_sermon", return_value=_DISTILLED):
            res = auth_client.patch(f"/api/sermons/{sermon.id}",
                                    json={"transcript": "grace and truth " * 30})
        assert res.status_code == 200
        assert res.get_json()["sermon"]["status"] == "ingested"
        _cleanup(church)

    def test_disconnect_removes_sermons(self, auth_client, church):
        src = _source(church)
        _sermon(church, src)
        assert auth_client.delete("/api/sermons/source").status_code == 200
        assert Sermon.query.filter_by(church_id=church.id).count() == 0

    def test_reingest_all_rebuilds_summaries(self, auth_client, church):
        src = _source(church)
        _sermon(church, src, video_id="a")
        _sermon(church, src, video_id="b", status="failed")
        with patch("routes.sermons_routes._run_in_background") as mock_bg:
            res = auth_client.post("/api/sermons/reingest-all")
        assert res.status_code == 200
        assert res.get_json()["count"] == 2
        assert mock_bg.called
        statuses = {s.status for s in Sermon.query.filter_by(church_id=church.id)}
        assert statuses == {"pending"}
        _cleanup(church)

    def test_exclude_sermon(self, auth_client, church):
        src = _source(church)
        sermon = _sermon(church, src, video_id="unwanted", title="Worship Night")
        res = auth_client.delete(f"/api/sermons/{sermon.id}")
        assert res.status_code == 200
        assert sermon.status == "excluded"
        assert sermon.summary is None

        # Hidden from the dashboard list
        with patch("sermons.YOUTUBE_API_KEY", "key"):
            data = auth_client.get("/api/sermons/status").get_json()
        assert all(s["title"] != "Worship Night" for s in data["sermons"])

        # Invisible to the bot
        assert load_sermon_chunks(church.id) == []

        # Not re-created by the daily check (video_id still known)
        videos = [{"video_id": "unwanted", "title": "Worship Night",
                   "published_at": datetime.utcnow()}]
        with patch("sermons.list_recent_videos", return_value=videos):
            assert check_source(src) == 0
        assert Sermon.query.filter_by(church_id=church.id).count() == 1

        # Skipped by rebuild-all
        res = auth_client.post("/api/sermons/reingest-all")
        assert res.status_code == 400  # nothing left to rebuild
        _cleanup(church)

    def test_check_source_recovers_stuck_pending(self, app, church):
        src = _source(church)
        _sermon(church, src, video_id="stuck", status="pending", summary=None)
        with patch("sermons.list_recent_videos", return_value=[]), \
             patch("sermons.fetch_captions", return_value="words " * 100), \
             patch("sermons.distill_sermon", return_value=_DISTILLED):
            count = check_source(src)
        assert count == 1
        assert Sermon.query.filter_by(video_id="stuck").one().status == "ingested"
        _cleanup(church)

    def test_widget_prompt_directs_sermon_answers(self, app, church):
        from helpers import build_system_prompt
        prompt = build_system_prompt(church, widget=True)
        assert "sources labeled 'Sermon:'" in prompt
        assert "Blog posts and web pages are not sermons" in prompt

    def test_status_self_heals_stuck_pending(self, auth_client, church):
        import routes.sermons_routes as sr
        src = _source(church)
        _sermon(church, src, video_id="stuck", status="pending", summary=None)
        sr._active_recoveries.clear()
        with patch("sermons.YOUTUBE_API_KEY", "key"), \
             patch("routes.sermons_routes._run_in_background") as mock_bg:
            auth_client.get("/api/sermons/status")
            auth_client.get("/api/sermons/status")  # second poll must not stack
        assert mock_bg.call_count == 1
        sr._active_recoveries.clear()
        _cleanup(church)


class TestChurchLocalDates:
    def test_prompt_uses_church_local_today(self, app, church):
        from zoneinfo import ZoneInfo
        from datetime import datetime as dt
        from helpers import build_system_prompt
        expected = dt.now(ZoneInfo("America/New_York")).strftime("%A, %B %-d, %Y")
        prompt = build_system_prompt(church, widget=True)
        assert f"Today's date is {expected}" in prompt

    def test_sermon_chunk_date_is_church_local(self, app, church):
        src = _source(church)
        # 01:00 UTC on July 6 is the evening of July 5 in Georgia
        _sermon(church, src, video_id="tz",
                published_at=datetime(2026, 7, 6, 1, 0, 0))
        chunks = load_sermon_chunks(church.id)
        assert "July 5, 2026" in chunks[0]["content"]
        _cleanup(church)
