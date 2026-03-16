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
from flask import Flask
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from sqlalchemy import text, inspect as sa_inspect
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from models import db, User, Church, SystemPrompt, Conversation, WidgetConversation, Invite
from config import DEFAULT_SYSTEM_PROMPT, MAX_UPLOAD_MB
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

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_limited(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
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

# ── App setup ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

_secret = os.getenv("SECRET_KEY", "")
if not _secret:
    _secret = secrets.token_hex(32)
    print("WARNING: SECRET_KEY is not set. Generated a random key — sessions will not persist across restarts.")
app.config["SECRET_KEY"] = _secret
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATA_DIR / 'wesley.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Store paths and rate limiters in app config so route modules can access them
app.config["UPLOADS_DIR"] = UPLOADS_DIR
app.config["WIDGET_CHAT_LIMITER"] = _RateLimiter(max_requests=30, window_seconds=60)
app.config["WIDGET_BRANDING_LIMITER"] = _RateLimiter(max_requests=60, window_seconds=60)

db.init_app(app)

# Make csrf_token() available in all Jinja2 templates
app.jinja_env.globals["csrf_token"] = csrf_token

login_manager = LoginManager(app)
login_manager.login_view = "auth.login_page"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    from flask import request, jsonify, redirect, url_for
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required."}), 401
    return redirect(url_for("auth.login_page"))


# ── Register Blueprints ──────────────────────────────────────────────────────

from routes.auth import auth_bp
from routes.pages import pages_bp
from routes.chat import chat_bp
from routes.documents_routes import documents_bp
from routes.widget import widget_bp
from routes.settings import settings_bp
from routes.admin import admin_bp
from routes.stripe_routes import stripe_bp

app.register_blueprint(auth_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(documents_bp)
app.register_blueprint(widget_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(stripe_bp)

# ── Database init + migrations ───────────────────────────────────────────────

with app.app_context():
    db.create_all()
    log.info("db.create_all() completed — all tables present.")

    # Inline migration: add Phase 2 columns to existing churches table if absent
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

    # Inline migration: branding columns added to churches table
    insp2 = sa_inspect(db.engine)
    existing_cols2 = {c["name"] for c in insp2.get_columns("churches")}
    with db.engine.connect() as conn2:
        if "bot_name" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN bot_name VARCHAR(100) NOT NULL DEFAULT 'Wesley'"))
            conn2.commit()
            log.info("Migration: added churches.bot_name")
        if "welcome_message" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN welcome_message VARCHAR(500) NOT NULL DEFAULT 'How can I help you today?'"))
            conn2.commit()
            log.info("Migration: added churches.welcome_message")
        if "primary_color" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN primary_color VARCHAR(7) NOT NULL DEFAULT '#0a3d3d'"))
            conn2.commit()
            log.info("Migration: added churches.primary_color")
        if "church_city" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN church_city VARCHAR(200)"))
            conn2.commit()
            log.info("Migration: added churches.church_city")
        if "onboarding_complete" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT 1"))
            conn2.commit()
            log.info("Migration: added churches.onboarding_complete")
        if "trial_ends_at" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN trial_ends_at DATETIME"))
            conn2.commit()
            log.info("Migration: added churches.trial_ends_at")
        if "stripe_subscription_id" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN stripe_subscription_id VARCHAR(200)"))
            conn2.commit()
            log.info("Migration: added churches.stripe_subscription_id")
        if "billing_exempt" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN billing_exempt BOOLEAN NOT NULL DEFAULT 0"))
            conn2.commit()
            log.info("Migration: added churches.billing_exempt")
        if "plan" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN plan VARCHAR(20) NOT NULL DEFAULT 'founders'"))
            conn2.commit()
            log.info("Migration: added churches.plan")
        if "stripe_customer_id" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN stripe_customer_id VARCHAR(200)"))
            conn2.commit()
            log.info("Migration: added churches.stripe_customer_id")
        if "trial_reminder_sent" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN trial_reminder_sent BOOLEAN NOT NULL DEFAULT 0"))
            conn2.commit()
            log.info("Migration: added churches.trial_reminder_sent")
        if "starter_questions" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN starter_questions TEXT"))
            conn2.commit()
            log.info("Migration: added churches.starter_questions")
        if "bot_subtitle" not in existing_cols2:
            conn2.execute(text("ALTER TABLE churches ADD COLUMN bot_subtitle VARCHAR(200)"))
            conn2.commit()
            log.info("Migration: added churches.bot_subtitle")

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

    # Inline migration: add visibility column to documents table
    insp_docs = sa_inspect(db.engine)
    existing_doc_cols = {c["name"] for c in insp_docs.get_columns("documents")}
    with db.engine.connect() as conn_d:
        if "visibility" not in existing_doc_cols:
            conn_d.execute(text(
                "ALTER TABLE documents ADD COLUMN visibility VARCHAR(20) NOT NULL DEFAULT 'staff_only'"
            ))
            conn_d.commit()
            log.info("Migration: added documents.visibility (default 'staff_only')")

    # Inline migration: add password-reset + role columns to users table
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

    # Seed the master system prompt on first run
    if not SystemPrompt.query.get(1):
        db.session.add(SystemPrompt(id=1, content=DEFAULT_SYSTEM_PROMPT))
        db.session.commit()
        log.info("System prompt seeded with default.")

# ── Flask CLI commands ───────────────────────────────────────────────────────


@app.cli.command("init-db")
def init_db_command():
    """Explicitly create all database tables. Safe to run on an existing DB."""
    db.create_all()
    click.echo("init-db: all tables created (or already exist).")
    from sqlalchemy import inspect as sa_inspect2
    tables = sa_inspect2(db.engine).get_table_names()
    click.echo(f"init-db: tables in DB → {', '.join(sorted(tables))}")


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


scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(nightly_crawl_job, CronTrigger(hour=2, minute=0))
scheduler.add_job(nightly_cleanup_job, CronTrigger(hour=3, minute=0))
scheduler.add_job(nightly_widget_cleanup_job, CronTrigger(hour=3, minute=30))
scheduler.add_job(invite_cleanup_job, CronTrigger(hour=4, minute=0))
scheduler.add_job(trial_reminder_job, CronTrigger(hour=9, minute=0))
if not scheduler.running:
    scheduler.start()


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true"))
