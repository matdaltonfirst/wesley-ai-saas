"""Admin routes: the public chatbot's editable instructions and AI usage."""

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required

from models import db, SystemPrompt
from config import DEFAULT_SYSTEM_PROMPT
from helpers import is_admin
from usage import usage_totals

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin")
@login_required
def admin_panel():
    if not is_admin():
        return render_template("admin.html", forbidden=True), 403
    prompt_row = SystemPrompt.query.get(1)
    current_prompt = prompt_row.content if prompt_row else DEFAULT_SYSTEM_PROMPT
    return render_template(
        "admin.html",
        forbidden=False,
        current_prompt=current_prompt,
        default_prompt=DEFAULT_SYSTEM_PROMPT,
    )


@admin_bp.route("/api/admin/system-prompt", methods=["POST"])
@login_required
def update_system_prompt():
    if not is_admin():
        return jsonify({"error": "Forbidden."}), 403
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Prompt content cannot be empty."}), 400
    prompt_row = SystemPrompt.query.get(1)
    if prompt_row:
        prompt_row.content = content
    else:
        db.session.add(SystemPrompt(id=1, content=content))
    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/usage")
@login_required
def admin_usage():
    """AI calls and tokens over the last 30 days, split by staff and widget."""
    if not is_admin():
        return jsonify({"error": "Forbidden."}), 403
    return jsonify(usage_totals(days=30))
