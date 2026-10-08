"""The YouTube connector against a fake YouTube."""

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from connectors import runner
from connectors.youtube import YouTubeConnector
from models import RawPayload, StreamingNumber, SyncRun, YoutubeVideo, db
import streaming as S

NOW = datetime.utcnow().replace(microsecond=0)
SUN_START = datetime(2026, 10, 4, 13, 30)        # 9:30 Eastern


def iso(dt):
    return dt.isoformat() + "Z"


def fake_youtube(videos, analytics, calls=None, channel_items=True):
    """A fake that answers by URL. *analytics* maps video id to a metrics dict, or an Exception."""
    def resp(body, status=200):
        r = MagicMock(); r.status_code = status; r.headers = {}; r.json.return_value = body; r.text = json.dumps(body)
        return r

    def fake(method, url, **kw):
        if calls is not None:
            calls.append((url, kw.get("params")))
        if url.endswith("/oauth2/v4/token") or url.endswith("/token"):
            return resp({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
        if url.endswith("/channels"):
            return resp({"items": [{"id": "UCx", "snippet": {"title": "Dalton First UMC"},
                                    "contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}] if channel_items else []})
        if url.endswith("/playlistItems"):
            return resp({"items": [{"contentDetails": {"videoId": v["id"], "videoPublishedAt": v["snippet"]["publishedAt"]}} for v in videos]})
        if url.endswith("/videos"):
            ids = kw["params"]["id"].split(",")
            return resp({"items": [v for v in videos if v["id"] in ids]})
        if "youtubeanalytics" in url:
            vid = kw["params"]["filters"].split("==")[1]
            a = analytics.get(vid)
            if isinstance(a, Exception):
                raise a
            if a is None:
                return resp({"columnHeaders": [], "rows": []})
            names = list(a)
            return resp({"columnHeaders": [{"name": n} for n in names], "rows": [[a[n] for n in names]]})
        raise AssertionError("unexpected url " + url)
    return fake


def live_video(vid, title, start, views=1000, minutes_end=75):
    return {"id": vid, "snippet": {"title": title, "publishedAt": iso(start - timedelta(hours=1))},
            "statistics": {"viewCount": str(views), "likeCount": "40", "commentCount": "3"},
            "liveStreamingDetails": {"actualStartTime": iso(start), "actualEndTime": iso(start + timedelta(minutes=minutes_end))}}


@pytest.fixture
def yt(app, church, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "cs")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
    c = YouTubeConnector()
    c.min_interval_seconds = 0
    c.request_fn = fake_youtube([], {})
    c.exchange_code("code")
    return c


def sync(yt, videos, analytics, **kw):
    yt.request_fn = fake_youtube(videos, analytics, **kw)
    return runner.run_sync(yt, "manual")


class TestYouTubeSync:
    def test_a_live_service_becomes_a_streaming_number_with_peak_concurrent(self, yt):
        v = live_video("v1", "WHO IS MY NEIGHBOR? | Modern Service", NOW - timedelta(days=1))
        v2 = live_video("v1", "WHO IS MY NEIGHBOR? | Modern Service", SUN_START)
        run = sync(yt, [v2], {"v1": {"views": 1500, "estimatedMinutesWatched": 9000, "averageViewDuration": 410,
                                     "peakConcurrentViewers": 212, "averageConcurrentViewers": 140}})
        assert run.status in ("ok", "partial")
        n = StreamingNumber.query.one()
        assert (n.platform, n.service_date, n.service_label) == ("youtube", date(2026, 10, 4), "Modern service")
        assert (n.peak_concurrent, n.total_views, n.watch_minutes) == (212, 1500, 9000)
        assert S.row_dict(n)["sources"]["peak_concurrent"] == "api"
        vid = YoutubeVideo.query.one()
        assert vid.peak_concurrent == 212 and vid.likes == 40 and vid.is_live and vid.avg_view_seconds == 410

    def test_total_views_prefer_analytics_over_the_public_counter(self, yt):
        sync(yt, [live_video("v1", "Sunday", SUN_START, views=999)], {"v1": {"views": 1234, "peakConcurrentViewers": 10, "estimatedMinutesWatched": 5}})
        assert StreamingNumber.query.one().total_views == 1234

    def test_the_service_date_is_the_churchs_local_date(self, yt):
        late = datetime(2026, 10, 5, 2, 30)         # 10:30 pm Sunday evening Eastern, already Monday in UTC
        sync(yt, [live_video("v1", "Evening", late)], {"v1": {"views": 5, "peakConcurrentViewers": 5, "estimatedMinutesWatched": 5}})
        assert StreamingNumber.query.one().service_date == date(2026, 10, 4)

    def test_service_labels_come_from_the_title(self, yt):
        vids = [live_video("a", "Traditional Worship Service", SUN_START), live_video("b", "Sunday Live", SUN_START + timedelta(minutes=90))]
        sync(yt, vids, {"a": {"views": 100, "peakConcurrentViewers": 20, "estimatedMinutesWatched": 1},
                        "b": {"views": 200, "peakConcurrentViewers": 30, "estimatedMinutesWatched": 2}})
        assert {n.service_label for n in StreamingNumber.query} == {"Traditional service", "Sunday service"}

    def test_two_streams_with_the_same_label_and_day_add_views_and_take_the_higher_peak(self, yt):
        vids = [live_video("a", "Modern Service", SUN_START), live_video("b", "Modern Service (replay stream)", SUN_START + timedelta(hours=3))]
        sync(yt, vids, {"a": {"views": 100, "peakConcurrentViewers": 20, "estimatedMinutesWatched": 10},
                        "b": {"views": 50, "peakConcurrentViewers": 35, "estimatedMinutesWatched": 5}})
        n = StreamingNumber.query.one()
        assert (n.total_views, n.peak_concurrent, n.watch_minutes) == (150, 35, 15)

    def test_syncing_again_changes_nothing(self, yt):
        args = ([live_video("v1", "Sunday", SUN_START)], {"v1": {"views": 10, "peakConcurrentViewers": 5, "estimatedMinutesWatched": 1}})
        sync(yt, *args)
        second = sync(yt, *args)
        assert second.rows_changed == 0 and StreamingNumber.query.count() == 1

    def test_a_manual_correction_survives_the_next_sync(self, yt):
        args = ([live_video("v1", "Sunday", SUN_START)], {"v1": {"views": 10, "peakConcurrentViewers": 5, "estimatedMinutesWatched": 1}})
        sync(yt, *args)
        S.save_number(date(2026, 10, 4), "Sunday service", "youtube", {"total_views": 777}, "manual", by="c@x", reason="fix")
        db.session.commit()
        sync(yt, [live_video("v1", "Sunday", SUN_START)], {"v1": {"views": 99, "peakConcurrentViewers": 6, "estimatedMinutesWatched": 2}})
        n = StreamingNumber.query.one()
        assert n.total_views == 777 and n.peak_concurrent == 6          # peak kept updating; views stayed corrected

    def test_rising_views_do_not_flood_the_edit_history(self, yt):
        sync(yt, [live_video("v1", "Sunday", SUN_START)], {"v1": {"views": 10, "peakConcurrentViewers": 5, "estimatedMinutesWatched": 1}})
        for views in (20, 30, 40):
            sync(yt, [live_video("v1", "Sunday", SUN_START)], {"v1": {"views": views, "peakConcurrentViewers": 5, "estimatedMinutesWatched": 1}})
        from models import StreamingNumberEdit
        assert StreamingNumber.query.one().total_views == 40 and StreamingNumberEdit.query.count() == 3   # first write only

    def test_an_ordinary_upload_is_stored_but_is_not_a_service(self, yt):
        v = {"id": "up1", "snippet": {"title": "Choir feature", "publishedAt": iso(NOW - timedelta(days=3))},
             "statistics": {"viewCount": "55"}}
        sync(yt, [v], {})
        assert YoutubeVideo.query.one().views == 55 and StreamingNumber.query.count() == 0

    def test_a_video_without_analytics_is_a_warning_not_a_failure(self, yt):
        from connectors.errors import UpstreamError
        run = sync(yt, [live_video("v1", "Sunday", SUN_START, views=321)], {"v1": UpstreamError("quota")})
        assert run.status == "partial" and "No analytics" in json.loads(run.detail)["warnings"][0]
        assert StreamingNumber.query.one().total_views == 321 and StreamingNumber.query.one().peak_concurrent is None

    def test_old_videos_are_ignored(self, yt):
        old = live_video("old", "Sunday", NOW - timedelta(days=200))
        sync(yt, [old], {"old": {}})
        assert YoutubeVideo.query.count() == 0

    def test_raw_payloads_are_stored_separately(self, yt):
        sync(yt, [live_video("v1", "Sunday", SUN_START)], {"v1": {}})
        assert {r.resource for r in RawPayload.query} == {"channel", "video"}

    def test_the_channel_name_is_remembered_for_the_status_page(self, yt):
        sync(yt, [], {})
        assert yt.token_row().account_label == "Dalton First UMC"

    def test_the_wrong_account_gives_a_helpful_message(self, yt):
        run = sync(yt, [], {}, channel_items=False)
        assert run.status == "failed" and "church channel" in run.error

    def test_analytics_requests_ask_for_the_peak_metric_for_one_video(self, yt):
        calls = []
        sync(yt, [live_video("v1", "Sunday", SUN_START)], {"v1": {}}, calls=calls)
        params = next(p for u, p in calls if "youtubeanalytics" in u)
        assert "peakConcurrentViewers" in params["metrics"] and params["filters"] == "video==v1" and params["ids"] == "channel==MINE"

    def test_a_configured_channel_id_is_used_instead_of_mine(self, yt, monkeypatch):
        monkeypatch.setenv("YOUTUBE_CHANNEL_ID", "UCchurch")
        calls = []
        sync(yt, [live_video("v1", "Sunday", SUN_START)], {"v1": {}}, calls=calls)
        channel_params = next(p for u, p in calls if u.endswith("/channels"))
        assert channel_params.get("id") == "UCchurch" and "mine" not in channel_params
        analytics_params = next(p for u, p in calls if "youtubeanalytics" in u)
        assert analytics_params["ids"] == "channel==UCchurch"

    def test_a_wrong_configured_channel_id_says_so(self, yt, monkeypatch):
        monkeypatch.setenv("YOUTUBE_CHANNEL_ID", "UCnope")
        run = sync(yt, [], {}, channel_items=False)
        assert run.status == "failed" and "YOUTUBE_CHANNEL_ID" in run.error

    def test_it_asks_for_offline_access_and_both_scopes(self, yt):
        url = yt.authorize_redirect("s")
        assert "access_type=offline" in url and "yt-analytics.readonly" in url and "youtube.readonly" in url
