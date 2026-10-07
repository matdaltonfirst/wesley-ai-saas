"""Shared constants and configuration — imported by app.py and route modules."""

import os
from pathlib import Path


# ── Database ─────────────────────────────────────────────────────────────────

def database_url(data_dir: Path) -> str:
    """The SQLAlchemy URL: Postgres when DATABASE_URL is set, else local SQLite.

    Railway injects DATABASE_URL when a Postgres service is attached, so
    deploying against Postgres is a matter of attaching the service — there is
    no second source of truth to keep in step.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{data_dir / 'wesley.db'}"
    # Railway and Heroku both still hand out the "postgres://" scheme, which
    # SQLAlchemy 2.x refuses to load a dialect for.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Name the driver we ship. A bare "postgresql://" lets SQLAlchemy choose,
    # and newer releases choose psycopg 3, which is not installed: a rebuild
    # with unpinned dependencies then crashes at boot.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def engine_options(url: str) -> dict:
    """Connection-pool settings appropriate to the backing database."""
    if not is_postgres(url):
        return {}
    return {
        # Railway closes idle connections; without pre-ping the first query
        # after a quiet period fails instead of transparently reconnecting.
        "pool_pre_ping": True,
        "pool_recycle": 900,
        # Kept modest deliberately: gunicorn worker count multiplies this, and
        # a small Postgres plan has a low connection ceiling.
        "pool_size": 5,
        "max_overflow": 5,
    }

# ── Platform constants (override via environment variables) ───────────────────

APP_URL       = os.getenv("APP_URL",       "https://app.wesleyai.co")  # unchanged until a new domain is chosen
FROM_EMAIL    = os.getenv("FROM_EMAIL",    "Wesley AI <noreply@wesleyai.co>")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "info@wesleyai.co")
GEMINI_MODEL  = os.getenv("GEMINI_MODEL",  "gemini-2.5-flash-lite")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

# ── Planning Center OAuth (register the app at api.planningcenteronline.com) ──

PCO_CLIENT_ID     = os.getenv("PCO_CLIENT_ID", "")
PCO_CLIENT_SECRET = os.getenv("PCO_CLIENT_SECRET", "")
PCO_TOKEN_ENCRYPTION_KEY = os.getenv("PCO_TOKEN_ENCRYPTION_KEY", "")
PCO_API_BASE      = "https://api.planningcenteronline.com"

# ── YouTube Data API (sermon ingestion) ───────────────────────────────────────

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# ── Default timezone for church-facing dates (Organization.timezone overrides) ─

DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# ── Branding defaults (single source of truth) ───────────────────────────────

DEFAULT_BOT_NAME = "Wesley"
DEFAULT_WELCOME  = "How can I help you today?"
DEFAULT_COLOR    = "#0a3d3d"
DEFAULT_SUBTITLE = "Ask me anything about our church"
DEFAULT_STARTERS = [
    "What is our volunteer policy?",
    "Help me draft a Sunday bulletin",
    "What events are coming up?",
    "Write a prayer for our newsletter",
]

# ── The organization ─────────────────────────────────────────────────────────
#
# One church. These seed a brand-new database; after that the Organization row
# is the source of truth (see organization.py).

ORG_NAME   = os.getenv("ORG_NAME",   "Dalton First United Methodist Church")
ORG_CITY   = os.getenv("ORG_CITY",   "Dalton, GA")
# Only accounts on this email domain may be staff. Enforced server-side.
ORG_DOMAIN = os.getenv("ORG_DOMAIN", "daltonfumc.com")

# ── Default system prompt ────────────────────────────────────────────────────
#
# The admin-editable instructions for the public website chatbot. Theology does
# not belong here: it comes from the profile in the `denominations` package.

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful AI assistant for a local church. "
    "You speak with warmth, grace, and pastoral care. "
    "You answer questions about this church's beliefs and practices only from "
    "its approved information and its selected denominational profile — never "
    "from your own assumptions about what churches believe. "
    "For deep theological or personal questions you always encourage the user "
    "to speak with their pastor."
)

# ── File uploads ─────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_UPLOAD_MB = 32
