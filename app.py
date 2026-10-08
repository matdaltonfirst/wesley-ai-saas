"""Wesley AI SaaS — Flask application factory and startup."""

import os
import secrets
import logging
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import click
import resend
from flask import Flask, request, jsonify, redirect, url_for
from flask_login import LoginManager
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from models import db, User, SystemPrompt, Conversation, WidgetConversation
from config import (
    DEFAULT_SYSTEM_PROMPT, MAX_UPLOAD_MB, SESSION_ABSOLUTE_HOURS, SESSION_IDLE_HOURS,
    database_url, engine_options, is_postgres,
)
from helpers import csrf_token

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────

log = logging.getLogger("wesley")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── Rate limiter ─────────────────────────────────────────────────────────────

class _RateLimiter:
    """Simple sliding-window rate limiter keyed by IP address."""

    # Keys are only pruned for the caller's own key, so a long window plus many
    # distinct IPs would grow the dict without bound. Sweep expired keys once
    # the dict gets large rather than on every call.
    _SWEEP_THRESHOLD = 10_000

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_limited(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self._SWEEP_THRESHOLD:
                stale = [
                    k for k, ts in self._hits.items()
                    if not ts or now - ts[-1] >= self.window
                ]
                for k in stale:
                    del self._hits[k]
            timestamps = self._hits[key]
            self._hits[key] = [t for t in timestamps if now - t < self.window]
            if len(self._hits[key]) >= self.max_requests:
                return True
            self._hits[key].append(now)
            return False


# ── Paths ────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.getenv("DATA_DIR", "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

# ── External API keys ───────────────────────────────────────────────────────

resend.api_key = os.getenv("RESEND_API_KEY", "")


# ── Application factory ──────────────────────────────────────────────────────

def create_app(testing: bool = False) -> Flask:
    """Create and configure the Flask application.

    Args:
        testing: When True, uses an in-memory SQLite database, bypasses CSRF
                 checks, and skips schema migrations and scheduled jobs.
    """
    _app = Flask(__name__, static_folder="static", template_folder="templates")
    _app.wsgi_app = ProxyFix(_app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    if testing:
        _app.config.update({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "testing-secret-key-not-for-production",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "SIGNUP_ENABLED": True,
            # StaticPool ensures all app contexts share the same in-memory
            # SQLite connection, so data seeded in fixture setup remains
            # visible inside test-client requests (which open their own context).
            "SQLALCHEMY_ENGINE_OPTIONS": {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            },
            "MAX_CONTENT_LENGTH": MAX_UPLOAD_MB * 1024 * 1024,
            "UPLOADS_DIR": UPLOADS_DIR,
            "PUBLIC_LIMITS_DISABLED": True,
            "CHAT_LIMITER": _RateLimiter(max_requests=10000, window_seconds=1),
            "WIDGET_CHAT_LIMITER": _RateLimiter(max_requests=10000, window_seconds=1),
            "WIDGET_BRANDING_LIMITER": _RateLimiter(max_requests=10000, window_seconds=1),
            "GUEST_LIMITER": _RateLimiter(max_requests=10000, window_seconds=1),
            "AUTH_LIMITER": _RateLimiter(max_requests=10000, window_seconds=1),
        })
    else:
        _secret = os.getenv("SECRET_KEY", "")
        if not _secret:
            _secret = secrets.token_hex(32)
            print("WARNING: SECRET_KEY is not set. Generated a random key — sessions will not persist across restarts.")
        _db_url = database_url(DATA_DIR)
        log.info("Database: %s", "PostgreSQL" if is_postgres(_db_url) else "SQLite")
        _app.config.update({
            "SECRET_KEY": _secret,
            # Closed by default: open signup lets anyone create a tenant and
            # spend AI budget. Set SIGNUP_ENABLED=1 to reopen.
            "SIGNUP_ENABLED": os.getenv("SIGNUP_ENABLED", "").lower() in ("1", "true", "yes"),
            "SQLALCHEMY_DATABASE_URI": _db_url,
            "SQLALCHEMY_ENGINE_OPTIONS": engine_options(_db_url),
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "MAX_CONTENT_LENGTH": MAX_UPLOAD_MB * 1024 * 1024,
            "UPLOADS_DIR": UPLOADS_DIR,
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            # Secure unless running the local dev server (which is plain http).
            "SESSION_COOKIE_SECURE": os.getenv(
                "SESSION_COOKIE_SECURE",
                "0" if os.getenv("FLASK_DEBUG", "").lower() in ("1", "true") else "1",
            ).lower() in ("1", "true", "yes"),
            "PERMANENT_SESSION_LIFETIME": timedelta(hours=SESSION_IDLE_HOURS),
            "CHAT_LIMITER": _RateLimiter(max_requests=120, window_seconds=60),
            "WIDGET_CHAT_LIMITER": _RateLimiter(max_requests=30, window_seconds=60),
            "WIDGET_BRANDING_LIMITER": _RateLimiter(max_requests=60, window_seconds=60),
            # Guest submissions write into the church's system of record (and
            # into Planning Center), so they are held to a much tighter budget
            # than chat: a real visitor submits once, not five times an hour.
            "GUEST_LIMITER": _RateLimiter(max_requests=5, window_seconds=3600),
            # Credential stuffing and password-reset email bombing. Generous
            # enough that a shared church-office IP will not trip it.
            "AUTH_LIMITER": _RateLimiter(max_requests=10, window_seconds=900),
        })

    db.init_app(_app)
    # Alembic owns schema changes from here on. The SQLite retrofit block
    # below stays only for existing SQLite databases.
    Migrate(_app, db)

    # Make csrf_token() available in all Jinja2 templates
    _app.jinja_env.globals["csrf_token"] = csrf_token

    _lm = LoginManager(_app)
    _lm.login_view = "auth.login_page"

    @_lm.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    @_lm.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("auth.login_page"))

    # ── Register Blueprints ──────────────────────────────────────────────────

    from routes.auth import auth_bp
    from routes.pages import pages_bp
    from routes.chat import chat_bp
    from routes.documents_routes import documents_bp
    from routes.public_api import public_bp
    from routes.staff_widget import staff_widget_bp
    from routes.team import team_bp
    from routes.streaming_routes import streaming_bp
    from routes.integrations import integrations_bp
    from routes.settings import settings_bp
    from routes.admin import admin_bp
    from routes.comms_routes import comms_bp
    from routes.calendars import calendars_bp
    from routes.pco_routes import pco_bp
    from routes.sermons_routes import sermons_bp
    from routes.packets_routes import packets_bp
    from knowledge_packs import knowledge_bp

    _app.register_blueprint(auth_bp)
    _app.register_blueprint(pages_bp)
    _app.register_blueprint(chat_bp)
    _app.register_blueprint(documents_bp)
    _app.register_blueprint(public_bp)
    _app.register_blueprint(staff_widget_bp)
    _app.register_blueprint(team_bp)
    _app.register_blueprint(streaming_bp)
    _app.register_blueprint(integrations_bp)
    _app.register_blueprint(settings_bp)
    _app.register_blueprint(admin_bp)
    _app.register_blueprint(comms_bp)
    _app.register_blueprint(calendars_bp)
    _app.register_blueprint(pco_bp)
    _app.register_blueprint(sermons_bp)
    _app.register_blueprint(packets_bp)
    _app.register_blueprint(knowledge_bp)

    # ── Session cap and CSRF ─────────────────────────────────────────────────

    # Visitors to the public chatbot have no session, so there is nothing to
    # forge and the check below does not apply to them (it only runs for a signed-in
    # person); they are protected by rate limits instead. The sign-in endpoints
    # run their own check.
    _CSRF_EXEMPT_PREFIXES = ("/api/auth/",)

    @_app.before_request
    def enforce_session_and_csrf():
        from flask import session as flask_session
        from flask_login import current_user, logout_user

        if current_user.is_authenticated:
            # Absolute cap, on top of the rolling idle timeout.
            started = flask_session.get("login_at")
            expired = True
            if started:
                try:
                    expired = datetime.utcnow() - datetime.fromisoformat(started) > timedelta(hours=SESSION_ABSOLUTE_HOURS)
                except ValueError:
                    expired = True
            if expired and not _app.config.get("TESTING"):
                logout_user()
                flask_session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Your session has expired. Please sign in again."}), 401
                return redirect(url_for("auth.login_page"))

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.path.startswith(_CSRF_EXEMPT_PREFIXES):
                return None
            if _app.config.get("TESTING") and not _app.config.get("FORCE_CSRF"):
                return None
            if not current_user.is_authenticated:
                return None          # the route's own auth answers (401)
            sent = request.headers.get("X-CSRFToken") or request.form.get("csrf_token", "")
            expected = flask_session.get("csrf_token", "")
            if not sent or not expected or not secrets.compare_digest(sent, expected):
                return jsonify({"error": "CSRF validation failed. Reload the page and try again."}), 403
        return None

    # ── Security headers ─────────────────────────────────────────────────────

    @_app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    # ── Flask CLI commands ───────────────────────────────────────────────────

    @_app.cli.command("init-db")
    def init_db_command():
        """Explicitly create all database tables. Safe to run on an existing DB."""
        db.create_all()
        click.echo("init-db: all tables created (or already exist).")
        from sqlalchemy import inspect as sa_inspect2
        tables = sa_inspect2(db.engine).get_table_names()
        click.echo(f"init-db: tables in DB → {', '.join(sorted(tables))}")

    # ── Database init + migrations ───────────────────────────────────────────

    with _app.app_context():
        _url = _app.config["SQLALCHEMY_DATABASE_URI"]
        _skip_create = os.getenv("WESLEY_SKIP_CREATE_ALL", "").lower() in ("1", "true", "yes")

        if is_postgres(_url):
            # Alembic owns the Postgres schema. Creating tables here would build
            # them behind Alembic's back, leaving its version table absent and
            # every future `flask db upgrade` trying to create what already
            # exists. The deploy runs `flask db upgrade` instead.
            log.info("Postgres detected — schema is managed by Alembic.")
        elif _skip_create:
            # Set when generating an Alembic revision, which has to compare the
            # models against an empty database.
            log.info("WESLEY_SKIP_CREATE_ALL set — not creating tables.")
        else:
            db.create_all()
            log.info("db.create_all() completed — all tables present.")

        # Seed the master system prompt on first run. On Postgres the tables
        # exist only once `flask db upgrade` has run, which happens in the
        # deploy command before this process starts — but a first boot in the
        # wrong order must degrade rather than crash the app.
        if not _skip_create:
            try:
                if not SystemPrompt.query.get(1):
                    db.session.add(SystemPrompt(id=1, content=DEFAULT_SYSTEM_PROMPT))
                    db.session.commit()
                    log.info("System prompt seeded with default.")
            except Exception:
                db.session.rollback()
                log.warning("System prompt seed deferred — schema not ready yet.")

    return _app


# ── Production setup ─────────────────────────────────────────────────────────

app = create_app()

# ── API key validation ───────────────────────────────────────────────────────

_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    log.warning("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
else:
    log.info("Gemini API key is set.")

if not os.getenv("RESEND_API_KEY"):
    log.warning("RESEND_API_KEY is not set. Password reset emails will not be sent.")

# ── Nightly scheduled jobs ───────────────────────────────────────────────────


def nightly_crawl_job():
    """Re-crawl the church website. Runs at 2am daily."""
    with app.app_context():
        from crawler import crawl_church_website
        from organization import get_org
        org = get_org()
        if not org.website_url:
            log.info("Nightly crawl: no website URL configured.")
            return
        try:
            log.info("Nightly crawl: %s", crawl_church_website(org.website_url))
        except Exception as exc:
            log.error("Nightly crawl error: %s", exc)


def embedding_warm_job():
    """Embed all retrievable content. Runs at 2:45am, after the crawl.

    Retrieval stays on keyword scoring until the corpus is fully embedded, so
    this job is what actually switches semantic search on, and doing it here
    means no visitor ever waits on a cold corpus.
    """
    with app.app_context():
        from embeddings import chunk_hashes, is_enabled, prune_cache, warm_chunks
        if not is_enabled():
            log.info("Embedding warm: disabled, skipping.")
            return

        from documents import (
            load_church_documents, load_church_web_content, load_curated_content,
        )
        from calendar_feed import load_calendar_chunks
        from sermons import load_sermon_chunks
        from denominations import load_denomination_chunks

        try:
            chunks = (
                load_church_documents(UPLOADS_DIR)
                + load_church_web_content()
                + load_curated_content()
                + load_calendar_chunks()
                + load_sermon_chunks()
                + load_denomination_chunks()
            )
            embedded = warm_chunks(chunks)
            log.info("Embedding warm: %d new vector(s).", embedded)
        except Exception:
            # Without this run's hashes the live vectors would look orphaned, so
            # skip the prune rather than delete work that is still in use.
            log.exception("Embedding warm failed; prune skipped.")
            return
        pruned = prune_cache(chunk_hashes(chunks))
        if pruned:
            log.info("Embedding prune: removed %d unreachable vector(s).", pruned)


def nightly_cleanup_job():
    """Delete conversations (and their messages) last updated more than 14 days ago."""
    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(days=14)
        old_convs = Conversation.query.filter(Conversation.updated_at < cutoff).all()
        count = len(old_convs)
        for conv in old_convs:
            db.session.delete(conv)
        db.session.commit()
        log.info("Nightly cleanup: deleted %d staff conversation(s) older than 14 days.", count)


def nightly_widget_cleanup_job():
    """Delete widget conversations (and their messages) older than 30 days."""
    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(days=30)
        old = WidgetConversation.query.filter(WidgetConversation.updated_at < cutoff).all()
        count = len(old)
        for wconv in old:
            db.session.delete(wconv)
        db.session.commit()
        log.info("Nightly widget cleanup: deleted %d widget conversation(s) older than 30 days.", count)
        import public_limits
        log.info("Rate-limit counters pruned: %d.", public_limits.prune())
        from connectors import runner as _runner
        log.info("Old sync runs pruned: %d.", _runner.prune_runs())


def calendar_refresh_job():
    """Nightly 1:30 AM job: re-fetch every connected calendar feed."""
    with app.app_context():
        from calendar_feed import refresh_all_calendars
        ok = refresh_all_calendars()
        log.info("Calendar refresh job: %d feed(s) refreshed successfully.", ok)


def sermon_check_job():
    """Daily 4:30 AM job: ingest new sermons from connected YouTube channels."""
    with app.app_context():
        from sermons import check_all_sources
        count = check_all_sources()
        log.info("Sermon check job: ingested %d new sermon(s).", count)


def transcript_backfill_job():
    """Fill in transcripts for sermons ingested while captions were broken.

    Bounded and idempotent, so it drains the backlog over a few nights and then
    costs nothing. Runs before the embedding warm so a newly filled transcript
    is embedded the same night.
    """
    with app.app_context():
        from sermons import backfill_transcripts
        result = backfill_transcripts()
        if result["filled"] or result["failed"]:
            log.info("Transcript backfill: %d filled, %d without captions.",
                     result["filled"], result["failed"])


def monday_packet_job():
    """Turn Sunday's sermon into a week of content and email it to admins.

    Runs after the transcript backfill and the embedding warm, so a sermon
    ingested overnight is fully prepared before its packet is built.
    """
    with app.app_context():
        from packets import run_monday_packets
        run_monday_packets()


def weekly_digest_job():
    """Monday 13:00 UTC (early morning US) job: email each church a summary of
    last week's widget activity."""
    with app.app_context():
        from digest import send_weekly_digests
        sent = send_weekly_digests()
        log.info("Weekly digest job: sent digest(s) for %d church(es).", sent)


def pco_reconciliation_job():
    """Recover interrupted and retryable Planning Center guest syncs."""
    with app.app_context():
        from pco import reconcile_pending_syncs
        synced = reconcile_pending_syncs()
        if synced:
            log.info("Planning Center reconciliation: synced %d guest(s).", synced)


# Only start the scheduler in production (not during tests or CLI commands)
# Every gunicorn worker starts its own scheduler, so each job is wrapped in a
# cross-process lock: all workers wake, exactly one runs it. Without this,
# raising the worker count would send each church duplicate digests, crawl each
# website repeatedly, and repeat every billing warning.
def connector_sync_job(key):
    """Build the job body for one connector. Errors are recorded by the runner."""
    def job():
        with app.app_context():
            import connectors
            from connectors import runner
            runner.run_sync(connectors.get(key), "scheduled")
    job.__name__ = f"sync_{key}_job"
    return job


_SCHEDULED_JOBS = [
    ("nightly_crawl",         nightly_crawl_job,         CronTrigger(hour=2, minute=0)),
    ("transcript_backfill",   transcript_backfill_job,   CronTrigger(hour=2, minute=30)),
    ("embedding_warm",        embedding_warm_job,        CronTrigger(hour=2, minute=45)),
    ("nightly_cleanup",       nightly_cleanup_job,       CronTrigger(hour=3, minute=0)),
    ("nightly_widget_cleanup", nightly_widget_cleanup_job, CronTrigger(hour=3, minute=30)),
    ("monday_packet",         monday_packet_job,         CronTrigger(day_of_week="mon", hour=11, minute=0)),
    ("weekly_digest",         weekly_digest_job,         CronTrigger(day_of_week="mon", hour=13, minute=0)),
    ("calendar_refresh",      calendar_refresh_job,      CronTrigger(hour=1, minute=30)),
    ("sermon_check",          sermon_check_job,          CronTrigger(hour=4, minute=30)),
    ("pco_reconciliation",    pco_reconciliation_job,    "interval"),
]

# WESLEY_DISABLE_SCHEDULER=1 for one-off tooling (migrations, rehearsals, scripts):
# importing this module would otherwise start the jobs, including ones that call
# Planning Center and send email, inside a process that is only meant to inspect.
_scheduler_disabled = os.getenv("WESLEY_DISABLE_SCHEDULER", "").lower() in ("1", "true", "yes")

if not app.testing and not _scheduler_disabled:
    from scheduling import single_flight

    scheduler = BackgroundScheduler(daemon=True)
    for _name, _fn, _trigger in _SCHEDULED_JOBS:
        _locked = single_flight(app, _name)(_fn)
        if _trigger == "interval":
            scheduler.add_job(_locked, "interval", minutes=5, id=_name)
        else:
            scheduler.add_job(_locked, _trigger, id=_name)
    # One interval job per connector that syncs on a schedule.
    try:
        import connectors as _connectors
        for _c in _connectors.all_connectors():
            if _c.interval_minutes:
                _locked = single_flight(app, "sched_sync_" + _c.key)(connector_sync_job(_c.key))
                scheduler.add_job(_locked, "interval", minutes=_c.interval_minutes, id="sync_" + _c.key,
                                  jitter=60, max_instances=1, coalesce=True)
    except Exception:
        log.exception("Could not schedule connector syncs")
    if not scheduler.running:
        scheduler.start()


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true"))
