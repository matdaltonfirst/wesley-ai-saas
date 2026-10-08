"""The connector interface: one small contract every integration follows.

A connector knows how to authenticate, fetch, and normalize. It does not know
how to schedule itself, record runs, encrypt tokens, back off, or report health:
the framework does that, identically for all of them. Adding or swapping a tool
means writing one subclass and registering it.

Data flow for one sync::

    runner.run_sync(key)
      -> SyncRun row (start)
      -> connector.sync(ctx)
           ctx.client.get(...)            retries, rate limits, token refresh
           ctx.store_raw(resource, id, p) last raw payload, kept apart
           ctx.upsert(Model, lookup, vals) idempotent normalized rows
      -> SyncRun row (end, counts, plain-language error)
      -> Integration row (last success, last error)
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

import crypto
from models import IntegrationToken, RawPayload, db

from .errors import AuthError, ConnectorError, NotConfigured
from .http import HttpClient

log = logging.getLogger("wesley")


@dataclass
class Health:
    """What the Integrations page shows for one connector, in plain language."""
    status: str            # not_set_up | waiting | healthy | stale | failing | needs_reconnect | off
    headline: str
    detail: str = ""
    action: str = ""       # a verb phrase for a button, e.g. "Reconnect"
    last_success_at: datetime = None


class SyncContext:
    """What a connector's ``sync`` is handed: a client, storage helpers, counters."""

    def __init__(self, connector, run, client):
        self.connector = connector
        self.run = run
        self.client = client
        self.fetched = 0
        self.changed = 0
        self.warnings = []
        self.counts = {}

    def note(self, resource: str, fetched: int = 0, changed: int = 0):
        self.fetched += fetched
        self.changed += changed
        row = self.counts.setdefault(resource, {"fetched": 0, "changed": 0})
        row["fetched"] += fetched
        row["changed"] += changed

    def warn(self, message: str):
        """A problem with one item that should not fail the whole run."""
        if len(self.warnings) < 20:
            self.warnings.append(message[:300])

    def store_raw(self, resource: str, external_id, payload) -> None:
        """Keep the last raw response for an object, replacing the previous one."""
        text = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        row = RawPayload.query.filter_by(
            integration=self.connector.key, resource=resource, external_id=str(external_id)).first()
        if row is None:
            db.session.add(RawPayload(integration=self.connector.key, resource=resource,
                                      external_id=str(external_id), payload=text, payload_hash=digest))
        elif row.payload_hash != digest:
            row.payload, row.payload_hash, row.fetched_at = text, digest, datetime.utcnow()

    def upsert(self, model, lookup: dict, values: dict) -> bool:
        """Create or update one normalized row. True if anything changed.

        Idempotent: running the same sync twice changes nothing the second time.
        """
        row = model.query.filter_by(**lookup).first()
        if row is None:
            db.session.add(model(**lookup, **values))
            return True
        changed = False
        for key, value in values.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                changed = True
        return changed


class Connector:
    """Subclass this. Override the attributes and ``sync``; override the rest as needed."""

    key = ""
    label = ""
    description = ""
    docs_anchor = ""                # anchor in docs/INTEGRATIONS.md
    interval_minutes = 60           # how often the scheduler runs it
    stale_after_minutes = None      # healthy -> stale threshold; default 3 x interval
    requires_oauth = False
    supports_webhook = False
    min_interval_seconds = 0.2      # gap between calls (the provider's rate limit)
    needs_permission = "integrations.manage"
    request_fn = None               # tests inject a fake transport here

    # ── Setup state ──────────────────────────────────────────────────────────
    def env_vars(self) -> list:
        """Names of environment variables that must be set for this to work."""
        return []

    def configured(self) -> bool:
        return all(os.getenv(v) for v in self.env_vars())

    def not_configured_message(self) -> str:
        missing = [v for v in self.env_vars() if not os.getenv(v)]
        return "Not set up on this server yet. Missing: " + ", ".join(missing) + "." if missing else ""

    def connected(self) -> bool:
        return True

    def waiting_for_access(self) -> str:
        """A message if we are waiting on the provider (e.g. API access not yet granted)."""
        return ""

    # ── Work ─────────────────────────────────────────────────────────────────
    def sync(self, ctx: SyncContext) -> None:
        raise NotImplementedError

    def make_client(self, on_unauthorized=None) -> HttpClient:
        return HttpClient(self.label or self.key, min_interval=self.min_interval_seconds,
                          on_unauthorized=on_unauthorized, request_fn=self.request_fn)

    # ── Webhooks ─────────────────────────────────────────────────────────────
    def verify_webhook(self, headers, body: bytes) -> bool:
        return False

    # ── Auth headers ─────────────────────────────────────────────────────────
    def auth_headers(self) -> dict:
        return {}


class OAuthConnector(Connector):
    """Standard OAuth 2.0 authorization-code flow with refresh, tokens encrypted at rest."""

    requires_oauth = True
    authorize_url = ""
    token_url = ""
    scopes: list = []
    client_id_env = ""
    client_secret_env = ""
    basic_auth_for_token = False      # send client credentials as HTTP Basic (Constant Contact)
    extra_authorize_params: dict = {}
    refresh_margin = timedelta(minutes=5)

    def env_vars(self):
        return [self.client_id_env, self.client_secret_env]

    # Redirect URI registered with the provider: <APP_URL>/integrations/<key>/callback
    def redirect_uri(self) -> str:
        from config import APP_URL
        return APP_URL.rstrip("/") + f"/integrations/{self.key}/callback"

    def authorize_redirect(self, state: str) -> str:
        params = {
            "client_id": os.getenv(self.client_id_env, ""),
            "redirect_uri": self.redirect_uri(),
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            **self.extra_authorize_params,
        }
        return self.authorize_url + "?" + urlencode(params)

    def _token_request(self, form: dict) -> dict:
        client = Connector.make_client(self)      # no refresh hook: this IS the refresh
        auth = None
        form = dict(form)
        if self.basic_auth_for_token:
            auth = (os.getenv(self.client_id_env, ""), os.getenv(self.client_secret_env, ""))
        else:
            form["client_id"] = os.getenv(self.client_id_env, "")
            form["client_secret"] = os.getenv(self.client_secret_env, "")
        try:
            return client.post(self.token_url, data=form, auth=auth,
                               headers={"Accept": "application/json"})
        except ConnectorError as exc:
            if exc.kind == "upstream":
                raise AuthError(f"{self.label} would not complete sign-in. Try connecting again.") from exc
            raise

    def exchange_code(self, code: str, user_id=None) -> IntegrationToken:
        data = self._token_request({
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.redirect_uri(),
        })
        return self._save_tokens(data, user_id=user_id)

    def _save_tokens(self, data: dict, user_id=None, label=None) -> IntegrationToken:
        if not data.get("access_token"):
            raise AuthError(f"{self.label} did not return a sign-in.")
        row = IntegrationToken.query.filter_by(key=self.key).first()
        if row is None:
            row = IntegrationToken(key=self.key, access_token="")
            db.session.add(row)
        row.access_token = crypto.encrypt(data["access_token"])
        # Some providers rotate refresh tokens, some never return one on refresh.
        if data.get("refresh_token"):
            row.refresh_token = crypto.encrypt(data["refresh_token"])
        row.expires_at = datetime.utcnow() + timedelta(seconds=int(data.get("expires_in") or 3600))
        row.scope = data.get("scope") or row.scope or " ".join(self.scopes)
        if label:
            row.account_label = label
        if user_id is not None:
            row.connected_by_id = user_id
        db.session.commit()
        return row

    def token_row(self):
        return IntegrationToken.query.filter_by(key=self.key).first()

    def connected(self) -> bool:
        return self.token_row() is not None

    def refresh(self) -> IntegrationToken:
        row = self.token_row()
        if row is None or not row.refresh_token:
            raise AuthError(f"{self.label} is not connected. Connect it from the Integrations page.")
        data = self._token_request({
            "grant_type": "refresh_token", "refresh_token": crypto.decrypt(row.refresh_token)})
        return self._save_tokens(data)

    def access_token(self) -> str:
        """A valid access token, refreshed first if it is about to expire."""
        row = self.token_row()
        if row is None:
            raise AuthError(f"{self.label} is not connected. Connect it from the Integrations page.")
        if row.expires_at and row.expires_at - datetime.utcnow() < self.refresh_margin:
            row = self.refresh()
        return crypto.decrypt(row.access_token)

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def make_client(self, on_unauthorized=None):
        def refresh_and_retry(headers):
            self.refresh()
            return {**headers, **self.auth_headers()}
        return super().make_client(on_unauthorized=on_unauthorized or refresh_and_retry)

    def disconnect(self) -> None:
        IntegrationToken.query.filter_by(key=self.key).delete()
        db.session.commit()


class StaticTokenConnector(Connector):
    """A connector authenticated by a token an admin pastes in (Meta page token, API keys)."""

    requires_oauth = False
    token_label = "Access token"

    def token_row(self):
        return IntegrationToken.query.filter_by(key=self.key).first()

    def connected(self) -> bool:
        return self.token_row() is not None

    def save_token(self, token: str, label: str = None, scope: str = None, user_id=None):
        row = self.token_row()
        if row is None:
            row = IntegrationToken(key=self.key, access_token="")
            db.session.add(row)
        row.access_token = crypto.encrypt(token.strip())
        row.account_label = label
        row.scope = scope
        row.connected_by_id = user_id
        db.session.commit()

    def access_token(self) -> str:
        row = self.token_row()
        if row is None:
            raise AuthError(f"{self.label} is not connected yet.")
        return crypto.decrypt(row.access_token)

    def disconnect(self) -> None:
        IntegrationToken.query.filter_by(key=self.key).delete()
        db.session.commit()
