"""Shared helper functions used across multiple route modules."""

import ipaddress
import json
import secrets
import logging
from urllib.parse import urlparse

from datetime import datetime
from flask import session, request, abort
from flask_login import current_user

from config import (
    DEFAULT_BOT_NAME, DEFAULT_WELCOME, DEFAULT_COLOR, DEFAULT_SUBTITLE,
    DEFAULT_SYSTEM_PROMPT,
)
from denominations import PROFILE, render_local_practice_block
from gemini_client import (  # noqa: F401  (re-exported for existing imports)
    call_gemini, friendly_gemini_error, sse_event, stream_gemini,
)
from prompts import STAFF_SYSTEM_PROMPT, WESLEY_CORE
from models import SystemPrompt, TextSnippet, QnAPair
from organization import get_org

log = logging.getLogger("wesley")


def org_tz():
    """The organization's IANA timezone, falling back to the configured default."""
    from zoneinfo import ZoneInfo
    from config import DEFAULT_TIMEZONE
    name = get_org().timezone or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/New_York")


def local_now():
    """Current wall-clock datetime where the church is, not UTC.

    Visitor-facing dates must be church-local: on a Saturday evening in
    Georgia, UTC has already rolled into Sunday.
    """
    return datetime.now(org_tz())


def utc_to_local(dt):
    """Convert a naive-UTC datetime (as stored in the DB) to church-local."""
    from zoneinfo import ZoneInfo
    if dt is None:
        return None
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(org_tz())


def iso_utc(dt):
    """Serialize a DB datetime as ISO 8601 with an explicit UTC marker.

    Timestamps are stored naive-UTC (datetime.utcnow); without the trailing
    "Z" browsers parse them as local time, skewing displayed times by the
    viewer's UTC offset.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


# ── Branding ─────────────────────────────────────────────────────────────────

def build_branding_dict(church=None) -> dict:
    """The public chatbot branding as JSON, from the organization row."""
    church = church or get_org()
    try:
        sugs = json.loads(church.starter_questions) if church.starter_questions else []
    except (ValueError, TypeError):
        sugs = []
    return {
        "bot_name":          church.bot_name       or DEFAULT_BOT_NAME,
        "bot_subtitle":      church.bot_subtitle    or DEFAULT_SUBTITLE,
        "welcome_message":   church.welcome_message or DEFAULT_WELCOME,
        "primary_color":     church.primary_color   or DEFAULT_COLOR,
        "church_city":       church.church_city     or "",
        "starter_questions": sugs,
    }


# ── System prompt builder ────────────────────────────────────────────────────
#
# Prompt layers, in assembly order:
#   1. Current date + denominationally neutral Wesley AI core
#   2. Exactly one selected denominational profile
#   3. Church identity and branding
#   4. Approved local-practice context
#   5. Approved Q&A and text snippets
#   6. Staff or public-widget behaviour rules
#
# Layer 1 controls behaviour only — never theology. Layer 2 is the only place a
# denomination is named. See docs/denominational-architecture.md.

def _platform_prompt() -> str:
    """The admin-editable public-chatbot instructions."""
    prompt_row = SystemPrompt.query.get(1)
    content = (prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT) or ""
    return content.strip()


def build_system_prompt(widget: bool = False, staff: bool = False) -> str:
    """Assemble the full Gemini system instruction.

    staff=True  -> the staff assistant. Sees all approved Q&A and snippets.
    otherwise   -> the public website chatbot prompt, built by
                   ``public_knowledge`` from public content only.

    Both use the same United Methodist profile, so they cannot drift apart
    theologically.
    """
    if not staff:
        from public_knowledge import build_public_prompt
        return build_public_prompt()

    church = get_org()
    today_str = local_now().strftime("%A, %B %-d, %Y")
    base = f"Today's date is {today_str}.\n\n" + STAFF_SYSTEM_PROMPT + WESLEY_CORE
    base += PROFILE.prompt_block()

    ctx = f"\n\nYou are installed at {church.name}"
    if church.church_city:
        ctx += f", located in {church.church_city}"
    ctx += f". Your name is {church.bot_name or DEFAULT_BOT_NAME}."
    ctx += render_local_practice_block(church)

    qna_pairs = QnAPair.query.filter_by(is_active=True).all()
    qna_block = ""
    if qna_pairs:
        lines = "\n".join(f"Q: {p.question}\nA: {p.answer}" for p in qna_pairs)
        qna_block = (
            "\n\n--- Approved Q&A \u2014 Always Use These Answers Exactly ---\n"
            "If a visitor asks something matching one of these questions, use the "
            "provided answer. Do not paraphrase or modify its wording. You may append "
            "a numbered citation marker when citation instructions request one.\n\n"
            + lines
        )
    snippets = TextSnippet.query.filter_by(is_active=True).all()
    snippet_block = ""
    if snippets:
        lines = "\n".join(f"{x.title}: {x.content}" for x in snippets)
        snippet_block = "\n\n--- Additional Church Information ---\n" + lines
    return base + ctx + qna_block + snippet_block


# ── Auth helpers ─────────────────────────────────────────────────────────────

def is_admin() -> bool:
    """True for a signed-in user with the admin role."""
    return current_user.is_authenticated and current_user.has_role("admin")


# ── CSRF ─────────────────────────────────────────────────────────────────────

def csrf_token() -> str:
    """Return (and lazily create) a per-session CSRF token."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf() -> None:
    """Abort 403 if the submitted CSRF token doesn't match the session token."""
    token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
        abort(403)


def validate_csrf_json():
    """Check CSRF for JSON API endpoints.

    Returns ``(None, None)`` when the token is valid, or a ``(response, status)``
    tuple that the caller should immediately return to the client.

    CSRF validation is skipped automatically when ``app.config["TESTING"]`` is
    True so that the test suite can call API endpoints without managing tokens.
    """
    from flask import jsonify, current_app  # local import avoids circular dependency
    if current_app.config.get("TESTING"):
        return None, None
    token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
        return jsonify({"error": "CSRF validation failed."}), 403
    return None, None


# ── SSRF Protection ───────────────────────────────────────────────────────────

def is_safe_url(url: str) -> bool:
    """Validate that a URL does not point to internal/private network addresses.

    Returns True if the URL is safe to fetch, False if it targets a private
    or reserved IP range (SSRF risk).
    """
    import socket
    from flask import current_app, has_app_context

    # Skip SSRF check in test mode (mirrors CSRF handling pattern)
    if has_app_context() and current_app.config.get("TESTING"):
        return True

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    try:
        # Resolve hostname and check all resulting IPs
        addrinfos = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in addrinfos:
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
    except (socket.gaierror, ValueError):
        # If DNS resolution fails, reject the URL
        return False

    return True
