"""AI usage metering.

Every Gemini call is attributed to a surface (staff chat or the public
widget) and a model, then folded into one row per day. That makes three
questions answerable: what the AI costs, whether the public widget is being
abused, and what a retrieval change did to token cost.

Metering must never break a working answer, so record_usage swallows its own
failures — a lost counter is an acceptable price for a delivered reply.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from models import db, UsageDaily

log = logging.getLogger("wesley")

STAFF = "staff"
WIDGET = "widget"


def record_usage(surface: str, usage: Optional[dict]) -> None:
    """Fold one Gemini call into the day's bucket.

    *usage* is the dict populated by call_gemini. An empty or missing dict
    still records the call — knowing a request happened matters even when the
    token counts did not come back.
    """
    usage = usage or {}
    model = str(usage.get("model") or "unknown")[:60]
    prompt = int(usage.get("prompt_tokens") or 0)
    response = int(usage.get("response_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or (prompt + response)
    today = date.today()

    try:
        # Read-then-write rather than a dialect-specific upsert, so this keeps
        # working through the planned move to Postgres. The unique constraint
        # is the real guard: on a concurrent insert one thread loses, and the
        # retry folds its counts into the row the winner created.
        for attempt in range(2):
            row = UsageDaily.query.filter_by(
                day=today, surface=surface, model=model,
            ).first()
            if row is None:
                row = UsageDaily(
                    day=today, surface=surface, model=model,
                    calls=0, prompt_tokens=0, response_tokens=0, total_tokens=0,
                )
                db.session.add(row)
            row.calls += 1
            row.prompt_tokens += prompt
            row.response_tokens += response
            row.total_tokens += total
            try:
                db.session.commit()
                return
            except IntegrityError:
                db.session.rollback()
                if attempt == 1:
                    raise
    except Exception:
        db.session.rollback()
        log.exception("Usage metering failed")


def usage_totals(days: int = 30) -> dict:
    """Totals over the trailing *days*:
    {"calls", "total_tokens", "staff_calls", "widget_calls"}."""
    since = date.today() - timedelta(days=days - 1)
    rows = (
        db.session.query(
            UsageDaily.surface,
            func.sum(UsageDaily.calls),
            func.sum(UsageDaily.total_tokens),
        )
        .filter(UsageDaily.day >= since)
        .group_by(UsageDaily.surface)
    )
    totals = {"calls": 0, "total_tokens": 0, "staff_calls": 0, "widget_calls": 0}
    for surface, calls, tokens in rows:
        totals["calls"] += calls or 0
        totals["total_tokens"] += tokens or 0
        totals["widget_calls" if surface == WIDGET else "staff_calls"] += calls or 0
    return totals
