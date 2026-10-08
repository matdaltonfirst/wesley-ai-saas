"""Planning Center: calendar, services, groups, check-in counts, publishing, registrations.

Auth reuses the existing OAuth connection (``pco_connections``), which the guest
sync already uses. That connection was made with only the ``people`` scope, so the
first job is to reconnect it with the wider list in ``config.PCO_SCOPES`` (the
Integrations page says so). **Giving is never requested here.**

Privacy by construction: check-ins and registrations are read as counts
(``check_ins``, ``attendees`` totals); no person, child or attendee name is
requested or stored. People are not synced at all: Phase 5 looks a person up live
when someone with ``data.people_basic`` asks, so there is no copy to go stale or leak.

Each resource syncs independently. If one fails for a missing permission the
others still run and the run is marked partial with a plain-language warning.

Webhooks: ``POST /webhooks/planning_center``, authenticated with the subscription's
HMAC-SHA256 signature (``X-PCO-Webhooks-Authenticity``, secret ``PCO_WEBHOOK_SECRET``).
A webhook is treated only as "something changed": it triggers a sync and its
payload is never trusted for data.
"""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta

import pco
from config import PCO_API_BASE, PCO_SCOPES, PCO_WEBHOOK_SECRET
from models import (
    PcoCheckinCount, PcoConnection, PcoEpisode, PcoEvent, PcoGroup, PcoServicePlan,
    PcoSignup, db,
)

from .base import Connector
from .errors import AuthError, ConnectorError, NotConfigured

log = logging.getLogger("wesley")

REQUIRED = {  # resource -> scope it needs
    "calendar": "calendar", "services": "services", "groups": "groups", "check_ins": "check_ins",
    "publishing": "publishing", "registrations": "registrations",
}
PAGE = 100
MAX_PAGES = 20


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


class PlanningCenterConnector(Connector):
    key = "planning_center"
    label = "Planning Center"
    description = "Calendar events, service plans, groups, check-in counts, sermon episodes and registrations."
    docs_anchor = "planning-center"
    interval_minutes = 15
    supports_webhook = True
    min_interval_seconds = 0.25            # PCO allows about 100 requests per 20 seconds

    def env_vars(self):
        return ["PCO_CLIENT_ID", "PCO_CLIENT_SECRET"]

    def connection(self):
        return PcoConnection.query.first()

    def connected(self) -> bool:
        return self.connection() is not None

    def granted_scopes(self) -> set:
        conn = self.connection()
        return set((conn.scope or "people").split()) if conn else set()

    def missing_scopes(self) -> list:
        wanted = set(PCO_SCOPES.split()) - {"people"}
        return sorted(wanted - self.granted_scopes())

    def waiting_for_access(self) -> str:
        if not self.connected():
            return ""
        missing = self.missing_scopes()
        if missing and set(missing) >= set(REQUIRED.values()) & set(PCO_SCOPES.split()):
            return ("Planning Center is connected for guest follow-up only. Reconnect it to let the app "
                    "read the calendar, services, groups, check-in counts, publishing and registrations. "
                    "Missing: " + ", ".join(missing) + ".")
        return ""

    # ── Auth ─────────────────────────────────────────────────────────────────
    def access_token(self) -> str:
        conn = self.connection()
        if conn is None:
            raise AuthError("Planning Center is not connected.")
        try:
            if conn.token_expires_at <= datetime.utcnow() + timedelta(minutes=2):
                pco._refresh_tokens(conn)
            return pco._stored_token(conn, "access_token")
        except pco.PcoError as exc:
            raise AuthError(str(exc)) from exc

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def make_client(self, on_unauthorized=None):
        def refresh(headers):
            try:
                pco._refresh_tokens(self.connection(), force=True)
            except pco.PcoError as exc:
                raise AuthError(str(exc)) from exc
            return {**headers, **self.auth_headers()}
        return super().make_client(on_unauthorized=on_unauthorized or refresh)

    # ── Webhook ──────────────────────────────────────────────────────────────
    def verify_webhook(self, headers, body: bytes) -> bool:
        secret = PCO_WEBHOOK_SECRET or ""
        sent = headers.get("X-PCO-Webhooks-Authenticity", "")
        if not secret or not sent:
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sent)

    # ── Paging ───────────────────────────────────────────────────────────────
    def pages(self, ctx, path, params=None):
        """Yield (items, included_by_key) for each page, following ``links.next``."""
        url = PCO_API_BASE + path
        params = {"per_page": PAGE, **(params or {})}
        for _ in range(MAX_PAGES):
            body = ctx.client.get(url, params=params, headers=self.auth_headers())
            included = {(i.get("type"), i.get("id")): i for i in body.get("included", [])}
            yield body.get("data", []), included, body
            url, params = (body.get("links") or {}).get("next"), None
            if not url:
                return

    # ── Sync ─────────────────────────────────────────────────────────────────
    def sync(self, ctx):
        steps = [("calendar", self.sync_calendar), ("services", self.sync_services),
                 ("groups", self.sync_groups), ("check_ins", self.sync_checkins),
                 ("publishing", self.sync_publishing), ("registrations", self.sync_registrations)]
        granted = self.granted_scopes()
        for name, fn in steps:
            if REQUIRED[name] not in granted:
                ctx.warn(f"{name.replace('_', ' ').title()} skipped: not yet permitted. Reconnect Planning Center.")
                continue
            try:
                fn(ctx)
                db.session.commit()
            except AuthError:
                raise
            except ConnectorError as exc:
                db.session.rollback()
                ctx.warn(f"{name.replace('_', ' ').title()}: {exc}")

    def sync_calendar(self, ctx):
        seen, now = set(), datetime.utcnow()
        for items, included, _ in self.pages(ctx, "/calendar/v2/event_instances",
                                             {"filter": "future", "include": "event,tags", "order": "starts_at"}):
            for it in items:
                a, rel = it.get("attributes", {}), it.get("relationships", {})
                event_id = ((rel.get("event") or {}).get("data") or {}).get("id")
                event = included.get(("Event", event_id), {}).get("attributes", {})
                tags = [included.get(("Tag", t["id"]), {}).get("attributes", {}).get("name")
                        for t in (rel.get("tags") or {}).get("data", [])]
                ctx.store_raw("event_instance", it["id"], it)
                seen.add(it["id"])
                changed = ctx.upsert(PcoEvent, {"instance_id": it["id"]}, {
                    "event_id": event_id, "name": (a.get("name") or event.get("name") or "")[:300],
                    "starts_at": parse_time(a.get("starts_at")), "ends_at": parse_time(a.get("ends_at")),
                    "all_day": bool(a.get("all_day_event")), "location": (a.get("location") or None),
                    "tags": ",".join(t for t in tags if t)[:300] or None,
                    "visible_in_church_center": event.get("visible_in_church_center"),
                    "updated_remote_at": parse_time(event.get("updated_at")), "removed_at": None,
                })
                ctx.note("calendar", fetched=1, changed=int(changed))
        # Future instances that vanished were cancelled or deleted: mark, never delete.
        for row in PcoEvent.query.filter(PcoEvent.starts_at >= now, PcoEvent.removed_at.is_(None)).all():
            if row.instance_id not in seen:
                row.removed_at = now
                ctx.note("calendar", changed=1)

    def sync_services(self, ctx):
        types = []
        for items, _, _ in self.pages(ctx, "/services/v2/service_types", {}):
            types += [(t["id"], t.get("attributes", {}).get("name")) for t in items]
        for type_id, type_name in types:
            for flt, order in (("future", "sort_date"), ("past", "-sort_date")):
                params = {"filter": flt, "order": order, "per_page": 8 if flt == "past" else 25}
                items, _, _ = next(self.pages(ctx, f"/services/v2/service_types/{type_id}/plans", params), ([], {}, {}))
                for p in items:
                    a = p.get("attributes", {})
                    ctx.store_raw("plan", p["id"], p)
                    changed = ctx.upsert(PcoServicePlan, {"plan_id": p["id"]}, {
                        "service_type": type_name, "title": a.get("title"), "series": a.get("series_title"),
                        "sort_date": parse_time(a.get("sort_date")),
                        "positions_total": a.get("plan_people_count"),
                        "positions_needed": a.get("needed_positions_count"),
                    })
                    ctx.note("services", fetched=1, changed=int(changed))

    def sync_groups(self, ctx):
        for items, included, _ in self.pages(ctx, "/groups/v2/groups", {"include": "group_type"}):
            for g in items:
                a = g.get("attributes", {})
                gt = ((g.get("relationships", {}).get("group_type") or {}).get("data") or {}).get("id")
                ctx.store_raw("group", g["id"], g)
                changed = ctx.upsert(PcoGroup, {"group_id": g["id"]}, {
                    "name": (a.get("name") or "")[:300], "archived": bool(a.get("archived_at")),
                    "group_type": included.get(("GroupType", gt), {}).get("attributes", {}).get("name"),
                    "members_count": a.get("memberships_count"),
                })
                ctx.note("groups", fetched=1, changed=int(changed))

    def sync_checkins(self, ctx):
        """Counts per session. Names are never requested."""
        for items, included, _ in self.pages(ctx, "/check-ins/v2/event_times",
                                             {"include": "event", "order": "-starts_at"}):
            for t in items:
                a = t.get("attributes", {})
                ev = ((t.get("relationships", {}).get("event") or {}).get("data") or {}).get("id")
                name = included.get(("Event", ev), {}).get("attributes", {}).get("name") or a.get("name")
                ctx.store_raw("event_time", t["id"], t)
                changed = ctx.upsert(PcoCheckinCount, {"event_time_id": t["id"]}, {
                    "event_name": (name or "")[:300] or None, "starts_at": parse_time(a.get("starts_at")),
                    "check_ins": a.get("total_count"), "guests": a.get("guest_count"),
                    "regulars": a.get("regular_count"), "volunteers": a.get("volunteer_count"),
                })
                ctx.note("check_ins", fetched=1, changed=int(changed))
            break                       # the newest page is enough; older sessions are already stored

    def sync_publishing(self, ctx):
        items, _, _ = next(self.pages(ctx, "/publishing/v2/episodes",
                                      {"order": "-published_live_at", "per_page": 12}), ([], {}, {}))
        for e in items:
            a = e.get("attributes", {})
            views = None
            try:
                stats = ctx.client.get(f"{PCO_API_BASE}/publishing/v2/episodes/{e['id']}/episode_statistics",
                                       headers=self.auth_headers())
                sa = ((stats.get("data") or [{}])[0] if isinstance(stats.get("data"), list) else stats.get("data") or {}).get("attributes", {})
                views = next((sa[k] for k in ("views", "view_count", "total_views") if sa.get(k) is not None), None)
            except AuthError:
                raise
            except ConnectorError:
                pass                      # statistics are optional; the episode itself still syncs
            ctx.store_raw("episode", e["id"], e)
            changed = ctx.upsert(PcoEpisode, {"episode_id": e["id"]}, {
                "title": (a.get("title") or "")[:500], "views": views,
                "published_at": parse_time(a.get("published_live_at") or a.get("published_to_library_at")),
            })
            ctx.note("publishing", fetched=1, changed=int(changed))

    def sync_registrations(self, ctx):
        for items, _, _ in self.pages(ctx, "/registrations/v2/signups", {"filter": "unarchived"}):
            for s in items:
                a = s.get("attributes", {})
                count = None
                try:
                    r = ctx.client.get(f"{PCO_API_BASE}/registrations/v2/signups/{s['id']}/attendees",
                                       params={"per_page": 1}, headers=self.auth_headers())
                    count = (r.get("meta") or {}).get("total_count")      # a number only: no attendee is read
                except AuthError:
                    raise
                except ConnectorError:
                    ctx.warn(f"Could not count attendees for '{(a.get('name') or s['id'])[:60]}'.")
                ctx.store_raw("signup", s["id"], s)
                changed = ctx.upsert(PcoSignup, {"signup_id": s["id"]}, {
                    "name": (a.get("name") or "")[:300], "archived": bool(a.get("archived")),
                    "opens_at": parse_time(a.get("open_at")), "closes_at": parse_time(a.get("close_at")),
                    "capacity": a.get("maximum_capacity"), "attendee_count": count,
                })
                ctx.note("registrations", fetched=1, changed=int(changed))
