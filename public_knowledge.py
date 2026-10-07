"""Everything the public website chatbot may know, and nothing else.

This module is the public path's whole view of the church's data. It has its own
queries and imports no loader that can return staff material. Separation is
enforced twice:

* **Structure.** ``tests/test_public_boundary.py`` parses the public modules and
  fails if they import anything outside an explicit allowlist, so a future edit
  cannot quietly wire a staff source into the public path.
* **Storage.** Staff-only content carries ``audience="staff"`` (Q&A, snippets,
  calendars) or ``visibility="staff_only"`` (documents) and the queries below
  select the public value explicitly. Absent a value they return nothing.

The model is called with no tools, so the context built here is the only data
the public chatbot can ever see.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calendar_feed import CONTEXT_DAYS, MAX_CONTEXT_EVENTS, _format_when
from config import DEFAULT_BOT_NAME, DEFAULT_SYSTEM_PROMPT, DEFAULT_TIMEZONE
from denominations import PROFILE, render_local_practice_block, score_denomination_chunks
from documents import (
    _parse_doc_chunks, build_cited_context, find_relevant_chunks, get_church_dir,
)
from models import (
    CalendarEvent, ChurchCalendar, CrawledPage, Document, QnAPair, SystemPrompt,
    TextSnippet,
)
from organization import get_org
from prompts import PUBLIC_ADDENDUM, PUBLIC_IDENTITY_PREFIX, WESLEY_CORE
from sermons import load_sermon_chunks, score_sermon_chunks
from calendar_feed import score_calendar_chunks

MAX_DOC_CHUNKS = 5
MAX_WEB_CHUNKS = 5


# ── Public sources ───────────────────────────────────────────────────────────

def public_documents(uploads_dir):
    """Documents the church marked for the chatbot, and only those."""
    docs = Document.query.filter_by(visibility="staff_and_chatbot").all()
    folder = get_church_dir(uploads_dir)
    chunks = []
    for doc in docs:
        path = folder / doc.filename
        if path.exists():
            chunks.extend(_parse_doc_chunks(doc, path))
    return chunks


def public_web_pages():
    return [
        {"content": p.content, "source": p.title or p.url, "location": p.url}
        for p in CrawledPage.query.all() if p.content and p.content.strip()
    ]


def public_curated():
    chunks = []
    for pair in QnAPair.query.filter_by(is_active=True, audience="public").all():
        chunks.append({
            "content": f"Question: {pair.question}\nAnswer: {pair.answer}",
            "source": "Approved church answer", "location": pair.question,
            "type": "approved_answer",
        })
    for s in TextSnippet.query.filter_by(is_active=True, audience="public").all():
        chunks.append({
            "content": s.content, "source": s.title,
            "location": "Church information", "type": "church_information",
        })
    return chunks


def public_calendar():
    """Upcoming events from calendars marked public only."""
    now = datetime.utcnow()
    events = (
        CalendarEvent.query
        .join(ChurchCalendar, ChurchCalendar.id == CalendarEvent.calendar_id)
        .filter(
            ChurchCalendar.audience == "public",
            CalendarEvent.starts_at >= now - timedelta(days=1),
            CalendarEvent.starts_at <= now + timedelta(days=CONTEXT_DAYS),
        )
        .order_by(CalendarEvent.starts_at)
        .limit(MAX_CONTEXT_EVENTS)
        .all()
    )
    chunks = []
    for event in events:
        lines = [f"Event: {event.title}", f"When: {_format_when(event)}"]
        if event.location:
            lines.append(f"Where: {event.location}")
        if event.description:
            lines.append(event.description)
        chunks.append({
            "content": "\n".join(lines),
            "source": (event.calendar.label if event.calendar else None) or "Church calendar",
            "location": "", "type": "calendar",
        })
    return chunks


def build_public_context(question: str, uploads_dir):
    """The retrieved context and citation candidates for one public question."""
    web = public_web_pages()
    docs = public_documents(uploads_dir) + public_curated()
    scored_docs = find_relevant_chunks(question, docs, top_n=MAX_DOC_CHUNKS) if docs else []
    scored_web = find_relevant_chunks(question, web, top_n=MAX_WEB_CHUNKS) if web else []
    scored_cal = score_calendar_chunks(question, public_calendar())
    scored_ser = score_sermon_chunks(question, load_sermon_chunks())
    scored_denom = score_denomination_chunks(question)
    return build_cited_context([scored_docs, scored_web, scored_cal, scored_ser, scored_denom])


# ── Public prompt ────────────────────────────────────────────────────────────

def _today() -> str:
    try:
        zone = ZoneInfo(get_org().timezone or DEFAULT_TIMEZONE)
    except Exception:
        zone = ZoneInfo("America/New_York")
    return datetime.now(zone).strftime("%A, %B %-d, %Y")


def build_public_prompt() -> str:
    """The public chatbot's system instruction, from public content only."""
    church = get_org()
    row = SystemPrompt.query.get(1)
    instructions = ((row.content if row else DEFAULT_SYSTEM_PROMPT) or "").strip()
    parts = [PUBLIC_IDENTITY_PREFIX] + ([instructions] if instructions else [])
    base = f"Today's date is {_today()}.\n\n" + "\n\n".join(parts) + WESLEY_CORE
    base += PROFILE.prompt_block()

    ctx = f"\n\nYou are installed at {church.name}"
    if church.church_city:
        ctx += f", located in {church.church_city}"
    ctx += f". Your name is {church.bot_name or DEFAULT_BOT_NAME}."
    ctx += render_local_practice_block(church)

    qna = QnAPair.query.filter_by(is_active=True, audience="public").all()
    qna_block = ""
    if qna:
        lines = "\n".join(f"Q: {p.question}\nA: {p.answer}" for p in qna)
        qna_block = (
            "\n\n--- Approved Q&A — Always Use These Answers Exactly ---\n"
            "If a visitor asks something matching one of these questions, use the "
            "provided answer. Do not paraphrase or modify its wording. You may append "
            "a numbered citation marker when citation instructions request one.\n\n"
            + lines
        )
    snippets = TextSnippet.query.filter_by(is_active=True, audience="public").all()
    snippet_block = ""
    if snippets:
        snippet_block = ("\n\n--- Additional Church Information ---\n"
                         + "\n".join(f"{s.title}: {s.content}" for s in snippets))
    return base + ctx + qna_block + snippet_block + PUBLIC_ADDENDUM
