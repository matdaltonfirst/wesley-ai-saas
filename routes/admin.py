"""Super admin routes: system prompt, church management."""

import string
import secrets
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from models import db, User, Church, SystemPrompt
from config import DEFAULT_SYSTEM_PROMPT
from helpers import is_super_admin

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin")
@login_required
def admin_panel():
    if not is_super_admin():
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
    if not is_super_admin():
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


@admin_bp.route("/api/admin/churches", methods=["GET"])
@login_required
def admin_list_churches():
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403

    churches = Church.query.order_by(Church.created_at.desc()).all()

    total_messages = db.session.execute(
        text("SELECT COUNT(*) FROM messages")
    ).scalar() or 0
    total_widget_messages = db.session.execute(
        text("SELECT COUNT(*) FROM widget_messages")
    ).scalar() or 0
    total_all_messages = total_messages + total_widget_messages

    now = datetime.utcnow()
    active_subs = 0
    trialing = 0
    for c in churches:
        if c.stripe_subscription_id:
            active_subs += 1
        elif c.trial_ends_at and c.trial_ends_at > now:
            trialing += 1

    stats = {
        "total_churches":       len(churches),
        "total_messages":       total_all_messages,
        "active_subscriptions": active_subs,
        "trialing":             trialing,
    }

    church_list = []
    for c in churches:
        first_user = User.query.filter_by(church_id=c.id).order_by(User.created_at).first()
        admin_email = first_user.email if first_user else ""

        msg_count = db.session.execute(
            text("SELECT COUNT(*) FROM messages m "
                 "JOIN conversations cv ON cv.id = m.conversation_id "
                 "WHERE cv.church_id = :cid"),
            {"cid": c.id}
        ).scalar() or 0

        widget_msg_count = db.session.execute(
            text("SELECT COUNT(*) FROM widget_messages wm "
                 "JOIN widget_conversations wc ON wc.id = wm.widget_conversation_id "
                 "WHERE wc.church_id = :cid"),
            {"cid": c.id}
        ).scalar() or 0

        doc_count = db.session.execute(
            text("SELECT COUNT(*) FROM documents WHERE church_id = :cid"),
            {"cid": c.id}
        ).scalar() or 0

        if c.billing_exempt:
            status = "exempt"
        elif c.stripe_subscription_id:
            status = "active"
        elif c.trial_ends_at and c.trial_ends_at > now:
            status = "trialing"
        else:
            status = "expired"

        church_list.append({
            "id":                    c.id,
            "name":                  c.name,
            "admin_email":           admin_email,
            "church_city":           c.church_city or "",
            "created_at":            c.created_at.isoformat() if c.created_at else "",
            "trial_ends_at":         c.trial_ends_at.isoformat() if c.trial_ends_at else "",
            "plan":                  c.plan or "founders",
            "billing_exempt":        c.billing_exempt,
            "stripe_subscription_id": c.stripe_subscription_id or "",
            "status":                status,
            "message_count":         msg_count,
            "widget_message_count":  widget_msg_count,
            "doc_count":             doc_count,
        })

    return jsonify({"stats": stats, "churches": church_list})


@admin_bp.route("/api/admin/churches/<int:church_id>", methods=["PATCH"])
@login_required
def admin_update_church(church_id):
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403

    church = Church.query.get_or_404(church_id)
    data = request.get_json(silent=True) or {}

    if "name" in data:
        name = data["name"].strip()
        if name:
            church.name = name

    if "church_city" in data:
        church.church_city = data["church_city"].strip() or None

    if "plan" in data:
        plan = data["plan"].strip()
        if plan in ("founders", "small", "medium", "large"):
            church.plan = plan

    if "trial_ends_at" in data:
        val = data["trial_ends_at"]
        if not val:
            church.trial_ends_at = None
        else:
            try:
                if "T" in str(val):
                    church.trial_ends_at = datetime.fromisoformat(str(val)[:19])
                else:
                    church.trial_ends_at = datetime.strptime(str(val)[:10], "%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "Invalid trial_ends_at format. Use YYYY-MM-DD."}), 400

    if "billing_exempt" in data:
        church.billing_exempt = bool(data["billing_exempt"])

    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/churches/<int:church_id>/reset-password", methods=["POST"])
@login_required
def admin_reset_password(church_id):
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403

    church = Church.query.get_or_404(church_id)
    user = User.query.filter_by(church_id=church.id).order_by(User.created_at).first()
    if not user:
        return jsonify({"error": "No user found for this church."}), 404

    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(12))
    user.password_hash = generate_password_hash(temp_password)
    db.session.commit()

    return jsonify({"email": user.email, "temp_password": temp_password})
