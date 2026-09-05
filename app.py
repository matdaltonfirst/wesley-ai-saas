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
import stripe
from flask import Flask, request, jsonify, redirect, url_for
from flask_login import LoginManager
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from sqlalchemy import text, inspect as sa_inspect
from sqlalchemy.pool import StaticPool
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from models import db, User, Church, SystemPrompt, Conversation, WidgetConversation, Invite
from config import (
    DEFAULT_SYSTEM_PROMPT, MAX_UPLOAD_MB, database_url, engine_options,
    is_postgres,
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

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
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
            # StaticPool ensures all app contexts share the same in-memory
            # SQLite connection, so data seeded in fixture setup remains
            # visible inside test-client requests (which open their own context).
            "SQLALCHEMY_ENGINE_OPTIONS": {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            },
            "MAX_CONTENT_LENGTH": MAX_UPLOAD_MB * 1024 * 1024,
            "UPLOADS_DIR": UPLOADS_DIR,
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
            "SQLALCHEMY_DATABASE_URI": _db_url,
            "SQLALCHEMY_ENGINE_OPTIONS": engine_options(_db_url),
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "MAX_CONTENT_LENGTH": MAX_UPLOAD_MB * 1024 * 1024,
            "UPLOADS_DIR": UPLOADS_DIR,
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SAMESITE": "Lax",
            "SESSION_COOKIE_SECURE": os.getenv("SESSION_COOKIE_SECURE", "1" if os.getenv("FLASK_ENV") == "production" else "0").lower() in ("1", "true", "yes"),
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
    from routes.widget import widget_bp
    from routes.settings import settings_bp
    from routes.admin import admin_bp
    from routes.stripe_routes import stripe_bp
    from routes.comms_routes import comms_bp
    from routes.calendars import calendars_bp
    from routes.pco_routes import pco_bp
    from routes.sermons_routes import sermons_bp
    from knowledge_packs import knowledge_bp

    _app.register_blueprint(auth_bp)
    _app.register_blueprint(pages_bp)
    _app.register_blueprint(chat_bp)
    _app.register_blueprint(documents_bp)
    _app.register_blueprint(widget_bp)
    _app.register_blueprint(settings_bp)
    _app.register_blueprint(admin_bp)
    _app.register_blueprint(stripe_bp)
    _app.register_blueprint(comms_bp)
    _app.register_blueprint(calendars_bp)
    _app.register_blueprint(pco_bp)
    _app.register_blueprint(sermons_bp)
    _app.register_blueprint(knowledge_bp)

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
            _schema_ready = False
        elif _skip_create:
            # Set when generating an Alembic revision, which has to compare the
            # models against an empty database.
            log.info("WESLEY_SKIP_CREATE_ALL set — not creating tables.")
            _schema_ready = False
        else:
            db.create_all()
            log.info("db.create_all() completed — all tables present.")
            _schema_ready = True

        # Hand-written ALTER TABLEs that retrofit columns onto SQLite databases
        # created before those columns existed. SQLite-shaped (BOOLEAN DEFAULT 0
        # is not valid Postgres) and pointless anywhere the tables were not just
        # created by create_all.
        if _schema_ready and not testing:
            _run_migrations()

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


def _run_migrations() -> None:
    """Run all inline schema migrations for existing databases."""

    gc_cols = {c["name"] for c in sa_inspect(db.engine).get_columns("guest_connections")}
    gc_migrations = [
        ("pco_person_id",  "ALTER TABLE guest_connections ADD COLUMN pco_person_id VARCHAR(50)"),
        ("pco_synced_at",  "ALTER TABLE guest_connections ADD COLUMN pco_synced_at DATETIME"),
        ("pco_sync_error", "ALTER TABLE guest_connections ADD COLUMN pco_sync_error VARCHAR(500)"),
        ("pco_sync_status", "ALTER TABLE guest_connections ADD COLUMN pco_sync_status VARCHAR(20)"),
        ("pco_sync_attempts", "ALTER TABLE guest_connections ADD COLUMN pco_sync_attempts INTEGER NOT NULL DEFAULT 0"),
        ("pco_next_retry_at", "ALTER TABLE guest_connections ADD COLUMN pco_next_retry_at DATETIME"),
        ("pco_sync_started_at", "ALTER TABLE guest_connections ADD COLUMN pco_sync_started_at DATETIME"),
        ("pco_email_synced", "ALTER TABLE guest_connections ADD COLUMN pco_email_synced BOOLEAN NOT NULL DEFAULT 0"),
        ("pco_phone_synced", "ALTER TABLE guest_connections ADD COLUMN pco_phone_synced BOOLEAN NOT NULL DEFAULT 0"),
        ("pco_note_synced", "ALTER TABLE guest_connections ADD COLUMN pco_note_synced BOOLEAN NOT NULL DEFAULT 0"),
        ("pco_workflow_synced", "ALTER TABLE guest_connections ADD COLUMN pco_workflow_synced BOOLEAN NOT NULL DEFAULT 0"),
    ]
    for col, ddl in gc_migrations:
        if col not in gc_cols:
            with db.engine.connect() as conn:
                conn.execute(text(ddl))
                conn.commit()
            log.info("Migration: added guest_connections.%s", col)

    for table_name in ("messages", "widget_messages"):
        message_cols = {c["name"] for c in sa_inspect(db.engine).get_columns(table_name)}
        if "sources" not in message_cols:
            with db.engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN sources TEXT"))
                conn.commit()
            log.info("Migration: added %s.sources", table_name)

    # ── churches table ───────────────────────────────────────────────────────
    insp = sa_inspect(db.engine)
    existing_cols = {c["name"] for c in insp.get_columns("churches")}
    with db.engine.connect() as conn:
        if "website_url" not in existing_cols:
            conn.execute(text("ALTER TABLE churches ADD COLUMN website_url VARCHAR(500)"))
            conn.commit()
            log.info("Migration: added churches.website_url")
        if "last_crawled_at" not in existing_cols:
            conn.execute(text("ALTER TABLE churches ADD COLUMN last_crawled_at DATETIME"))
            conn.commit()
            log.info("Migration: added churches.last_crawled_at")

    insp2 = sa_inspect(db.engine)
    existing_cols2 = {c["name"] for c in insp2.get_columns("churches")}
    with db.engine.connect() as conn2:
        migrations = [
            ("bot_name",            "ALTER TABLE churches ADD COLUMN bot_name VARCHAR(100) NOT NULL DEFAULT 'Wesley'"),
            ("welcome_message",     "ALTER TABLE churches ADD COLUMN welcome_message VARCHAR(500) NOT NULL DEFAULT 'How can I help you today?'"),
            ("primary_color",       "ALTER TABLE churches ADD COLUMN primary_color VARCHAR(7) NOT NULL DEFAULT '#0a3d3d'"),
            ("church_city",         "ALTER TABLE churches ADD COLUMN church_city VARCHAR(200)"),
            ("onboarding_complete", "ALTER TABLE churches ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT 1"),
            ("trial_ends_at",       "ALTER TABLE churches ADD COLUMN trial_ends_at DATETIME"),
            ("stripe_subscription_id", "ALTER TABLE churches ADD COLUMN stripe_subscription_id VARCHAR(200)"),
            ("billing_exempt",      "ALTER TABLE churches ADD COLUMN billing_exempt BOOLEAN NOT NULL DEFAULT 0"),
            ("plan",                "ALTER TABLE churches ADD COLUMN plan VARCHAR(20) NOT NULL DEFAULT 'founders'"),
            ("stripe_customer_id",  "ALTER TABLE churches ADD COLUMN stripe_customer_id VARCHAR(200)"),
            ("trial_reminder_sent", "ALTER TABLE churches ADD COLUMN trial_reminder_sent BOOLEAN NOT NULL DEFAULT 0"),
            ("starter_questions",   "ALTER TABLE churches ADD COLUMN starter_questions TEXT"),
            ("bot_subtitle",        "ALTER TABLE churches ADD COLUMN bot_subtitle VARCHAR(200)"),
            ("comms_enabled",       "ALTER TABLE churches ADD COLUMN comms_enabled BOOLEAN NOT NULL DEFAULT 1"),
            ("digest_last_sent_at", "ALTER TABLE churches ADD COLUMN digest_last_sent_at DATETIME"),
            ("timezone",            "ALTER TABLE churches ADD COLUMN timezone VARCHAR(50)"),
        ]
        # Theology & affiliation. Existing churches are all United Methodist,
        # so the column default backfills them to 'umc' — no church silently
        # changes denomination, and no local content is touched.
        denomination_migrations = [
            ("denomination",                 "ALTER TABLE churches ADD COLUMN denomination VARCHAR(40) NOT NULL DEFAULT 'umc'"),
            ("denomination_profile_version", "ALTER TABLE churches ADD COLUMN denomination_profile_version VARCHAR(40)"),
            ("denomination_updated_at",      "ALTER TABLE churches ADD COLUMN denomination_updated_at DATETIME"),
            ("local_practices",              "ALTER TABLE churches ADD COLUMN local_practices TEXT"),
            ("statement_of_faith",           "ALTER TABLE churches ADD COLUMN statement_of_faith TEXT"),
        ]
        manual_billing_migrations = [
            ("manual_payment_active",   "ALTER TABLE churches ADD COLUMN manual_payment_active BOOLEAN NOT NULL DEFAULT 0"),
            ("manual_payment_note",     "ALTER TABLE churches ADD COLUMN manual_payment_note VARCHAR(500)"),
            ("manual_payment_start",    "ALTER TABLE churches ADD COLUMN manual_payment_start DATE"),
            ("manual_payment_expires",  "ALTER TABLE churches ADD COLUMN manual_payment_expires DATE"),
            ("manual_payment_amount",   "ALTER TABLE churches ADD COLUMN manual_payment_amount REAL"),
            ("manual_payment_plan",     "ALTER TABLE churches ADD COLUMN manual_payment_plan VARCHAR(20)"),
            ("manual_payment_set_by",   "ALTER TABLE churches ADD COLUMN manual_payment_set_by VARCHAR(200)"),
            ("stripe_invite_sent_at",   "ALTER TABLE churches ADD COLUMN stripe_invite_sent_at DATETIME"),
            ("stripe_invite_resent_at", "ALTER TABLE churches ADD COLUMN stripe_invite_resent_at DATETIME"),
            ("warning_30_sent",         "ALTER TABLE churches ADD COLUMN warning_30_sent BOOLEAN NOT NULL DEFAULT 0"),
            ("warning_7_sent",          "ALTER TABLE churches ADD COLUMN warning_7_sent BOOLEAN NOT NULL DEFAULT 0"),
            ("expired_sent",            "ALTER TABLE churches ADD COLUMN expired_sent BOOLEAN NOT NULL DEFAULT 0"),
        ]
        migrations = migrations + denomination_migrations + manual_billing_migrations
        for col_name, sql in migrations:
            if col_name not in existing_cols2:
                conn2.execute(text(sql))
                conn2.commit()
                log.info("Migration: added churches.%s", col_name)

    # Backfill trial_ends_at
    with db.engine.connect() as conn3:
        trial_cutoff = datetime.utcnow() + timedelta(days=14)
        result = conn3.execute(
            text("UPDATE churches SET trial_ends_at = :ts WHERE trial_ends_at IS NULL"),
            {"ts": trial_cutoff},
        )
        conn3.commit()
        if result.rowcount:
            log.info("Migration: set trial_ends_at for %d existing church(es)", result.rowcount)

    # Backfill denomination for any row that predates the column default.
    with db.engine.connect() as conn_denom:
        result = conn_denom.execute(text(
            "UPDATE churches SET denomination = 'umc' "
            "WHERE denomination IS NULL OR denomination = ''"
        ))
        conn_denom.commit()
        if result.rowcount:
            log.info("Migration: defaulted denomination to 'umc' for %d church(es)",
                     result.rowcount)

    # ── documents table ──────────────────────────────────────────────────────
    insp_docs = sa_inspect(db.engine)
    existing_doc_cols = {c["name"] for c in insp_docs.get_columns("documents")}
    with db.engine.connect() as conn_d:
        if "visibility" not in existing_doc_cols:
            conn_d.execute(text(
                "ALTER TABLE documents ADD COLUMN visibility VARCHAR(20) NOT NULL DEFAULT 'staff_only'"
            ))
            conn_d.commit()
            log.info("Migration: added documents.visibility (default 'staff_only')")

    # ── users table ──────────────────────────────────────────────────────────
    insp_users = sa_inspect(db.engine)
    existing_user_cols = {c["name"] for c in insp_users.get_columns("users")}
    with db.engine.connect() as conn_u:
        if "reset_token" not in existing_user_cols:
            conn_u.execute(text("ALTER TABLE users ADD COLUMN reset_token VARCHAR(100)"))
            conn_u.commit()
            log.info("Migration: added users.reset_token")
        if "reset_token_expires" not in existing_user_cols:
            conn_u.execute(text("ALTER TABLE users ADD COLUMN reset_token_expires DATETIME"))
            conn_u.commit()
            log.info("Migration: added users.reset_token_expires")
        if "role" not in existing_user_cols:
            conn_u.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin'"))
            conn_u.commit()
            log.info("Migration: added users.role")

    # ── Indexes on church_id foreign keys ────────────────────────────────────
    # CREATE INDEX IF NOT EXISTS is idempotent — safe to run on every startup.
    _church_id_indexes = [
        ("idx_conversations_church_id",       "conversations"),
        ("idx_users_church_id",               "users"),
        ("idx_documents_church_id",           "documents"),
        ("idx_crawled_pages_church_id",       "crawled_pages"),
        ("idx_widget_conversations_church_id","widget_conversations"),
        ("idx_invites_church_id",             "invites"),
    ]
    with db.engine.connect() as conn_idx:
        for idx_name, table in _church_id_indexes:
            conn_idx.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} (church_id)"
            ))
        conn_idx.commit()
    log.info("Migration: ensured church_id indexes on all relevant tables")


# ── Production setup ─────────────────────────────────────────────────────────

app = create_app()

# ── API key validation ───────────────────────────────────────────────────────

_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    log.warning("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
else:
    log.info("Gemini API key loaded (%s…)", _api_key[:8])

if not os.getenv("STRIPE_ANNUAL_PRICE_ID"):
    log.warning("STRIPE_ANNUAL_PRICE_ID is not set. Annual billing will not work.")

if not os.getenv("RESEND_API_KEY"):
    log.warning("RESEND_API_KEY is not set. Password reset emails will not be sent.")

# ── Nightly scheduled jobs ───────────────────────────────────────────────────


def nightly_crawl_job():
    """Re-crawl all churches that have a website URL configured. Runs at 2am daily."""
    with app.app_context():
        from crawler import crawl_church_website
        churches = Church.query.filter(Church.website_url.isnot(None)).all()
        log.info("Nightly crawl: found %d church(es) to crawl.", len(churches))
        for church in churches:
            if not church.website_url:
                continue
            try:
                result = crawl_church_website(church.id, church.website_url)
                log.info("Nightly crawl church_id=%d (%s): %s", church.id, church.name, result)
            except Exception as exc:
                log.error("Nightly crawl error church_id=%d: %s", church.id, exc)


def embedding_warm_job():
    """Embed every church's retrievable content. Runs at 2:45am, after the crawl.

    Retrieval stays on keyword scoring until a church's corpus is fully
    embedded, so this job is what actually switches semantic search on — and
    doing it here means no visitor ever waits on a cold corpus.
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

        total = 0
        live_hashes = set()
        failed = False
        for church in Church.query.all():
            try:
                chunks = (
                    load_church_documents(church.id, UPLOADS_DIR)
                    + load_church_web_content(church.id)
                    + load_curated_content(church.id)
                    + load_calendar_chunks(church.id)
                    + load_sermon_chunks(church.id)
                    + load_denomination_chunks(church.denomination)
                )
                live_hashes |= chunk_hashes(chunks)
                embedded = warm_chunks(chunks)
                total += embedded
                if embedded:
                    log.info("Embedding warm church_id=%d (%s): %d new vector(s)",
                             church.id, church.name, embedded)
            except Exception:
                # A church whose chunks could not be loaded contributes no
                # hashes, so its live vectors would look orphaned. Skip the
                # prune rather than delete work that is still in use.
                failed = True
                log.exception("Embedding warm failed for church_id=%d", church.id)

        log.info("Embedding warm: %d new vector(s) across all churches.", total)
        if failed:
            log.warning("Embedding prune skipped — at least one church failed to load.")
        else:
            pruned = prune_cache(live_hashes)
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


def trial_reminder_job():
    """Daily 9 AM job: email churches whose trial ends in 3–5 days (once only)."""
    with app.app_context():
        from emails import send_trial_expiring_email
        from config import FROM_EMAIL, APP_URL, SUPPORT_EMAIL
        now  = datetime.utcnow()
        low  = now + timedelta(days=3)
        high = now + timedelta(days=5)
        churches = Church.query.filter(
            Church.trial_ends_at >= low,
            Church.trial_ends_at <= high,
            Church.trial_reminder_sent == False,  # noqa: E712
            Church.stripe_subscription_id == None,  # noqa: E711
            Church.billing_exempt == False,  # noqa: E712
        ).all()
        sent = 0
        for church in churches:
            first_user = User.query.filter_by(church_id=church.id).order_by(User.id).first()
            if first_user:
                send_trial_expiring_email(first_user.email, church.name, church.trial_ends_at, FROM_EMAIL, APP_URL, SUPPORT_EMAIL)
            church.trial_reminder_sent = True
            sent += 1
        if churches:
            db.session.commit()
        log.info("Trial reminder job: sent %d reminder(s).", sent)


def invite_cleanup_job():
    """Daily 4 AM job: delete unaccepted invites older than 7 days."""
    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(days=7)
        old = Invite.query.filter(
            Invite.accepted == False,  # noqa: E712
            Invite.created_at < cutoff,
        ).all()
        count = len(old)
        for invite in old:
            db.session.delete(invite)
        db.session.commit()
        log.info("Invite cleanup: deleted %d expired invite(s).", count)


def manual_billing_check_job():
    """Daily 8 AM job: send expiration warnings and deactivate expired manual billing."""
    with app.app_context():
        from datetime import date
        from emails import (
            send_manual_expiring_30_email,
            send_manual_expiring_7_email,
            send_manual_expired_email,
        )
        from config import FROM_EMAIL, SUPPORT_EMAIL

        today = date.today()
        churches = Church.query.filter(
            Church.manual_payment_active == True,  # noqa: E712
            Church.manual_payment_expires.isnot(None),
        ).all()

        for church in churches:
            expires = church.manual_payment_expires
            if expires is None:
                continue

            days_left = (expires - today).days
            first_user = User.query.filter_by(church_id=church.id).order_by(User.id).first()
            admin_email = first_user.email if first_user else None
            expires_str = expires.strftime("%B %d, %Y")

            # Expired today or past
            if days_left < 0:
                church.manual_payment_active = False
                if admin_email and not church.expired_sent:
                    send_manual_expired_email(
                        admin_email, church.name, expires_str, FROM_EMAIL, SUPPORT_EMAIL
                    )
                    church.expired_sent = True
                    log.info("Manual billing expired: church_id=%d — deactivated + emailed", church.id)

            # 7-day warning window (6–8 days so we catch it on the first matching run)
            elif days_left <= 8 and not church.warning_7_sent:
                if admin_email:
                    send_manual_expiring_7_email(
                        admin_email, church.name, expires_str, FROM_EMAIL, SUPPORT_EMAIL
                    )
                church.warning_7_sent = True
                log.info("Manual billing 7-day warning sent: church_id=%d", church.id)

            # 30-day warning window (28–32 days)
            elif days_left <= 32 and not church.warning_30_sent:
                if admin_email:
                    send_manual_expiring_30_email(
                        admin_email, church.name, expires_str, FROM_EMAIL, SUPPORT_EMAIL
                    )
                church.warning_30_sent = True
                log.info("Manual billing 30-day warning sent: church_id=%d", church.id)

        if churches:
            db.session.commit()


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
_SCHEDULED_JOBS = [
    ("nightly_crawl",         nightly_crawl_job,         CronTrigger(hour=2, minute=0)),
    ("transcript_backfill",   transcript_backfill_job,   CronTrigger(hour=2, minute=30)),
    ("embedding_warm",        embedding_warm_job,        CronTrigger(hour=2, minute=45)),
    ("nightly_cleanup",       nightly_cleanup_job,       CronTrigger(hour=3, minute=0)),
    ("nightly_widget_cleanup", nightly_widget_cleanup_job, CronTrigger(hour=3, minute=30)),
    ("invite_cleanup",        invite_cleanup_job,        CronTrigger(hour=4, minute=0)),
    ("trial_reminder",        trial_reminder_job,        CronTrigger(hour=9, minute=0)),
    ("manual_billing_check",  manual_billing_check_job,  CronTrigger(hour=8, minute=0)),
    ("monday_packet",         monday_packet_job,         CronTrigger(day_of_week="mon", hour=11, minute=0)),
    ("weekly_digest",         weekly_digest_job,         CronTrigger(day_of_week="mon", hour=13, minute=0)),
    ("calendar_refresh",      calendar_refresh_job,      CronTrigger(hour=1, minute=30)),
    ("sermon_check",          sermon_check_job,          CronTrigger(hour=4, minute=30)),
    ("pco_reconciliation",    pco_reconciliation_job,    "interval"),
]

if not app.testing:
    from scheduling import single_flight

    scheduler = BackgroundScheduler(daemon=True)
    for _name, _fn, _trigger in _SCHEDULED_JOBS:
        _locked = single_flight(app, _name)(_fn)
        if _trigger == "interval":
            scheduler.add_job(_locked, "interval", minutes=5, id=_name)
        else:
            scheduler.add_job(_locked, _trigger, id=_name)
    if not scheduler.running:
        scheduler.start()


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true"))
