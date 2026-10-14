"""The Needs attention list for the signed-in person."""

from flask import Blueprint, jsonify, request
from flask_login import current_user

import attention
from audit import log_event
from permissions import require_any

attention_bp = Blueprint("attention", __name__)

# Anyone who lands on the home page may ask what needs their attention; what comes back is limited by their roles.
_ANYONE = ("chat.use", "events.read", "sunday.read", "streaming.read", "integrations.read", "guests.read")


@attention_bp.route("/api/attention")
@require_any(*_ANYONE)
def list_attention():
    return jsonify({"alerts": attention.alerts_for(current_user)})


@attention_bp.route("/api/attention/snooze", methods=["POST"])
@require_any(*_ANYONE)
def snooze_alert():
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or "").strip()
    if not key:
        return jsonify({"error": "Which alert?"}), 400
    attention.snooze(current_user, key, data.get("days") or 7)
    log_event("attention.snooze", source=key[:60])
    return jsonify({"ok": True})
