"""Shared helper functions used across multiple route modules."""

import json
import os
import secrets
import logging

from datetime import datetime
from flask import redirect, url_for, session, request, abort
from flask_login import current_user
from google import genai
from google.genai import types

from config import (
    DEFAULT_BOT_NAME, DEFAULT_WELCOME, DEFAULT_COLOR, DEFAULT_SUBTITLE,
    DEFAULT_SYSTEM_PROMPT, SUPER_ADMIN_EMAIL, EXEMPT_DOMAINS, GEMINI_MODEL,
)
from models import SystemPrompt

log = logging.getLogger("wesley")


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

def build_system_prompt(church, widget: bool = False) -> str:
    """Assemble the full Gemini system instruction for a given church."""
    today_str = datetime.utcnow().strftime("%A, %B %-d, %Y")
    prompt_row = SystemPrompt.query.get(1)
    base = f"Today's date is {today_str}.\n\n" + (prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT)

    ctx = f"\n\nYou are installed at {church.name}"
    if church.church_city:
        ctx += f", located in {church.church_city}"
    ctx += f". Your name is {church.bot_name or DEFAULT_BOT_NAME}."

    if not widget:
        return base + ctx

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
    return base + ctx + addendum


# ── Auth helpers ─────────────────────────────────────────────────────────────

def is_super_admin() -> bool:
    return current_user.is_authenticated and current_user.email == SUPER_ADMIN_EMAIL


def is_billing_exempt(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    return domain in EXEMPT_DOMAINS


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
        f"[Relevant church information:]\n{context}\n---\n{question}"
        if context.strip()
        else question
    )
    contents.append(types.Content(role="user", parts=[types.Part(text=current_text)]))

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=system_instruction),
    )
    return response.text
