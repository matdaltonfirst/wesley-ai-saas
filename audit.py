"""The audit log: who did or accessed what, when, and from which source.

Append-only. Nothing in the app edits or deletes these rows, and the nightly
cleanup jobs do not touch them. Entries hold identifiers and source names, never
message text, so the log itself is not a second copy of what people asked.

Logging must never break the action being logged, so failures are swallowed and
reported to the application log.
"""

import json
import logging

from flask import has_request_context, request
from flask_login import current_user

from models import AuditLog, db

log = logging.getLogger("wesley")


def log_event(action: str, source: str = None, detail: dict = None, user=None) -> None:
    """Record one event. *user* defaults to the signed-in person, if any."""
    try:
        who = user
        if who is None and has_request_context() and current_user and current_user.is_authenticated:
            who = current_user
        ip = None
        if has_request_context():
            ip = request.remote_addr
        db.session.add(AuditLog(
            user_id=getattr(who, "id", None),
            email=getattr(who, "email", None),
            action=action[:60],
            source=(source or "")[:200] or None,
            detail=json.dumps(detail, default=str)[:4000] if detail else None,
            ip=ip,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Audit log write failed for %s", action)


def log_ai_access(surface: str, sources: list, question_chars: int = 0) -> None:
    """Record which sources the staff AI was given for one question.

    *sources* is the list of citation dicts built for the turn. Only titles and
    types are stored, never the question or the retrieved text.
    """
    log_event(
        "ai.chat",
        source=", ".join(sorted({s.get("type", "?") for s in sources})) or "none",
        detail={
            "surface": surface,
            "sources": [{"type": s.get("type"), "title": (s.get("title") or "")[:120]}
                        for s in sources][:20],
            "question_chars": question_chars,
        },
    )
