"""Abuse protection for the public chatbot endpoints.

Three layers, none of which depend on a prompt:

* **Per-visitor limits**, counted in the database so they hold across gunicorn
  workers and survive restarts (the old in-memory limiter gave each worker its
  own budget and forgot everything on deploy).
* **A daily circuit breaker** on total public AI calls, so an attack on the
  public endpoint costs a bounded amount and the church gets a log line, not a bill.
* **An origin check**, so other websites cannot embed the chatbot and spend the
  church's AI budget from their visitors' browsers.
"""

import logging
import os
import time
from urllib.parse import urlparse

from flask import current_app, request
from config import ORG_DOMAIN
from models import RateLimitHit, db

log = logging.getLogger("wesley")

# (name, limit, window seconds), checked in order for each chat request.
CHAT_LIMITS = (("minute", 20, 60), ("day", 300, 86400))
GUEST_LIMITS = (("hour", 5, 3600),)
DAILY_CAP = int(os.getenv("PUBLIC_DAILY_CAP", "3000"))


def _bump(key: str, window_seconds: int) -> int:
    """Count one hit in the current window and return the new total.

    One atomic statement (INSERT ... ON CONFLICT DO UPDATE count = count + 1), so
    two workers hitting the same key at once cannot lose an increment. An
    earlier read-then-write version did exactly that: eight racing threads made
    forty hits and it counted eleven.
    """
    window = int(time.time() // window_seconds) * window_seconds
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert

    stmt = insert(RateLimitHit).values(key=key, window=window, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key", "window"],
        set_={"count": RateLimitHit.count + 1},
    )
    db.session.execute(stmt)
    db.session.commit()
    row = RateLimitHit.query.filter_by(key=key, window=window).first()
    return row.count if row else 1


def allowed(visitor: str, limits=CHAT_LIMITS, scope: str = "chat") -> bool:
    """Whether this visitor may make another request. Counts the attempt.

    Fails open if the counter itself breaks: a database hiccup must not take the
    public chatbot offline, and the daily cap still bounds the damage.
    """
    if current_app.config.get("PUBLIC_LIMITS_DISABLED"):
        return True
    try:
        for name, limit, seconds in limits:
            if _bump(f"{scope}:{name}:{visitor}", seconds) > limit:
                return False
        return True
    except Exception:
        db.session.rollback()
        log.exception("public rate limit counter failed")
        return True


def under_daily_cap() -> bool:
    """False once the day's total public AI calls pass the circuit breaker."""
    if current_app.config.get("PUBLIC_LIMITS_DISABLED"):
        return True
    try:
        total = _bump("cap:chat:day", 86400)
        if total == DAILY_CAP + 1:
            log.error("PUBLIC CHATBOT DAILY CAP (%d) REACHED; answering with a fallback "
                      "until midnight UTC.", DAILY_CAP)
        return total <= DAILY_CAP
    except Exception:
        db.session.rollback()
        log.exception("public daily cap counter failed")
        return True


def prune(older_than_seconds: int = 3 * 86400) -> int:
    """Delete counters for long-finished windows. Returns rows removed."""
    cutoff = int(time.time()) - older_than_seconds
    removed = RateLimitHit.query.filter(RateLimitHit.window < cutoff).delete()
    db.session.commit()
    return removed


# ── Origin check ─────────────────────────────────────────────────────────────

def _allowed_hosts() -> set:
    raw = os.getenv("PUBLIC_ALLOWED_ORIGINS", ORG_DOMAIN)
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    if os.getenv("FLASK_DEBUG", "").lower() in ("1", "true"):
        hosts |= {"localhost", "127.0.0.1"}
    return hosts


def origin_allowed(origin: str) -> bool:
    """A browser Origin header is allowed if its host is, or is under, an allowed host."""
    host = (urlparse(origin).hostname or "").lower()
    return any(host == a or host.endswith("." + a) for a in _allowed_hosts())


def cors_origin():
    """The value for Access-Control-Allow-Origin, or None if the caller is refused.

    A request with no Origin header is not a browser cross-site call (a script,
    curl, a server) and is governed by the rate limits instead.
    """
    origin = request.headers.get("Origin")
    if not origin:
        return "*"
    return origin if origin_allowed(origin) else None
