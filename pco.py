"""Planning Center integration — OAuth, API client, and guest-connection sync.

Each church connects its own Planning Center account via OAuth ("Connect
Planning Center" in Settings → Integrations). Wesley then pushes widget
Guest Connections into PCO People: find-or-create the person, attach a
context note, and optionally add them to a follow-up workflow.
"""

import logging
import base64
import hashlib
import threading
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, or_

from models import db, PcoConnection, GuestConnection
from config import (
    PCO_CLIENT_ID, PCO_CLIENT_SECRET, PCO_TOKEN_ENCRYPTION_KEY,
    PCO_API_BASE, APP_URL,
)

log = logging.getLogger("wesley")

TIMEOUT_S = 20
NOTE_CATEGORY_NAME = "Wesley AI"
TOKEN_PREFIX = "enc:"
MAX_SYNC_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (300, 1800, 7200)
STUCK_SYNC_MINUTES = 10

_refresh_locks_guard = threading.Lock()
_refresh_locks = {}


class PcoError(Exception):
    """A Planning Center API problem worth surfacing to church staff."""


def is_configured() -> bool:
    return bool(PCO_CLIENT_ID and PCO_CLIENT_SECRET)


def _fernet() -> Fernet:
    """Build the token cipher from a stable secret already held by Railway."""
    material = PCO_TOKEN_ENCRYPTION_KEY or PCO_CLIENT_SECRET
    if not material:
        raise PcoError("Planning Center token encryption is not configured.")
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(token: str) -> str:
    if not token or token.startswith(TOKEN_PREFIX):
        return token
    encrypted = _fernet().encrypt(token.encode("utf-8")).decode("ascii")
    return TOKEN_PREFIX + encrypted


def decrypt_token(token: str) -> str:
    """Decrypt a stored token; plaintext values support zero-downtime migration."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return token
    try:
        return _fernet().decrypt(token[len(TOKEN_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise PcoError("Planning Center token encryption key is invalid. Reconnect the account.") from exc


def _stored_token(conn: PcoConnection, field: str) -> str:
    stored = getattr(conn, field)
    plaintext = decrypt_token(stored)
    if stored and not stored.startswith(TOKEN_PREFIX):
        setattr(conn, field, encrypt_token(stored))
        db.session.commit()
    return plaintext


def _refresh_lock(connection_id: int):
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(connection_id, threading.Lock())


def redirect_uri() -> str:
    return APP_URL.rstrip("/") + "/pco/callback"


def authorize_url(state: str) -> str:
    return PCO_API_BASE + "/oauth/authorize?" + urlencode({
        "client_id": PCO_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "people",
        "state": state,
    })


def exchange_code(code: str) -> dict:
    """Trade the OAuth callback code for access + refresh tokens."""
    resp = requests.post(PCO_API_BASE + "/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "client_id": PCO_CLIENT_ID,
        "client_secret": PCO_CLIENT_SECRET,
        "redirect_uri": redirect_uri(),
    }, timeout=TIMEOUT_S)
    if resp.status_code != 200:
        log.error("PCO token exchange failed (%d): %s", resp.status_code, resp.text[:300])
        raise PcoError("Planning Center did not accept the sign-in. Please try again.")
    return resp.json()


def _refresh_tokens(
    conn: PcoConnection, force: bool = False, stale_access_token: str = None
) -> None:
    with _refresh_lock(conn.id):
        db.session.refresh(conn)
        current_access_token = _stored_token(conn, "access_token")
        if force and stale_access_token and current_access_token != stale_access_token:
            return  # another thread already refreshed the token
        if not force and conn.token_expires_at > datetime.utcnow() + timedelta(minutes=2):
            return
        current_refresh_token = _stored_token(conn, "refresh_token")
        resp = requests.post(PCO_API_BASE + "/oauth/token", data={
            "grant_type": "refresh_token",
            "refresh_token": current_refresh_token,
            "client_id": PCO_CLIENT_ID,
            "client_secret": PCO_CLIENT_SECRET,
        }, timeout=TIMEOUT_S)
        if resp.status_code != 200:
            log.error("PCO token refresh failed for church_id=%d (%d): %s",
                      conn.church_id, resp.status_code, resp.text[:300])
            raise PcoError(
                "The Planning Center connection has expired. Please reconnect it "
                "in Settings → Integrations."
            )
        tokens = resp.json()
        conn.access_token = encrypt_token(tokens["access_token"])
        if tokens.get("refresh_token"):
            conn.refresh_token = encrypt_token(tokens["refresh_token"])
        conn.token_expires_at = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 7200))
        db.session.commit()


def api(conn: PcoConnection, method: str, path: str, json=None, params=None) -> dict:
    """Authenticated PCO request with automatic token refresh."""
    if conn.token_expires_at <= datetime.utcnow() + timedelta(minutes=2):
        _refresh_tokens(conn)

    for attempt in (1, 2):
        access_token = _stored_token(conn, "access_token")
        resp = requests.request(
            method, PCO_API_BASE + path,
            headers={"Authorization": f"Bearer {access_token}"},
            json=json, params=params, timeout=TIMEOUT_S,
        )
        if resp.status_code == 401 and attempt == 1:
            _refresh_tokens(conn, force=True, stale_access_token=access_token)
            continue
        break

    if resp.status_code not in (200, 201, 204):
        detail = ""
        try:
            errors = resp.json().get("errors", [])
            detail = "; ".join(e.get("detail") or e.get("title", "") for e in errors)[:200]
        except Exception:
            pass
        log.error("PCO API %s %s failed (%d): %s", method, path, resp.status_code, detail)
        raise PcoError(f"Planning Center error ({resp.status_code}): {detail or 'request failed'}")
    return resp.json() if resp.status_code != 204 and resp.content else {}


def get_organization_name(conn: PcoConnection) -> str:
    data = api(conn, "GET", "/people/v2")
    return data.get("data", {}).get("attributes", {}).get("name", "")


def list_workflows(conn: PcoConnection) -> list[dict]:
    data = api(conn, "GET", "/people/v2/workflows", params={"per_page": 100})
    return [
        {"id": w["id"], "name": w["attributes"].get("name", "")}
        for w in data.get("data", [])
    ]


# ── Guest connection sync ────────────────────────────────────────────────────

def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], "Guest"
    return parts[0], " ".join(parts[1:])


def _find_person_by_email(conn: PcoConnection, email: str):
    data = api(conn, "GET", "/people/v2/emails",
               params={"where[address]": email, "per_page": 1})
    for row in data.get("data", []):
        person = row.get("relationships", {}).get("person", {}).get("data")
        if person:
            return person["id"]
    return None


def _create_person(conn: PcoConnection, gc) -> str:
    first, last = _split_name(gc.name)
    person = api(conn, "POST", "/people/v2/people", json={
        "data": {"type": "Person", "attributes": {"first_name": first, "last_name": last}}
    })
    return person["data"]["id"]


def _add_email(conn: PcoConnection, person_id: str, gc) -> None:
    api(conn, "POST", f"/people/v2/people/{person_id}/emails", json={
        "data": {"type": "Email", "attributes": {"address": gc.email, "location": "Home"}}
    })


def _add_phone(conn: PcoConnection, person_id: str, gc) -> None:
    if not gc.phone:
        return
    api(conn, "POST", f"/people/v2/people/{person_id}/phone_numbers", json={
        "data": {"type": "PhoneNumber",
                 "attributes": {"number": gc.phone, "location": "Mobile"}}
    })


def _ensure_note_category(conn: PcoConnection) -> str:
    data = api(conn, "GET", "/people/v2/note_categories", params={"per_page": 100})
    for cat in data.get("data", []):
        if cat["attributes"].get("name", "").lower() == NOTE_CATEGORY_NAME.lower():
            return cat["id"]
    created = api(conn, "POST", "/people/v2/note_categories", json={
        "data": {"type": "NoteCategory", "attributes": {"name": NOTE_CATEGORY_NAME}}
    })
    return created["data"]["id"]


def _add_context_note(conn: PcoConnection, person_id: str, gc) -> None:
    note = (
        f"Connected via website chat on {gc.created_at.strftime('%B %-d, %Y')}. "
        f"Interest: {gc.interest_area or 'General Interest'}."
    )
    if gc.opening_message:
        note += f' First message: "{gc.opening_message[:300]}"'
    category_id = _ensure_note_category(conn)
    api(conn, "POST", f"/people/v2/people/{person_id}/notes", json={
        "data": {
            "type": "Note",
            "attributes": {"note": note, "note_category_id": category_id},
        }
    })


def _add_to_workflow(conn: PcoConnection, person_id: str) -> None:
    if not conn.workflow_id:
        return
    api(conn, "POST", f"/people/v2/workflows/{conn.workflow_id}/cards", json={
        "data": {
            "type": "WorkflowCard",
            "relationships": {
                "person": {"data": {"type": "Person", "id": person_id}},
            },
        }
    })


def person_url(person_id: str) -> str:
    return f"https://people.planningcenteronline.com/people/AC{person_id}"


def queue_guest_sync(gc) -> None:
    """Persist a sync request before any background work starts."""
    gc.pco_sync_status = "pending"
    gc.pco_sync_error = None
    gc.pco_next_retry_at = datetime.utcnow()
    gc.pco_sync_started_at = None
    db.session.commit()


def _record_step(gc, **values) -> None:
    for field, value in values.items():
        setattr(gc, field, value)
    db.session.commit()


def _record_sync_failure(guest_id: int, exc: Exception) -> None:
    db.session.rollback()
    gc = db.session.get(GuestConnection, guest_id)
    if not gc:
        return
    gc.pco_sync_attempts = (gc.pco_sync_attempts or 0) + 1
    gc.pco_sync_error = str(exc)[:500]
    gc.pco_sync_started_at = None
    if gc.pco_sync_attempts >= MAX_SYNC_ATTEMPTS:
        gc.pco_sync_status = "failed"
        gc.pco_next_retry_at = None
    else:
        gc.pco_sync_status = "partial" if gc.pco_person_id else "pending"
        delay_index = min(gc.pco_sync_attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)
        gc.pco_next_retry_at = datetime.utcnow() + timedelta(
            seconds=RETRY_DELAYS_SECONDS[delay_index]
        )
    db.session.commit()


def sync_guest_connection(gc, force: bool = False) -> bool:
    """Resume a guest sync from its first unfinished durable step."""
    conn = PcoConnection.query.filter_by(church_id=gc.church_id).first()
    if not conn:
        return False
    if gc.pco_sync_status == "syncing" and not force:
        cutoff = datetime.utcnow() - timedelta(minutes=STUCK_SYNC_MINUTES)
        if gc.pco_sync_started_at and gc.pco_sync_started_at > cutoff:
            return False
    if force and gc.pco_sync_status == "failed":
        gc.pco_sync_attempts = 0

    guest_id = gc.id
    gc.pco_sync_status = "syncing"
    gc.pco_sync_started_at = datetime.utcnow()
    gc.pco_sync_error = None
    db.session.commit()
    try:
        person_id = gc.pco_person_id
        if not person_id:
            person_id = _find_person_by_email(conn, gc.email)
            if person_id:
                _record_step(gc, pco_person_id=person_id, pco_email_synced=True)
            else:
                person_id = _create_person(conn, gc)
                # Persist immediately: an email failure must not create another person.
                _record_step(gc, pco_person_id=person_id)

        if not gc.pco_email_synced:
            _add_email(conn, person_id, gc)
            _record_step(gc, pco_email_synced=True)

        if not gc.pco_phone_synced:
            _add_phone(conn, person_id, gc)
            _record_step(gc, pco_phone_synced=True)

        if not gc.pco_note_synced:
            _add_context_note(conn, person_id, gc)
            _record_step(gc, pco_note_synced=True)

        if not gc.pco_workflow_synced:
            _add_to_workflow(conn, person_id)
            _record_step(gc, pco_workflow_synced=True)

        _record_step(
            gc,
            pco_sync_status="synced",
            pco_sync_error=None,
            pco_synced_at=datetime.utcnow(),
            pco_next_retry_at=None,
            pco_sync_started_at=None,
        )
        log.info("PCO sync: guest %d -> person %s (church_id=%d)",
                 gc.id, person_id, gc.church_id)
        return True
    except Exception as exc:
        _record_sync_failure(guest_id, exc)
        log.error("PCO sync failed for guest %d: %s", guest_id, exc)
        return False


def reconcile_pending_syncs(limit: int = 50) -> int:
    """Retry due or interrupted auto-sync work from the existing database."""
    now = datetime.utcnow()
    stuck_before = now - timedelta(minutes=STUCK_SYNC_MINUTES)
    guests = (
        GuestConnection.query
        .join(PcoConnection, PcoConnection.church_id == GuestConnection.church_id)
        .filter(
            PcoConnection.auto_sync.is_(True),
            or_(
                GuestConnection.pco_sync_status.in_(("pending", "partial")),
                and_(
                    GuestConnection.pco_sync_status == "syncing",
                    GuestConnection.pco_sync_started_at <= stuck_before,
                ),
            ),
            or_(
                GuestConnection.pco_next_retry_at.is_(None),
                GuestConnection.pco_next_retry_at <= now,
            ),
        )
        .order_by(GuestConnection.created_at)
        .limit(limit)
        .all()
    )
    synced = 0
    for gc in guests:
        synced += int(sync_guest_connection(gc, force=True))
    return synced
