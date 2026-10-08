"""Facebook Page and linked Instagram account (Meta Graph API).

Auth: a **Page access token** pasted by an admin on the Integrations page (stored
encrypted), plus the Page id and optionally the Instagram business account id. A
long-lived Page token created from a long-lived user token does not expire on a
schedule; Meta revokes it if the person who made it loses admin access, changes
their password, or removes the app. That surfaces here as "needs to be reconnected".

Permissions the token needs (``docs/INTEGRATIONS.md`` has the click path):
``pages_show_list``, ``pages_read_engagement``, ``read_insights`` and, for Instagram,
``instagram_basic`` and ``instagram_manage_insights``. **Meta requires App Review
(Advanced Access) before these work for anyone who is not an admin, developer or
tester of the app.** For one church's own Page, create the app in Development mode,
add the church admin as a developer, and generate the token as that person: no
review is needed. Review is needed only if the app serves other people.

What it records:

* ``social_posts``: recent Facebook posts and Instagram media with likes, comments,
  shares, and reach/views where Meta still offers them.
* ``streaming_numbers`` (platform ``facebook``): total views and watch time for
  live videos. **Meta's API does not report peak concurrent viewers**, so that
  figure is left blank for a person to enter from Live Producer; the pane shows the gap.

Meta retires metrics without much notice (a batch ended 15 June 2026). Every insight
call is therefore optional: a rejected metric becomes one warning on the run, and
the post or video is still stored without it. The Graph API version is
``META_GRAPH_VERSION`` (default below). Field and metric names are from Meta's public
reference and are **not yet verified against a live Page**.
"""

import logging
import os
from datetime import datetime

import streaming
from models import SocialPost

from .base import StaticTokenConnector
from .errors import AuthError, ConnectorError, UpstreamError
from .runner import config_of
from .youtube import YouTubeConnector

log = logging.getLogger("wesley")

VERSION = os.getenv("META_GRAPH_VERSION", "v23.0")
GRAPH = f"https://graph.facebook.com/{VERSION}"
LIVE_FIELDS = "id,status,title,creation_time,live_views,video{id,length}"
VIDEO_METRICS = "total_video_views,total_video_view_total_time"


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def metric_value(body, name):
    for item in body.get("data", []):
        if item.get("name") == name:
            values = item.get("values") or [{}]
            v = values[-1].get("value")
            return int(v) if isinstance(v, (int, float)) else None
    return None


class MetaConnector(StaticTokenConnector):
    key = "facebook"
    label = "Facebook and Instagram"
    description = "Post performance on the church's Facebook Page and Instagram, and views of Facebook live services."
    docs_anchor = "facebook-and-instagram"
    interval_minutes = 120
    min_interval_seconds = 0.3
    token_label = "Page access token"

    def configured(self) -> bool:
        return True

    def settings(self) -> dict:
        return config_of(self.key)

    def waiting_for_access(self) -> str:
        if not self.connected():
            return ""
        if not self.settings().get("page_id"):
            return "Add the Facebook Page id on this page to start syncing."
        return ""

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def graph(self, ctx, path, **params):
        """A Graph call with Meta's error codes translated into plain language."""
        try:
            return ctx.client.get(f"{GRAPH}/{path}", params=params or None, headers=self.auth_headers())
        except UpstreamError as exc:
            text = str(exc)
            if "OAuthException" in text and ('"code":190' in text.replace(" ", "") or "Error validating access token" in text):
                raise AuthError("Meta no longer accepts this Page token. Create a new one and paste it again.") from exc
            raise

    # ── Sync ─────────────────────────────────────────────────────────────────
    def sync(self, ctx):
        cfg = self.settings()
        self.sync_facebook_posts(ctx, cfg["page_id"])
        self.sync_live_videos(ctx, cfg["page_id"])
        if cfg.get("ig_user_id"):
            self.sync_instagram(ctx, cfg["ig_user_id"])

    def _optional(self, ctx, what, fn):
        """Run an optional insights call; a retired or unpermitted metric is only a warning."""
        try:
            return fn()
        except AuthError:
            raise
        except ConnectorError as exc:
            ctx.warn(f"{what} not available from Meta right now: {str(exc)[:140]}")
            return None

    def sync_facebook_posts(self, ctx, page_id):
        body = self.graph(ctx, f"{page_id}/posts", limit=25, fields=(
            "id,created_time,permalink_url,message,status_type,"
            "reactions.summary(true).limit(0),comments.summary(true).limit(0),shares"))
        for p in body.get("data", []):
            reactions = ((p.get("reactions") or {}).get("summary") or {}).get("total_count") or 0
            comments = ((p.get("comments") or {}).get("summary") or {}).get("total_count") or 0
            shares = (p.get("shares") or {}).get("count") or 0
            reach = self._optional(ctx, "Post reach", lambda: metric_value(
                self.graph(ctx, f"{p['id']}/insights", metric="post_impressions_unique"), "post_impressions_unique"))
            ctx.store_raw("fb_post", p["id"], p)
            changed = ctx.upsert(SocialPost, {"platform": "facebook", "post_id": p["id"]}, {
                "kind": (p.get("status_type") or "post")[:20], "created_at": parse_time(p.get("created_time")),
                "permalink": p.get("permalink_url"), "caption": (p.get("message") or "")[:300] or None,
                "engagements": reactions + comments + shares, "reach": reach,
            })
            ctx.note("facebook_posts", fetched=1, changed=int(changed))

    def sync_live_videos(self, ctx, page_id):
        body = self.graph(ctx, f"{page_id}/live_videos", limit=25, fields=LIVE_FIELDS)
        services = {}
        for lv in body.get("data", []):
            video_id = (lv.get("video") or {}).get("id") or lv.get("id")
            created = parse_time(lv.get("creation_time"))
            insights = self._optional(ctx, "Live video insights", lambda: self.graph(
                ctx, f"{video_id}/video_insights", metric=VIDEO_METRICS))
            views = metric_value(insights, "total_video_views") if insights else None
            ms = metric_value(insights, "total_video_view_total_time") if insights else None
            minutes = int(ms / 60000) if ms is not None else None
            ctx.store_raw("fb_live", lv.get("id"), lv)
            changed = ctx.upsert(SocialPost, {"platform": "facebook", "post_id": str(video_id)}, {
                "kind": "live", "created_at": created, "live_views": lv.get("live_views"),
                "video_views": views, "watch_seconds": int(ms / 1000) if ms is not None else None,
                "caption": (lv.get("title") or "")[:300] or None,
            })
            ctx.note("facebook_live", fetched=1, changed=int(changed))
            if lv.get("status") in ("VOD", "PROCESSING") or lv.get("status") == "LIVE_STOPPED":
                if created and (views is not None or minutes is not None):
                    key = (YouTubeConnector.local_date(created), YouTubeConnector.service_label(lv.get("title")))
                    s = services.setdefault(key, {"views": None, "minutes": None, "id": str(video_id)})
                    if views is not None:
                        s["views"] = (s["views"] or 0) + views
                    if minutes is not None:
                        s["minutes"] = (s["minutes"] or 0) + minutes
        for (day, label), s in services.items():
            values = {k: v for k, v in (("total_views", s["views"]), ("watch_minutes", s["minutes"])) if v is not None}
            _, outcome = streaming.save_number(day, label, "facebook", values, "api", by="Facebook sync", external_id=s["id"])
            ctx.note("streaming_numbers", fetched=1, changed=int(outcome in ("created", "updated")))

    def sync_instagram(self, ctx, ig_id):
        body = self.graph(ctx, f"{ig_id}/media", limit=25,
                          fields="id,caption,media_type,permalink,timestamp,like_count,comments_count")
        for m in body.get("data", []):
            ins = self._optional(ctx, "Instagram insights", lambda: self.graph(
                ctx, f"{m['id']}/insights", metric="reach,views"))
            reach = metric_value(ins, "reach") if ins else None
            views = metric_value(ins, "views") if ins else None
            ctx.store_raw("ig_media", m["id"], m)
            changed = ctx.upsert(SocialPost, {"platform": "instagram", "post_id": m["id"]}, {
                "kind": (m.get("media_type") or "").lower()[:20] or None, "created_at": parse_time(m.get("timestamp")),
                "permalink": m.get("permalink"), "caption": (m.get("caption") or "")[:300] or None,
                "engagements": (m.get("like_count") or 0) + (m.get("comments_count") or 0),
                "reach": reach, "video_views": views,
            })
            ctx.note("instagram_media", fetched=1, changed=int(changed))
