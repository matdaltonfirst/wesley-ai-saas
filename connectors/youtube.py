"""YouTube: video stats, live-stream peak concurrents, watch time.

Auth: OAuth 2.0 for the church's own channel (not the API key used for sermon
discovery), scopes ``youtube.readonly`` and ``yt-analytics.readonly``. Uses the
same Google Cloud OAuth client as staff sign-in; the redirect URI
``<APP_URL>/integrations/youtube/callback`` must be added to it, and the
"YouTube Data API v3" and "YouTube Analytics API" must be enabled in the project.

What it feeds:

* ``youtube_videos``: every recent upload with views, likes, comments.
* ``streaming_numbers``: one row per live service (platform "youtube"), from the
  Analytics API's ``peakConcurrentViewers`` (which exists only for videos that
  were live), ``views`` and ``estimatedMinutesWatched``.

Transcripts are still fetched by the sermon pipeline; the official captions API
needs a broader scope and is a Phase 3 decision.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streaming
from config import DEFAULT_TIMEZONE
from models import YoutubeVideo
from organization import get_org

from .base import OAuthConnector
from .errors import ConnectorError, UpstreamError
from .runner import config_of

log = logging.getLogger("wesley")

DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
LOOKBACK_DAYS = 70
MAX_VIDEOS = 50


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")      # naive UTC
    except ValueError:
        return None


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class YouTubeConnector(OAuthConnector):
    key = "youtube"
    label = "YouTube"
    description = "Views, watch time and peak concurrent viewers for the church channel's live services."
    docs_anchor = "youtube"
    interval_minutes = 60
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes = ["https://www.googleapis.com/auth/youtube.readonly",
              "https://www.googleapis.com/auth/yt-analytics.readonly"]
    client_id_env = "GOOGLE_CLIENT_ID"
    client_secret_env = "GOOGLE_CLIENT_SECRET"
    extra_authorize_params = {"access_type": "offline", "prompt": "consent",
                              "include_granted_scopes": "true"}
    min_interval_seconds = 0.1

    # ── Labeling a stream as a service ───────────────────────────────────────
    @staticmethod
    def service_label(title: str) -> str:
        """Pick a service label from the video title using the configurable rules."""
        rules = config_of("youtube").get("service_labels") or [
            {"match": "modern", "label": "Modern service"},
            {"match": "traditional", "label": "Traditional service"},
            {"match": "contemporary", "label": "Modern service"},
        ]
        lowered = (title or "").lower()
        for rule in rules:
            if str(rule.get("match", "")).lower() in lowered:
                return str(rule.get("label") or streaming.DEFAULT_LABEL)
        return streaming.DEFAULT_LABEL

    @staticmethod
    def local_date(utc: datetime):
        try:
            zone = ZoneInfo(get_org().timezone or DEFAULT_TIMEZONE)
        except Exception:
            zone = ZoneInfo("America/New_York")
        return utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone).date()

    # ── API calls ────────────────────────────────────────────────────────────
    def _data(self, ctx, path, **params):
        return ctx.client.get(f"{DATA}/{path}", params=params, headers=self.auth_headers())

    def _recent_video_ids(self, ctx, uploads_playlist: str) -> list:
        cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)
        ids = []
        data = self._data(ctx, "playlistItems", part="contentDetails", playlistId=uploads_playlist,
                          maxResults=MAX_VIDEOS)
        for item in data.get("items", []):
            details = item.get("contentDetails", {})
            published = parse_time(details.get("videoPublishedAt"))
            if details.get("videoId") and (published is None or published >= cutoff):
                ids.append(details["videoId"])
        return ids

    def _analytics(self, ctx, video_id: str, start: datetime) -> dict:
        """Peak and average concurrents, views and minutes watched for one video."""
        params = {
            "ids": "channel==MINE", "startDate": start.date().isoformat(),
            "endDate": datetime.utcnow().date().isoformat(),
            "metrics": "views,estimatedMinutesWatched,averageViewDuration,peakConcurrentViewers,averageConcurrentViewers",
            "filters": f"video=={video_id}",
        }
        data = ctx.client.get(ANALYTICS, params=params, headers=self.auth_headers())
        headers = [h.get("name") for h in data.get("columnHeaders", [])]
        rows = data.get("rows") or []
        return dict(zip(headers, rows[0])) if rows else {}

    # ── Sync ─────────────────────────────────────────────────────────────────
    def sync(self, ctx):
        channel = self._data(ctx, "channels", part="snippet,contentDetails", mine="true")
        items = channel.get("items") or []
        if not items:
            raise UpstreamError("YouTube returned no channel for this sign-in. Reconnect using the church channel's account.")
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        row = self.token_row()
        row.account_label = items[0]["snippet"].get("title")
        ctx.store_raw("channel", items[0]["id"], items[0])

        ids = self._recent_video_ids(ctx, uploads)
        services = {}
        for chunk_start in range(0, len(ids), 50):
            chunk = ids[chunk_start:chunk_start + 50]
            videos = self._data(ctx, "videos", part="snippet,statistics,liveStreamingDetails",
                                id=",".join(chunk), maxResults=50).get("items", [])
            for v in videos:
                self._one_video(ctx, v, services)
        self._write_services(ctx, services)

    def _one_video(self, ctx, v, services):
        vid = v["id"]
        snippet, stats = v.get("snippet", {}), v.get("statistics", {})
        live = v.get("liveStreamingDetails") or {}
        start, end = parse_time(live.get("actualStartTime")), parse_time(live.get("actualEndTime"))
        ctx.store_raw("video", vid, v)
        peak = minutes = avg_seconds = views_analytics = None
        if live and start and end:                      # an ended live stream
            try:
                a = self._analytics(ctx, vid, start)
                peak = to_int(a.get("peakConcurrentViewers"))
                minutes = to_int(a.get("estimatedMinutesWatched"))
                avg_seconds = to_int(a.get("averageViewDuration"))
                views_analytics = to_int(a.get("views"))
            except ConnectorError as exc:
                if exc.kind == "auth":
                    raise
                ctx.warn(f"No analytics for '{(snippet.get('title') or vid)[:60]}': {exc}")
        total_views = views_analytics if views_analytics is not None else to_int(stats.get("viewCount"))
        changed = ctx.upsert(YoutubeVideo, {"video_id": vid}, {
            "title": (snippet.get("title") or "")[:500], "published_at": parse_time(snippet.get("publishedAt")),
            "is_live": bool(live), "actual_start": start, "actual_end": end,
            "views": to_int(stats.get("viewCount")), "likes": to_int(stats.get("likeCount")),
            "comments": to_int(stats.get("commentCount")), "peak_concurrent": peak,
            "watch_minutes": minutes, "avg_view_seconds": avg_seconds,
        })
        ctx.note("videos", fetched=1, changed=int(changed))
        if live and start and end:
            key = (self.local_date(start), self.service_label(snippet.get("title")))
            s = services.setdefault(key, {"views": None, "peak": None, "minutes": None, "ids": []})
            s["ids"].append(vid)
            if total_views is not None:
                s["views"] = (s["views"] or 0) + total_views
            if minutes is not None:
                s["minutes"] = (s["minutes"] or 0) + minutes
            if peak is not None:
                s["peak"] = max(s["peak"] or 0, peak)

    def _write_services(self, ctx, services):
        for (day, label), s in services.items():
            values = {}
            if s["views"] is not None:
                values["total_views"] = s["views"]
            if s["peak"] is not None:
                values["peak_concurrent"] = s["peak"]
            if s["minutes"] is not None:
                values["watch_minutes"] = s["minutes"]
            if not values:
                continue
            _, outcome = streaming.save_number(day, label, "youtube", values, "api", by="YouTube sync",
                                               external_id=s["ids"][0])
            ctx.note("streaming_numbers", fetched=1, changed=int(outcome in ("created", "updated")))
