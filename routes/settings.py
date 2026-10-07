"""Settings routes: branding, website URL, crawl, theology, staff management."""

import re
import json
import threading
import logging

from flask import Blueprint, request, jsonify, current_app
from permissions import can, require

from models import db, CrawledPage
from config import DEFAULT_COLOR
from denominations import (
    PROFILE, LocalPracticeError, load_local_practices,
    local_practice_schema, validate_local_practices, validate_statement_of_faith,
)
from organization import get_org
from helpers import build_branding_dict, iso_utc, is_safe_url

settings_bp = Blueprint("settings", __name__)
log = logging.getLogger("wesley")

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ── Church branding API ──────────────────────────────────────────────────────

@settings_bp.route("/api/church/branding", methods=["GET"])
@require("instructions.read")
def get_church_branding():
    return jsonify(build_branding_dict())


@settings_bp.route("/api/church/branding", methods=["POST"])
@require("instructions.write")
def save_church_branding():
    data = request.get_json(silent=True) or {}
    church = get_org()

    bot_name = (data.get("bot_name") or "").strip()
    bot_subtitle = (data.get("bot_subtitle") or "").strip()
    welcome_message = (data.get("welcome_message") or "").strip()
    primary_color = (data.get("primary_color") or "").strip()
    church_city = (data.get("church_city") or "").strip()
    raw_sugs = data.get("starter_questions") or []

    if not bot_name:
        return jsonify({"error": "Bot name cannot be empty."}), 400
    if not welcome_message:
        return jsonify({"error": "Welcome message cannot be empty."}), 400
    if primary_color and not _HEX_COLOR_RE.match(primary_color):
        return jsonify({"error": "Primary color must be a valid hex color (e.g. #1a2b3c)."}), 400

    clean_sugs = [str(s).strip()[:200] for s in raw_sugs if str(s).strip()][:4]

    church.bot_name = bot_name[:100]
    church.bot_subtitle = bot_subtitle[:200] if bot_subtitle else None
    church.welcome_message = welcome_message[:500]
    church.primary_color = primary_color if primary_color else DEFAULT_COLOR
    church.church_city = church_city[:200] if church_city else None
    church.starter_questions = json.dumps(clean_sugs) if clean_sugs else None
    db.session.commit()
    return jsonify({"ok": True})


# ── Church settings API (website URL + crawl stats) ──────────────────────────

@settings_bp.route("/api/church/settings", methods=["GET"])
@require("instructions.read")
def get_church_settings():
    church = get_org()
    page_count = CrawledPage.query.count()
    return jsonify({
        "website_url": church.website_url or "",
        "last_crawled_at": iso_utc(church.last_crawled_at),
        "page_count": page_count,
        # The id the website embed passes as data-church-id.
        "church_id": church.legacy_widget_id or church.id,
    })


@settings_bp.route("/api/church/settings", methods=["POST"])
@require("sources.write")
def save_church_settings():
    data = request.get_json(silent=True) or {}
    url = (data.get("website_url") or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    if len(url) > 500:
        return jsonify({"error": "URL must be 500 characters or fewer."}), 400
    if url and not is_safe_url(url):
        return jsonify({"error": "URL must not point to a private or internal network address."}), 400
    get_org().website_url = url or None
    db.session.commit()
    return jsonify({"ok": True})


# ── Theology API ─────────────────────────────────────────────────────────────
#
# The denomination is fixed (Wesleyan United Methodist), so there is nothing to
# select. Reading and changing are separate permissions (see permissions.py).

def _theology_payload(church) -> dict:
    return {
        "profile": PROFILE.to_dict(),
        "local_practices": load_local_practices(church),
        "local_practice_schema": local_practice_schema(),
        "statement_of_faith": church.statement_of_faith or "",
        "can_manage": can("instructions.write"),
    }


@settings_bp.route("/api/church/theology", methods=["GET"])
@require("instructions.read")
def get_church_theology():
    return jsonify(_theology_payload(get_org()))


@settings_bp.route("/api/church/theology/local-practices", methods=["POST"])
@require("instructions.write")
def save_church_local_practices():
    """Save validated structured local practices and statement of faith (admin only)."""
    data = request.get_json(silent=True) or {}
    church = get_org()
    try:
        if "local_practices" in data:
            cleaned = validate_local_practices(data.get("local_practices"))
            church.local_practices = json.dumps(cleaned) if cleaned else None
        if "statement_of_faith" in data:
            statement = validate_statement_of_faith(data.get("statement_of_faith"))
            church.statement_of_faith = statement or None
    except LocalPracticeError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, **_theology_payload(church)})


# ── Manual re-crawl ──────────────────────────────────────────────────────────

@settings_bp.route("/api/church/crawl", methods=["POST"])
@require("sources.write")
def trigger_crawl():
    import logging
    log = logging.getLogger("wesley")

    church = get_org()
    if not church.website_url:
        return jsonify({"error": "No website URL configured. Save a URL first."}), 400

    crawl_url  = church.website_url
    app = current_app._get_current_object()

    def run_crawl():
        try:
            with app.app_context():
                from crawler import crawl_church_website
                result = crawl_church_website(crawl_url)
                log.info("Manual crawl: %s", result)
        except Exception:
            log.exception("Background crawl failed")

    t = threading.Thread(target=run_crawl, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Crawl started in the background."})
