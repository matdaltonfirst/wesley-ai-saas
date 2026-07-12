"""Planning Center integration — OAuth, API client, and guest-connection sync.

Each church connects its own Planning Center account via OAuth ("Connect
Planning Center" in Settings → Integrations). Wesley then pushes widget
Guest Connections into PCO People: find-or-create the person, attach a
context note, and optionally add them to a follow-up workflow.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

from models import db, PcoConnection
from config import PCO_CLIENT_ID, PCO_CLIENT_SECRET, PCO_API_BASE, APP_URL

log = logging.getLogger("wesley")

TIMEOUT_S = 20
NOTE_CATEGORY_NAME = "Wesley AI"


class PcoError(Exception):
    """A Planning Center API problem worth surfacing to church staff."""


def is_configured() -> bool:
    return bool(PCO_CLIENT_ID and PCO_CLIENT_SECRET)


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


def _refresh_tokens(conn: PcoConnection) -> None:
    resp = requests.post(PCO_API_BASE + "/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": conn.refresh_token,
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
    conn.access_token = tokens["access_token"]
    conn.refresh_token = tokens.get("refresh_token") or conn.refresh_token
    conn.token_expires_at = datetime.utcnow() + timedelta(seconds=tokens.get("expires_in", 7200))
    db.session.commit()


def api(conn: PcoConnection, method: str, path: str, json=None, params=None) -> dict:
    """Authenticated PCO request with automatic token refresh."""
    if conn.token_expires_at <= datetime.utcnow() + timedelta(minutes=2):
        _refresh_tokens(conn)

    for attempt in (1, 2):
        resp = requests.request(
            method, PCO_API_BASE + path,
            headers={"Authorization": f"Bearer {conn.access_token}"},
            json=json, params=params, timeout=TIMEOUT_S,
        )
        if resp.status_code == 401 and attempt == 1:
            _refresh_tokens(conn)
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
    person_id = person["data"]["id"]
    api(conn, "POST", f"/people/v2/people/{person_id}/emails", json={
        "data": {"type": "Email", "attributes": {"address": gc.email, "location": "Home"}}
    })
    if gc.phone:
        api(conn, "POST", f"/people/v2/people/{person_id}/phone_numbers", json={
            "data": {"type": "PhoneNumber",
                     "attributes": {"number": gc.phone, "location": "Mobile"}}
        })
    return person_id


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


def sync_guest_connection(gc) -> bool:
    """Push one guest connection into PCO People. Returns True on success;
    failures are recorded on the row for staff to see and retry."""
    conn = PcoConnection.query.filter_by(church_id=gc.church_id).first()
    if not conn:
        return False
    try:
        person_id = _find_person_by_email(conn, gc.email)
        if not person_id:
            person_id = _create_person(conn, gc)
        # Note and workflow failures shouldn't lose the person link
        gc.pco_person_id = person_id
        try:
            _add_context_note(conn, person_id, gc)
            _add_to_workflow(conn, person_id)
        except PcoError as exc:
            gc.pco_sync_error = f"Person synced, but: {exc}"[:500]
            gc.pco_synced_at = datetime.utcnow()
            db.session.commit()
            return True
        gc.pco_sync_error = None
        gc.pco_synced_at = datetime.utcnow()
        db.session.commit()
        log.info("PCO sync: guest %d -> person %s (church_id=%d)",
                 gc.id, person_id, gc.church_id)
        return True
    except Exception as exc:
        db.session.rollback()
        gc.pco_sync_error = str(exc)[:500]
        db.session.commit()
        log.error("PCO sync failed for guest %d: %s", gc.id, exc)
        return False
