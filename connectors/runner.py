"""Running a sync, recording it, and judging a connector's health.

Every attempt is one ``SyncRun`` row: when it started and ended, how many rows it
fetched and changed, and a plain-language error if it failed. Nothing about a run
depends on the connector being well behaved: any exception is caught, classified,
and turned into a message staff can act on.
"""

import json
import logging
from datetime import datetime, timedelta

from audit import log_event
from models import Integration, SyncRun, db
from scheduling import job_lock

from .base import Health, SyncContext
from .errors import ConnectorError

log = logging.getLogger("wesley")

UNEXPECTED = "Something unexpected went wrong while syncing. The details are in the server log."


def integration_row(key: str) -> Integration:
    row = Integration.query.filter_by(key=key).first()
    if row is None:
        row = Integration(key=key, enabled=True)
        db.session.add(row)
        db.session.commit()
    return row


def config_of(key: str) -> dict:
    try:
        data = json.loads(integration_row(key).config or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def set_config(key: str, **values) -> dict:
    row = integration_row(key)
    data = config_of(key)
    data.update(values)
    row.config = json.dumps(data)
    db.session.commit()
    return data


def can_run(connector) -> str:
    """"" if a sync may start now, else why not (shown nowhere: health explains it)."""
    if getattr(connector, "manual_only", None) and connector.manual_only():
        return "manual only"
    if not integration_row(connector.key).enabled:
        return "off"
    if not connector.configured():
        return "not configured"
    if not connector.connected():
        return "not connected"
    if connector.waiting_for_access():
        return "waiting for access"
    return ""


def run_sync(connector, trigger: str = "scheduled", client=None):
    """Run one sync. Returns the SyncRun, or None if it was skipped or already running."""
    reason = can_run(connector)
    if reason:
        log.info("sync %s skipped: %s", connector.key, reason)
        return None
    with job_lock("sync:" + connector.key) as acquired:
        if not acquired:
            log.info("sync %s already running elsewhere", connector.key)
            return None
        run = SyncRun(integration=connector.key, trigger=trigger, status="running")
        db.session.add(run)
        db.session.commit()
        ctx = SyncContext(connector, run, client or connector.make_client())
        error, kind = None, None
        try:
            connector.sync(ctx)
            db.session.commit()
        except ConnectorError as exc:
            db.session.rollback()
            error, kind = str(exc), exc.kind
        except Exception:
            db.session.rollback()
            log.exception("sync %s crashed", connector.key)
            error, kind = UNEXPECTED, "other"
        return _finish(connector, run, ctx, error, kind)


def _finish(connector, run, ctx, error, kind):
    run = db.session.get(SyncRun, run.id)
    run.finished_at = datetime.utcnow()
    run.rows_fetched, run.rows_changed = ctx.fetched, ctx.changed
    run.detail = json.dumps({"resources": ctx.counts, "warnings": ctx.warnings})
    if error:
        run.status, run.error, run.error_kind = "failed", error, kind
    elif ctx.warnings:
        run.status = "partial"
    else:
        run.status = "ok"

    row = integration_row(connector.key)
    if error:
        row.last_error, row.last_error_kind = error, kind
    else:
        row.last_success_at, row.last_error, row.last_error_kind = datetime.utcnow(), None, None
    db.session.commit()
    log_event("sync.run", source=connector.key,
              detail={"status": run.status, "fetched": run.rows_fetched, "changed": run.rows_changed,
                      "trigger": run.trigger})
    return run


def last_runs(key: str, limit: int = 5) -> list:
    return SyncRun.query.filter_by(integration=key).order_by(SyncRun.id.desc()).limit(limit).all()


def health(connector) -> Health:
    manual = getattr(connector, "manual_only", None) and connector.manual_only()
    if manual:
        return Health("manual", "Entered by hand or by CSV", manual, "Open streaming numbers")
    row = integration_row(connector.key)
    if not row.enabled:
        return Health("off", "Turned off", "An admin turned this integration off.", "Turn on")
    if not connector.configured():
        return Health("not_set_up", "Not set up on this server",
                      connector.not_configured_message(), "")
    waiting = connector.waiting_for_access()
    if waiting:
        return Health("waiting", "Waiting for access", waiting)
    if not connector.connected():
        return Health("not_set_up", "Not connected", "Connect it to start syncing.", "Connect")

    last_success = row.last_success_at
    if row.last_error:
        if row.last_error_kind == "auth":
            return Health("needs_reconnect", "Needs to be reconnected", row.last_error, "Reconnect",
                          last_success)
        return Health("failing", "The last sync did not work", row.last_error, "Sync now", last_success)
    if last_success is None:
        return Health("waiting", "Connected, waiting for the first sync",
                      "It will sync on its own shortly, or use Sync now.", "Sync now")
    limit = connector.stale_after_minutes or connector.interval_minutes * 3
    if datetime.utcnow() - last_success > timedelta(minutes=limit):
        return Health("stale", "Not synced recently",
                      "The last good sync was a while ago and nothing has failed. The scheduler may have been down.",
                      "Sync now", last_success)
    return Health("healthy", "Working", "", "", last_success)


def prune_runs(days: int = 180) -> int:
    """Delete sync run records older than *days*. Raw payloads are replaced in place, so need no pruning."""
    removed = SyncRun.query.filter(SyncRun.started_at < datetime.utcnow() - timedelta(days=days)).delete()
    db.session.commit()
    return removed
