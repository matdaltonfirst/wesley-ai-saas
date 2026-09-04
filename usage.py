"""Per-tenant AI usage metering.

Every Gemini call is attributed to a church, a surface (staff chat or the
public widget), and a model, then folded into one row per day. That makes three
questions answerable that previously were not: which churches are expensive,
whether a church is being abused, and what a retrieval change did to token
cost.

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


def record_usage(church_id: int, surface: str, usage: Optional[dict]) -> None:
    """Fold one Gemini call into the church's daily bucket.

    *usage* is the dict populated by call_gemini. An empty or missing dict
    still records the call — knowing a request happened matters even when the
    token counts did not come back.
    """
    if not church_id:
        return
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
                church_id=church_id, day=today, surface=surface, model=model,
            ).first()
            if row is None:
                row = UsageDaily(
                    church_id=church_id, day=today, surface=surface, model=model,
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
        log.exception("Usage metering failed for church_id=%s", church_id)


def usage_totals(church_ids=None, days: int = 30) -> dict:
    """Totals per church over the trailing *days*, keyed by church id.

    Returns {church_id: {"calls", "total_tokens", "staff_calls", "widget_calls"}}
    for churches with any usage in the window.
    """
    since = date.today() - timedelta(days=days - 1)
    query = (
        db.session.query(
            UsageDaily.church_id,
            UsageDaily.surface,
            func.sum(UsageDaily.calls),
            func.sum(UsageDaily.total_tokens),
        )
        .filter(UsageDaily.day >= since)
        .group_by(UsageDaily.church_id, UsageDaily.surface)
    )
    if church_ids is not None:
        ids = list(church_ids)
        if not ids:
            return {}
        query = query.filter(UsageDaily.church_id.in_(ids))

    totals: dict = {}
    for church_id, surface, calls, tokens in query:
        bucket = totals.setdefault(church_id, {
            "calls": 0, "total_tokens": 0, "staff_calls": 0, "widget_calls": 0,
        })
        bucket["calls"] += calls or 0
        bucket["total_tokens"] += tokens or 0
        if surface == WIDGET:
            bucket["widget_calls"] += calls or 0
        else:
            bucket["staff_calls"] += calls or 0
    return totals
