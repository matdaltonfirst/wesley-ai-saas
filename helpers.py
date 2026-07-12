"""Shared helper functions used across multiple route modules."""

import json
import os
import secrets
import logging
import time

from datetime import datetime
from flask import redirect, url_for, session, request, abort
from flask_login import current_user
from google import genai
from google.genai import types

from config import (
    DEFAULT_BOT_NAME, DEFAULT_WELCOME, DEFAULT_COLOR, DEFAULT_SUBTITLE,
    DEFAULT_SYSTEM_PROMPT, SUPER_ADMIN_EMAIL, EXEMPT_DOMAINS, GEMINI_MODEL,
    GEMINI_FALLBACK_MODEL,
)
from models import SystemPrompt, TextSnippet, QnAPair

log = logging.getLogger("wesley")


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

def build_branding_dict(church) -> dict:
    """Return the standard branding JSON dict for a Church record."""
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

# Hardcoded staff prompt — never pulled from DB
_STAFF_SYSTEM_PROMPT = """\
You are Wesley, an AI ministry assistant built specifically for the staff and \
pastoral team of a United Methodist Church. You are grounded in Wesleyan theology \
and the Wesleyan-Methodist tradition — including the doctrines of grace, \
prevenient grace, justifying grace, sanctifying grace, and the pursuit of \
holiness of heart and life.

Your theological foundation:
- You reflect United Methodist beliefs and the Wesleyan theological tradition
- When doctrinal questions arise, answer from a Wesleyan-Arminian perspective
- You are familiar with the Articles of Religion, the General Rules, the \
Standard Sermons of John Wesley, and the theological heritage of the UMC
- You understand that United Methodists hold scripture, tradition, reason, \
and experience (the Wesleyan Quadrilateral) as sources of theological reflection

Your role is to actively help staff with:
- Sermon research, outlines, and manuscript development
- Biblical context, commentary insights, and theological reflection
- Devotional and small group content creation
- Staff communications and announcements
- Ministry planning and workflow support
- Answering questions from church documents and data sources

Tone: Think of yourself as a well-read Wesleyan ministry colleague who has deep \
knowledge of scripture, United Methodist theology, and church communications. \
Be direct, substantive, and genuinely helpful. Don't deflect to other staff \
members — the person asking IS the staff member.

When helping with sermon prep:
- Engage fully with the scripture and topic
- Offer outlines, illustrations, cultural context, and application ideas
- Ask clarifying questions to help sharpen the message
- Frame application through a Wesleyan lens — grace, transformation, \
sanctification, and love of God and neighbor

Always ground answers in uploaded church documents when relevant. If a question \
goes beyond your knowledge, say so honestly — but lean in first before stepping back.

Do not treat staff like website visitors. They are ministry professionals who \
need a capable partner, not a gatekeeper.\
"""

# Identity line guaranteed on every public (widget) prompt
_PUBLIC_IDENTITY_PREFIX = (
    "You are Wesley, a ministry assistant for a United Methodist Church. "
    "You are grounded in Wesleyan theology and the Wesleyan-Methodist tradition."
)


def build_system_prompt(church, widget: bool = False, staff: bool = False) -> str:
    """Assemble the full Gemini system instruction for a given church.

    staff=True  → staff interface: full ministry-partner prompt, no visitor restrictions
    staff=False → public widget (widget=True) or fallback: conservative visitor prompt
    """
    today_str = datetime.utcnow().strftime("%A, %B %-d, %Y")

    if staff:
        # Staff interface: use hardcoded staff prompt, never the DB prompt
        base = f"Today's date is {today_str}.\n\n" + _STAFF_SYSTEM_PROMPT
    else:
        # Public bot: use DB prompt (admin-configurable), guaranteed identity prefix
        prompt_row = SystemPrompt.query.get(1)
        db_content = prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT
        # Ensure the identity line is always present, even if admin edits the DB prompt
        if _PUBLIC_IDENTITY_PREFIX not in db_content:
            db_content = _PUBLIC_IDENTITY_PREFIX + "\n\n" + db_content
        base = f"Today's date is {today_str}.\n\n" + db_content

    ctx = f"\n\nYou are installed at {church.name}"
    if church.church_city:
        ctx += f", located in {church.church_city}"
    ctx += f". Your name is {church.bot_name or DEFAULT_BOT_NAME}."

    # Q&A and snippets injected for both staff and public
    qna_pairs = QnAPair.query.filter_by(church_id=church.id, is_active=True).all()
    qna_block = ""
    if qna_pairs:
        lines = "\n".join(f"Q: {p.question}\nA: {p.answer}" for p in qna_pairs)
        qna_block = (
            "\n\n--- Approved Q&A — Always Use These Answers Exactly ---\n"
            "If a visitor asks something matching one of these questions, use the "
            "provided answer. Do not paraphrase or modify its wording. You may append "
            "a numbered citation marker when citation instructions request one.\n\n"
            + lines
        )

    snippets = TextSnippet.query.filter_by(church_id=church.id, is_active=True).all()
    snippet_block = ""
    if snippets:
        lines = "\n".join(f"{s.title}: {s.content}" for s in snippets)
        snippet_block = "\n\n--- Additional Church Information ---\n" + lines

    if staff:
        # No visitor restrictions for staff
        return base + ctx + qna_block + snippet_block

    if not widget:
        # Fallback path (widget=False, staff=False) — unchanged behaviour
        return base + ctx + qna_block + snippet_block

    # Public widget addendum — unchanged
    addendum = (
        "\n\nWhen answering questions about schedules, events, menus, or anything "
        "time-sensitive, use today's date to give a specific, direct answer — "
        "do not list every option when only today's is relevant."
        "\n\nIMPORTANT: Never mention that you are referencing a document, file, "
        "or uploaded file of any kind. Never reveal or repeat file names (including "
        ".pdf and .docx filenames). Answer naturally and directly, as if you simply "
        "know the information."
        "\n\nRespond in plain text only. Do not use markdown formatting such as "
        "headings (##), bullet points (-), bold (**text**), italic (*text*), "
        "or any other markdown syntax. Write in natural, conversational sentences."
    )
    return base + ctx + qna_block + snippet_block + addendum


# ── Auth helpers ─────────────────────────────────────────────────────────────

def is_super_admin() -> bool:
    return current_user.is_authenticated and current_user.email == SUPER_ADMIN_EMAIL


def is_billing_exempt(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    return domain in EXEMPT_DOMAINS


def get_billing_status(church) -> dict:
    """Return a normalised billing-status dict for *church*.

    Returns:
        has_access        – bool: whether the church currently has paid access
        billing_type      – "manual" | "stripe" | "none"
        expires           – datetime.date or None
        days_remaining    – int or None
        stripe_invite_sent – bool
    """
    from datetime import date as _date
    today = _date.today()

    # 1. Active manual payment
    if (getattr(church, "manual_payment_active", False)
            and church.manual_payment_expires
            and church.manual_payment_expires >= today):
        days_remaining = (church.manual_payment_expires - today).days
        return {
            "has_access":          True,
            "billing_type":        "manual",
            "expires":             church.manual_payment_expires,
            "days_remaining":      days_remaining,
            "stripe_invite_sent":  bool(church.stripe_invite_sent_at),
        }

    # 2. Stripe subscription
    if church.stripe_subscription_id:
        return {
            "has_access":          True,
            "billing_type":        "stripe",
            "expires":             None,
            "days_remaining":      None,
            "stripe_invite_sent":  bool(church.stripe_invite_sent_at),
        }

    # 3. No active billing (trial or fully expired)
    return {
        "has_access":          church.is_active,  # True if trial still running
        "billing_type":        "none",
        "expires":             None,
        "days_remaining":      None,
        "stripe_invite_sent":  bool(getattr(church, "stripe_invite_sent_at", None)),
    }


def require_active():
    """Return a redirect to /subscribe if the current church's billing has lapsed.
    Returns None if the user may continue.
    """
    if is_billing_exempt(current_user.email):
        return None
    if current_user.church.billing_exempt:
        return None
    if not current_user.church.is_active:
        return redirect(url_for("stripe.subscribe_page"))
    return None


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


# ── Gemini ───────────────────────────────────────────────────────────────────

def friendly_gemini_error(exc: Exception) -> tuple[str, int]:
    msg = str(exc).lower()
    if "429" in msg or "quota" in msg or "rate" in msg or "exhausted" in msg:
        return ("The AI service is temporarily over its request limit. Please wait and try again.", 429)
    if "401" in msg or "403" in msg or "api_key" in msg:
        return ("API key error — please check that GEMINI_API_KEY is configured correctly.", 401)
    if "404" in msg or "not found" in msg:
        return ("The AI model could not be found. Please check the model name.", 404)
    if "503" in msg or "unavailable" in msg:
        return ("The AI service is temporarily unavailable. Please try again.", 503)
    return (f"AI error: {exc}", 502)


def call_gemini(question: str, context: str, history: list[dict], system_instruction: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")

    client = genai.Client(api_key=api_key)

    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    current_text = (
        "[Relevant church information:]\n"
        f"{context}\n---\n"
        "Use only sources that directly support your answer. Cite each factual claim "
        "drawn from a numbered source with its bracketed number, such as [1]. Do not "
        "cite a source unless it supports that claim. Use the smallest number of sources "
        "needed, preferring a page specifically about the question over home pages, blog "
        "posts, or pages where the fact appears only incidentally. If the sources do not support an "
        f"answer, say that the information is unavailable and do not add a citation.\n\n{question}"
        if context.strip()
        else question
    )
    contents.append(types.Content(role="user", parts=[types.Part(text=current_text)]))

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    models = [GEMINI_MODEL]
    if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
        models.append(GEMINI_FALLBACK_MODEL)

    last_exc: Exception = Exception("Unknown error")
    for model_idx, model in enumerate(models):
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return response.text
            except Exception as e:
                last_exc = e
                err = str(e).lower()
                if ("429" in err or "quota" in err or "rate" in err or "exhausted" in err) and attempt < 2:
                    time.sleep(2 ** attempt + 1)  # 2s, then 3s
                    continue
                break  # non-retryable, or retries exhausted: consider fallback model
        # Fall back only when the model itself is broken or overloaded
        # (retired/renamed → 404, outage → 500/503), not for auth or bad requests.
        err = str(last_exc).lower()
        retryable = ("404" in err or "not found" in err or "503" in err
                     or "unavailable" in err or "500" in err or "internal" in err)
        if retryable and model_idx < len(models) - 1:
            log.warning("[GEMINI] model %s failed (%s); falling back to %s",
                        model, last_exc, models[model_idx + 1])
            continue
        raise last_exc
    raise last_exc
