"""Text In Church: contacts, conversations, messages and connect cards.

API: ``https://api.textinchurch.com/API/1_0/``, Bearer token, form-encoded, JSON
responses, ``limit`` (max 1500) and ``offset`` paging. Documentation:
https://api-docs.textinchurch.com (reference pages ``contact``, ``conversation``,
``message``, ``connect card submission`` and ``webhook subscriptions``).

**Access is gated by Text In Church.** Their support must enable the API for the
account first (email support@textinchurch.com). Until a token exists the connector
reports "waiting for access"; ``TEXT_IN_CHURCH_MOCK=1`` runs it against the mock
adapter (``connectors/tic_mock.py``) so everything built on it can be developed
and demonstrated without a live account.

Auth options, either works:
* a **personal API key** an admin creates under Account Settings, Developer API
  (pasted on the Integrations page; stored encrypted); or
* **OAuth 2.0** (``oauthorize.php``) with ``TEXT_IN_CHURCH_CLIENT_ID``/``_SECRET``.
  Access tokens last two hours; refresh tokens rotate on every use.

What is stored: contact id, first and last name, source, status flags and dates
(no phone, email or address); conversations; message content and direction (only
visible to roles with ``data.text_in_church``); connect card submissions.

The analytics endpoints are limited, so the numbers staff need (message volume,
new contacts, connect cards, response times, guests awaiting follow-up) are
computed from this raw data in ``text_in_church_metrics.py``.

Field names below come straight from the reference pages. Two facts the docs do not
state are handled defensively: the format of ``msg_send_time``/``msg_stamp`` (several
formats are accepted) and the meaning of ``msg_incoming`` (truthy means incoming).
"""

import logging
import os
from datetime import datetime, timedelta

from models import (
    TicConnectCard, TicContact, TicConversation, TicMessage, db,
)

from . import tic_mock
from .base import Connector, OAuthConnector
from .errors import AuthError, ConnectorError

log = logging.getLogger("wesley")

BASE = "https://api.textinchurch.com/API/1_0/"
PAGE = 1500
MAX_PAGES = 40
MESSAGE_WINDOW_DAYS = 120


def parse_time(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            pass
    if text.isdigit():
        try:
            return datetime.utcfromtimestamp(int(text))
        except (ValueError, OverflowError):
            return None
    return None


def truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


class TextInChurchConnector(OAuthConnector):
    key = "text_in_church"
    label = "Text In Church"
    description = "Texting activity: new contacts, conversations, connect cards, and who is waiting for a reply."
    docs_anchor = "text-in-church"
    interval_minutes = 10
    supports_webhook = True
    authorize_url = BASE + "oauthorize.php"
    token_url = BASE + "oauthorize.php"
    scopes: list = []
    client_id_env = "TEXT_IN_CHURCH_CLIENT_ID"
    client_secret_env = "TEXT_IN_CHURCH_CLIENT_SECRET"
    min_interval_seconds = 0.3

    # ── Setup state: a pasted API key, OAuth, or the mock ────────────────────
    def mock(self) -> bool:
        return os.getenv("TEXT_IN_CHURCH_MOCK", "").lower() in ("1", "true", "yes")

    def configured(self) -> bool:
        return True                      # a key can be pasted without server settings

    def connected(self) -> bool:
        return self.mock() or self.token_row() is not None

    def waiting_for_access(self) -> str:
        if self.mock() or self.token_row() is not None:
            return ""
        return ("Waiting for Text In Church to enable API access. Email support@textinchurch.com to "
                "ask for it, then paste the API key on this page (Account Settings, Developer API).")

    def access_token(self) -> str:
        if self.mock():
            return "mock"
        return super().access_token()

    def refresh(self):
        row = self.token_row()
        if row is not None and not row.refresh_token:
            raise AuthError("The Text In Church API key was not accepted. Create a new one and paste it again.")
        return super().refresh()

    def save_api_key(self, key: str, user_id=None):
        """Store a personal API key (no refresh token: it does not expire on a schedule)."""
        import crypto
        from models import IntegrationToken
        row = self.token_row() or IntegrationToken(key=self.key, access_token="")
        db.session.add(row)
        row.access_token = crypto.encrypt(key.strip())
        row.refresh_token = None
        row.expires_at = None
        row.account_label = "Personal API key"
        row.connected_by_id = user_id
        db.session.commit()

    # ── Calls ────────────────────────────────────────────────────────────────
    def get(self, ctx, endpoint, **params):
        if self.mock():
            return tic_mock.respond(endpoint, params)
        return ctx.client.get(BASE + endpoint, params=params, headers=self.auth_headers())

    def paged(self, ctx, endpoint, **params):
        offset = 0
        for _ in range(MAX_PAGES):
            rows = self.get(ctx, endpoint, limit=PAGE, offset=offset, **params)
            if isinstance(rows, dict):          # tolerate an envelope the docs do not describe
                rows = rows.get("data") or rows.get("results") or []
            if not rows:
                return
            yield rows
            if len(rows) < PAGE:
                return
            offset += PAGE

    # ── Sync ─────────────────────────────────────────────────────────────────
    def sync(self, ctx):
        self.sync_contacts(ctx)
        self.sync_conversations(ctx)
        self.sync_messages(ctx)
        self.sync_connect_cards(ctx)

    def sync_contacts(self, ctx):
        for rows in self.paged(ctx, "contact.php", order_by="contact_id", sort_dir="DESC"):
            for c in rows:
                cid = str(c.get("contact_id"))
                ctx.store_raw("contact", cid, {k: v for k, v in c.items()
                                               if k in ("contact_id", "contact_first_name", "contact_last_name",
                                                        "contact_create_date", "contact_source", "contact_active",
                                                        "contact_optout_sms")})   # no phone, email or address kept
                changed = ctx.upsert(TicContact, {"contact_id": cid}, {
                    "first_name": (c.get("contact_first_name") or None), "last_name": (c.get("contact_last_name") or None),
                    "created_at": parse_time(c.get("contact_create_date")), "source": (c.get("contact_source") or None),
                    "active": truthy(c.get("contact_active", 1)), "optout_sms": truthy(c.get("contact_optout_sms", 0)),
                })
                ctx.note("contacts", fetched=1, changed=int(changed))

    def sync_conversations(self, ctx):
        for rows in self.paged(ctx, "conversation.php", order_by="conv_id", sort_dir="DESC"):
            for c in rows:
                cid = str(c.get("conv_id"))
                ctx.store_raw("conversation", cid, c)
                changed = ctx.upsert(TicConversation, {"conv_id": cid}, {
                    "contact_id": str(c.get("contact_id")) if c.get("contact_id") else None,
                    "archived": truthy(c.get("conv_archived", 0)),
                })
                ctx.note("conversations", fetched=1, changed=int(changed))

    def sync_messages(self, ctx):
        since = (datetime.utcnow() - timedelta(days=MESSAGE_WINDOW_DAYS)).strftime("%Y-%m-%d")
        for rows in self.paged(ctx, "message.php", start_date=since, order_by="msg_id", sort_dir="DESC"):
            for m in rows:
                mid = str(m.get("msg_id"))
                ctx.store_raw("message", mid, {k: v for k, v in m.items() if k != "msg_content"})
                changed = ctx.upsert(TicMessage, {"msg_id": mid}, {
                    "conv_id": str(m.get("conv_id")) if m.get("conv_id") else None,
                    "incoming": truthy(m.get("msg_incoming", 0)),
                    "sent_at": parse_time(m.get("msg_send_time") or m.get("msg_stamp")),
                    "content": m.get("msg_content"), "automated": truthy(m.get("automated", 0)),
                })
                ctx.note("messages", fetched=1, changed=int(changed))

    def sync_connect_cards(self, ctx):
        try:
            for rows in self.paged(ctx, "connectCardSubmission.php", order_by="id", sort_dir="DESC"):
                for s in rows:
                    sid = str(s.get("id") or s.get("submission_id"))
                    ctx.store_raw("connect_card", sid, s)
                    changed = ctx.upsert(TicConnectCard, {"submission_id": sid}, {
                        "contact_id": str(s.get("contact_id")) if s.get("contact_id") else None,
                        "collection": (s.get("collection_name") or s.get("collection_id") and str(s.get("collection_id")) or None),
                        "submitted_at": parse_time(s.get("created") or s.get("date_created") or s.get("submitted")),
                    })
                    ctx.note("connect_cards", fetched=1, changed=int(changed))
        except ConnectorError as exc:
            if exc.kind == "auth":
                raise
            ctx.warn("Connect cards could not be read: " + str(exc))

    # ── Webhook ──────────────────────────────────────────────────────────────
    def verify_webhook(self, headers, body: bytes) -> bool:
        """Text In Church's docs do not describe a signature, so a shared secret in the URL is used.

        The webhook URL registered with them must be
        ``/webhooks/text_in_church?token=<TEXT_IN_CHURCH_WEBHOOK_TOKEN>``. The payload is
        never trusted for data; it only triggers a sync.
        """
        import hmac
        from flask import request
        expected = os.getenv("TEXT_IN_CHURCH_WEBHOOK_TOKEN", "")
        got = request.args.get("token", "")
        return bool(expected) and hmac.compare_digest(expected, got)
