"""Shared constants and configuration — imported by app.py and route modules."""

import os

# ── Platform constants (override via environment variables) ───────────────────

APP_URL       = os.getenv("APP_URL",       "https://app.wesleyai.co")
FROM_EMAIL    = os.getenv("FROM_EMAIL",    "Wesley AI <noreply@wesleyai.co>")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "info@wesleyai.co")
GEMINI_MODEL  = os.getenv("GEMINI_MODEL",  "gemini-2.5-flash")

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

# ── Super admin ──────────────────────────────────────────────────────────────

SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "info@wesleyai.co")

# ── Default system prompt ────────────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = (
    "You are Wesley, a helpful AI assistant for United Methodist churches. "
    "You are grounded in Wesleyan theology and United Methodist doctrine. "
    "You speak with warmth, grace, and pastoral care. "
    "You never contradict UMC doctrine. "
    "For deep theological or personal questions you always encourage the user "
    "to speak with their pastor."
)

# ── File uploads ─────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MAX_UPLOAD_MB = 32

# ── Billing exempt domains ───────────────────────────────────────────────────

_extra_exempt  = {d.strip() for d in os.getenv("BILLING_EXEMPT_DOMAINS", "daltonfumc.com").split(",") if d.strip()}
EXEMPT_DOMAINS = {"wesleyai.co"} | _extra_exempt
