"""Cross-process locking for scheduled jobs.

APScheduler runs inside the web process, so every gunicorn worker starts its own
copy of the schedule. With one worker that was harmless. With several it would
mean each church receiving the weekly digest once per worker, each website
crawled several times over, and each billing-expiry warning sent repeatedly —
which is why the worker count could not simply be raised.

A Postgres advisory lock makes each job single-flight across every process: all
of them wake, one wins the lock and runs, the rest return immediately. The lock
lives on the connection, so it is released when the job finishes and also if the
process dies mid-job, which means a crash cannot wedge a job permanently.

On SQLite there is by definition a single process, so the lock is a no-op.
"""

import logging
import zlib
from contextlib import contextmanager
from functools import wraps

from sqlalchemy import text

log = logging.getLogger("wesley")

# Namespace for this application's advisory locks, so a key collision with
# anything else sharing the database is not possible.
_LOCK_NAMESPACE = 0x57455348  # "WESH"


def _job_key(name: str) -> int:
    return zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF


@contextmanager
def job_lock(name: str):
    """Yield True if this process may run *name*, False if another already is.

    Must be called inside an app context.
    """
    from models import db

    engine = db.engine
    if engine.dialect.name != "postgresql":
        # Single-process deployment; there is no one to race against.
        yield True
        return

    key = _job_key(name)
    connection = engine.connect()
    acquired = False
    try:
        acquired = bool(connection.execute(
            text("SELECT pg_try_advisory_lock(:ns, :key)"),
            {"ns": _LOCK_NAMESPACE, "key": key},
        ).scalar())
        yield acquired
    finally:
        if acquired:
            try:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"),
                    {"ns": _LOCK_NAMESPACE, "key": key},
                )
            except Exception:
                # Losing the connection already released the lock.
                log.warning("[SCHED] could not explicitly unlock %s", name)
        connection.close()


def single_flight(app, name: str):
    """Wrap a scheduled job so only one process across the fleet runs it.

    The wrapper owns the app context, because acquiring the lock needs the
    engine before the job body runs.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            with app.app_context():
                with job_lock(name) as acquired:
                    if not acquired:
                        log.debug("[SCHED] %s already running elsewhere; skipping.", name)
                        return None
                    return fn(*args, **kwargs)
        return wrapper
    return decorator
