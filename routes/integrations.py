"""The Integrations status page, connecting tools, manual sync, and webhook intake."""

import logging
import secrets
import threading

from flask import (
    Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for,
)
from flask_login import current_user

import connectors
import text_in_church_metrics
from audit import log_event
from connectors import runner
from connectors.base import OAuthConnector, StaticTokenConnector
from connectors.errors import ConnectorError
from helpers import iso_utc
from models import IntegrationToken, db
from organization import get_org
from permissions import can, require

log = logging.getLogger("wesley")

integrations_bp = Blueprint("integrations", __name__)


def _connector_or_404(key):
    try:
        return connectors.get(key), None
    except KeyError:
        return None, (jsonify({"error": "Unknown integration."}), 404)


def _how(c) -> str:
    """How a person connects it: oauth, token, pco, key, or none."""
    if c.key == "planning_center":
        return "pco"
    if c.key == "text_in_church":
        return "key"
    if c.key == "facebook":
        return "token"
    if isinstance(c, OAuthConnector):
        return "oauth"
    return "none"


def describe(c) -> dict:
    h = runner.health(c)
    runs = runner.last_runs(c.key, 5)
    token = c.token_row() if hasattr(c, "token_row") else None
    return {
        "key": c.key, "label": c.label, "description": c.description,
        "status": h.status, "headline": h.headline, "detail": h.detail, "action": h.action,
        "how": _how(c), "last_success_at": iso_utc(h.last_success_at),
        "interval_minutes": c.interval_minutes, "account": (token.account_label if token else "") or "",
        "supports_webhook": c.supports_webhook, "docs": f"/docs/INTEGRATIONS.md#{c.docs_anchor}",
        "settings": runner.config_of(c.key) if c.key in ("facebook", "youtube") else {},
        "runs": [{"id": r.id, "started_at": iso_utc(r.started_at), "finished_at": iso_utc(r.finished_at),
                  "status": r.status, "trigger": r.trigger, "rows_fetched": r.rows_fetched,
                  "rows_changed": r.rows_changed, "error": r.error or ""} for r in runs],
    }


@integrations_bp.route("/integrations")
@require("integrations.read")
def integrations_page():
    return render_template("integrations.html", church_name=get_org().name, user_email=current_user.email,
                           can_manage=can("integrations.manage"))


@integrations_bp.route("/api/integrations")
@require("integrations.read")
def list_integrations():
    return jsonify({"integrations": [describe(c) for c in connectors.all_connectors()],
                    "can_manage": can("integrations.manage")})


# ── Connect, disconnect, turn on and off ─────────────────────────────────────

@integrations_bp.route("/integrations/<key>/connect")
@require("integrations.manage")
def connect(key):
    c, err = _connector_or_404(key)
    if err:
        return err
    if key == "planning_center":
        return redirect(url_for("pco.pco_connect"))
    if not isinstance(c, OAuthConnector) or key == "text_in_church" and not c.configured():
        return jsonify({"error": "This integration is not connected that way."}), 400
    if not c.configured():
        return jsonify({"error": c.not_configured_message()}), 400
    session[f"oauth_state_{key}"] = secrets.token_urlsafe(24)
    return redirect(c.authorize_redirect(session[f"oauth_state_{key}"]))


@integrations_bp.route("/integrations/<key>/callback")
@require("integrations.manage")
def callback(key):
    c, err = _connector_or_404(key)
    if err or not isinstance(c, OAuthConnector):
        return redirect("/integrations?error=unknown")
    expected = session.pop(f"oauth_state_{key}", None)
    if not expected or not secrets.compare_digest(request.args.get("state", ""), expected):
        return redirect("/integrations?error=state")
    if request.args.get("error"):
        log_event("integration.connect_denied", source=key, detail={"error": request.args["error"][:60]})
        return redirect("/integrations?error=denied")
    try:
        c.exchange_code(request.args.get("code", ""), user_id=current_user.id)
    except ConnectorError as exc:
        log_event("integration.connect_failed", source=key, detail={"error": str(exc)[:150]})
        return redirect("/integrations?error=exchange")
    log_event("integration.connect", source=key)
    return redirect("/integrations?connected=" + key)


@integrations_bp.route("/api/integrations/<key>/disconnect", methods=["POST"])
@require("integrations.manage")
def disconnect(key):
    c, err = _connector_or_404(key)
    if err:
        return err
    if key == "planning_center":
        from models import PcoConnection
        PcoConnection.query.delete()
        db.session.commit()
    elif hasattr(c, "disconnect"):
        c.disconnect()
    else:
        return jsonify({"error": "Nothing to disconnect."}), 400
    log_event("integration.disconnect", source=key)
    return jsonify({"ok": True})


@integrations_bp.route("/api/integrations/<key>/enabled", methods=["POST"])
@require("integrations.manage")
def set_enabled(key):
    c, err = _connector_or_404(key)
    if err:
        return err
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be true or false."}), 400
    runner.integration_row(key).enabled = enabled
    db.session.commit()
    log_event("integration.enabled" if enabled else "integration.disabled", source=key)
    return jsonify({"ok": True})


@integrations_bp.route("/api/integrations/facebook/connect", methods=["POST"])
@require("integrations.manage")
def connect_facebook():
    data = request.get_json(silent=True) or {}
    token, page_id = (data.get("token") or "").strip(), (data.get("page_id") or "").strip()
    ig = (data.get("ig_user_id") or "").strip()
    if not token or not page_id.isdigit() or (ig and not ig.isdigit()):
        return jsonify({"error": "Enter the Page access token and the numeric Page id (and, if used, the numeric Instagram account id)."}), 400
    c = connectors.get("facebook")
    c.save_token(token, label=f"Page {page_id}", user_id=current_user.id)
    runner.set_config("facebook", page_id=page_id, ig_user_id=ig)
    runner.integration_row("facebook").last_error = None
    db.session.commit()
    log_event("integration.connect", source="facebook")
    return jsonify({"ok": True})


@integrations_bp.route("/api/integrations/text_in_church/key", methods=["POST"])
@require("integrations.manage")
def connect_text_in_church():
    key = ((request.get_json(silent=True) or {}).get("api_key") or "").strip()
    if len(key) < 10:
        return jsonify({"error": "Paste the API key from Text In Church (Account Settings, Developer API)."}), 400
    connectors.get("text_in_church").save_api_key(key, user_id=current_user.id)
    runner.integration_row("text_in_church").last_error = None
    db.session.commit()
    log_event("integration.connect", source="text_in_church")
    return jsonify({"ok": True})


# ── Sync now ─────────────────────────────────────────────────────────────────

def _run_in_background(c, trigger):
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            try:
                runner.run_sync(c, trigger)
            except Exception:
                log.exception("background sync of %s failed", c.key)
    threading.Thread(target=work, daemon=True).start()


@integrations_bp.route("/api/integrations/<key>/sync", methods=["POST"])
@require("integrations.manage")
def sync_now(key):
    c, err = _connector_or_404(key)
    if err:
        return err
    reason = runner.can_run(c)
    if reason:
        return jsonify({"error": runner.health(c).detail or "This integration cannot sync yet.",
                        "status": runner.health(c).status}), 409
    log_event("integration.sync_requested", source=key)
    if current_app.config.get("TESTING"):
        run = runner.run_sync(c, "manual")
        return jsonify({"ok": True, "status": run.status if run else "skipped"})
    _run_in_background(c, "manual")
    return jsonify({"ok": True, "status": "started"}), 202


# ── Webhooks (public; authenticated by each provider's own scheme) ───────────

@integrations_bp.route("/webhooks/<key>", methods=["POST"])
def webhook_intake(key):
    """A webhook only means "something changed". The payload is never used as data."""
    try:
        c = connectors.get(key)
    except KeyError:
        return jsonify({"error": "Not found."}), 404
    if not c.supports_webhook:
        return jsonify({"error": "Not found."}), 404
    body = request.get_data(cache=True)
    if len(body) > 1_000_000 or not c.verify_webhook(request.headers, body):
        log_event("webhook.rejected", source=key)
        return jsonify({"error": "Not authorized."}), 401
    log_event("webhook.received", source=key)
    if runner.can_run(c) == "":
        if current_app.config.get("TESTING"):
            runner.run_sync(c, "webhook")
        else:
            _run_in_background(c, "webhook")
    return jsonify({"ok": True}), 200


# ── Text In Church numbers (needs its own data permission) ───────────────────

@integrations_bp.route("/api/text-in-church/summary")
@require("data.text_in_church")
def text_in_church_summary():
    try:
        days = min(max(int(request.args.get("days", 28)), 1), 120)
    except ValueError:
        days = 28
    log_event("data.read", source="text_in_church", detail={"view": "summary", "days": days})
    return jsonify(text_in_church_metrics.summary(days))
