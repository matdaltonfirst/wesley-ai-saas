"""Constant Contact (v3 API): campaign results and list growth.

Auth: OAuth 2.0 authorization code flow against ``authz.constantcontact.com``,
client credentials sent as HTTP Basic, scopes ``campaign_data``, ``contact_data``
and ``offline_access`` (the last is what makes the provider return a refresh
token). Refresh tokens rotate, and the token endpoint is itself rate limited, so
tokens are refreshed only when about to expire (handled by the framework).

Counts only: contact addresses are never requested. List growth is recorded as a
daily snapshot of list sizes, because the API reports current size, not history.

Endpoints used (v3, base ``https://api.cc.email/v3``):

* ``GET /emails``: campaigns, each with its ``campaign_activities`` (id and role).
* ``GET /reports/stats/email_campaign_activities/{ids}``: up to ten activity ids;
  ``stats`` with ``em_sends``, ``em_opens``, ``em_clicks``, ``em_bounces``,
  ``em_optouts``, ``em_forwards`` (verified against Constant Contact's guide).
* ``GET /contact_lists?include_membership_count=all``: ``membership_count`` per list.
* ``GET /contacts?limit=1&include_count=true&status=all``: ``contacts_count``.

The last three endpoints' parameter names are taken from Constant Contact's
public reference and have not been exercised against a live account yet.
"""

import logging
from datetime import date, datetime

from models import EmailCampaign, EmailListSnapshot, db

from .base import OAuthConnector
from .errors import AuthError, ConnectorError

log = logging.getLogger("wesley")

API = "https://api.cc.email/v3"
MAX_CAMPAIGN_PAGES = 5
STATS_BATCH = 10


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


class ConstantContactConnector(OAuthConnector):
    key = "constant_contact"
    label = "Constant Contact"
    description = "How each email campaign performed, and how the email lists are growing."
    docs_anchor = "constant-contact"
    interval_minutes = 360
    authorize_url = "https://authz.constantcontact.com/oauth2/default/v1/authorize"
    token_url = "https://authz.constantcontact.com/oauth2/default/v1/token"
    scopes = ["campaign_data", "contact_data", "offline_access"]
    client_id_env = "CONSTANT_CONTACT_CLIENT_ID"
    client_secret_env = "CONSTANT_CONTACT_CLIENT_SECRET"
    basic_auth_for_token = True
    min_interval_seconds = 0.3

    def _get(self, ctx, path, **params):
        return ctx.client.get(API + path, params=params or None, headers=self.auth_headers())

    def sync(self, ctx):
        self.sync_campaigns(ctx)
        self.sync_lists(ctx)

    def _campaign_activities(self, ctx):
        """(activity_id, campaign name) for finished primary and resend activities."""
        found, path, params = [], "/emails", {"limit": 50}
        for _ in range(MAX_CAMPAIGN_PAGES):
            body = self._get(ctx, path, **params) if params else ctx.client.get(
                "https://api.cc.email" + path, headers=self.auth_headers())
            for c in body.get("campaigns", []):
                ctx.store_raw("campaign", c.get("campaign_id"), c)
                for a in c.get("campaign_activities", []):
                    if a.get("role") in ("primary_email", "resend"):
                        found.append((a["campaign_activity_id"], c.get("name"), c))
            nxt = ((body.get("_links") or {}).get("next") or {}).get("href")
            if not nxt:
                break
            path, params = nxt, None
        return found

    def sync_campaigns(self, ctx):
        activities = self._campaign_activities(ctx)
        names = {a[0]: (a[1], a[2]) for a in activities}
        ids = list(names)
        for i in range(0, len(ids), STATS_BATCH):
            chunk = ids[i:i + STATS_BATCH]
            body = self._get(ctx, "/reports/stats/email_campaign_activities/" + ",".join(chunk))
            for r in body.get("results", []):
                aid = r.get("campaign_activity_id")
                stats = r.get("stats") or {}
                name, campaign = names.get(aid, (None, {}))
                ctx.store_raw("campaign_stats", aid, r)
                changed = ctx.upsert(EmailCampaign, {"activity_id": aid}, {
                    "name": (name or "")[:300] or None, "sent_at": parse_time(campaign.get("updated_at")),
                    "sends": stats.get("em_sends"), "opens": stats.get("em_opens"),
                    "clicks": stats.get("em_clicks"), "bounces": stats.get("em_bounces"),
                    "optouts": stats.get("em_optouts"), "forwards": stats.get("em_forwards"),
                })
                ctx.note("campaigns", fetched=1, changed=int(changed))
            for err in body.get("errors", []):
                ctx.warn("Campaign stats unavailable for one email: " + str(err)[:120])

    def sync_lists(self, ctx):
        today = date.today()
        body = self._get(ctx, "/contact_lists", include_membership_count="all", limit=1000)
        for lst in body.get("lists", []):
            count = lst.get("membership_count")
            if count is None:
                continue
            changed = ctx.upsert(EmailListSnapshot, {"day": today, "list_id": lst["list_id"]},
                                 {"list_name": (lst.get("name") or "")[:200], "members": int(count)})
            ctx.note("lists", fetched=1, changed=int(changed))
        total = self._get(ctx, "/contacts", limit=1, include_count="true", status="all")
        if total.get("contacts_count") is not None:
            changed = ctx.upsert(EmailListSnapshot, {"day": today, "list_id": "all"},
                                 {"list_name": "All contacts", "members": int(total["contacts_count"])})
            ctx.note("lists", fetched=1, changed=int(changed))
