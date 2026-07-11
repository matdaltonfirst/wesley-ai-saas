"""Super admin routes: system prompt, church management, billing panel."""

import os
import string
import secrets
import threading
from datetime import datetime, date

import stripe
from flask import Blueprint, request, jsonify, render_template, url_for, current_app
from flask_login import login_required, current_user
from sqlalchemy import text
from werkzeug.security import generate_password_hash

from models import db, User, Church, SystemPrompt
from config import DEFAULT_SYSTEM_PROMPT, FROM_EMAIL, APP_URL, SUPPORT_EMAIL
from helpers import is_super_admin, get_billing_status, iso_utc
from emails import send_stripe_invite_email

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
            "created_at":            iso_utc(c.created_at) or "",
            "trial_ends_at":         iso_utc(c.trial_ends_at) or "",
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


# ── Admin Billing Panel ───────────────────────────────────────────────────────

def _billing_row(church) -> dict:
    """Build the billing summary dict shown in the admin billing table."""
    today = date.today()
    first_user = User.query.filter_by(church_id=church.id).order_by(User.id).first()
    admin_email = first_user.email if first_user else ""
    bs = get_billing_status(church)

    # Determine status label for display
    mp_expires = getattr(church, "manual_payment_expires", None)
    mp_active  = getattr(church, "manual_payment_active", False)
    if bs["billing_type"] == "manual":
        days = bs["days_remaining"]
        status = "expiring_soon" if days is not None and days <= 30 else "active"
    elif bs["billing_type"] == "stripe":
        status = "active"
    elif mp_expires and mp_expires < today:
        # Previously had manual billing but it's lapsed
        status = "expired"
    else:
        status = "no_sub"

    invite_sent_at = getattr(church, "stripe_invite_sent_at", None)
    invite_resent_at = getattr(church, "stripe_invite_resent_at", None)

    return {
        "id":                    church.id,
        "name":                  church.name,
        "admin_email":           admin_email,
        "billing_type":          bs["billing_type"],
        "status":                status,
        "expires":               bs["expires"].isoformat() if bs["expires"] else None,
        "days_remaining":        bs["days_remaining"],
        "manual_payment_active": mp_active,
        "manual_payment_plan":   getattr(church, "manual_payment_plan", None) or "",
        "manual_payment_note":   getattr(church, "manual_payment_note", None) or "",
        "manual_payment_amount": float(church.manual_payment_amount) if getattr(church, "manual_payment_amount", None) else None,
        "manual_payment_start":  church.manual_payment_start.isoformat() if getattr(church, "manual_payment_start", None) else None,
        "stripe_customer_id":    church.stripe_customer_id or "",
        "stripe_invite_sent_at":  iso_utc(invite_sent_at),
        "stripe_invite_resent_at": iso_utc(invite_resent_at),
        "stripe_invite_sent":    bool(invite_sent_at),
        "plan":                  church.plan or "",
    }


@admin_bp.route("/admin/billing")
@login_required
def admin_billing():
    if not is_super_admin():
        return render_template("admin_billing.html", forbidden=True), 403
    churches = Church.query.order_by(Church.name).all()
    return render_template("admin_billing.html", forbidden=False, churches=churches)


@admin_bp.route("/api/admin/billing/churches")
@login_required
def admin_billing_churches():
    """Return billing rows for all churches (JSON)."""
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403
    churches = Church.query.order_by(Church.name).all()
    return jsonify({"churches": [_billing_row(c) for c in churches]})


@admin_bp.route("/api/admin/billing/mark-paid/<int:church_id>", methods=["POST"])
@login_required
def admin_billing_mark_paid(church_id):
    """Set a church to manual billing."""
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403

    church = Church.query.get_or_404(church_id)
    data = request.get_json(silent=True) or {}

    start_str   = (data.get("start_date") or "").strip()
    expires_str = (data.get("expires_date") or "").strip()
    plan        = (data.get("plan") or "monthly").strip()
    note        = (data.get("note") or "").strip()
    amount_raw  = data.get("amount")

    if not expires_str:
        return jsonify({"error": "Expiration date is required."}), 400
    if plan not in ("monthly", "annual"):
        return jsonify({"error": "Plan must be 'monthly' or 'annual'."}), 400

    try:
        expires = date.fromisoformat(expires_str)
    except ValueError:
        return jsonify({"error": "Invalid expiration date. Use YYYY-MM-DD."}), 400

    start = date.today()
    if start_str:
        try:
            start = date.fromisoformat(start_str)
        except ValueError:
            return jsonify({"error": "Invalid start date. Use YYYY-MM-DD."}), 400

    amount = None
    if amount_raw not in (None, ""):
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid amount."}), 400

    church.manual_payment_active  = True
    church.manual_payment_start   = start
    church.manual_payment_expires = expires
    church.manual_payment_plan    = plan
    church.manual_payment_note    = note[:500] if note else None
    church.manual_payment_amount  = amount
    church.manual_payment_set_by  = current_user.email
    # Reset warning flags so new period gets fresh notifications
    church.warning_30_sent = False
    church.warning_7_sent  = False
    church.expired_sent    = False

    db.session.commit()
    return jsonify({"ok": True, "church": _billing_row(church)})


@admin_bp.route("/api/admin/billing/revoke/<int:church_id>", methods=["POST"])
@login_required
def admin_billing_revoke(church_id):
    """Revoke manual billing for a church."""
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403

    church = Church.query.get_or_404(church_id)
    church.manual_payment_active = False
    db.session.commit()
    return jsonify({"ok": True, "church": _billing_row(church)})


@admin_bp.route("/api/admin/billing/send-invite/<int:church_id>", methods=["POST"])
@login_required
def admin_billing_send_invite(church_id):
    """Generate a Stripe checkout link and email it to the church admin."""
    if not is_super_admin():
        return jsonify({"error": "Forbidden."}), 403

    if not stripe.api_key:
        return jsonify({"error": "STRIPE_SECRET_KEY is not configured."}), 500

    church = Church.query.get_or_404(church_id)
    first_user = User.query.filter_by(church_id=church.id).order_by(User.id).first()
    if not first_user:
        return jsonify({"error": "No admin user found for this church."}), 404

    # Resolve price ID from the church's manual payment plan
    plan = getattr(church, "manual_payment_plan", None) or "monthly"
    if plan == "annual":
        price_id = os.getenv("STRIPE_ANNUAL_PRICE_ID") or os.getenv("STRIPE_MONTHLY_PRICE_ID")
    else:
        price_id = os.getenv("STRIPE_MONTHLY_PRICE_ID")

    if not price_id:
        return jsonify({"error": "No Stripe Price ID configured. Set STRIPE_MONTHLY_PRICE_ID."}), 500

    try:
        # Look up or create Stripe customer
        customer_id = church.stripe_customer_id
        if not customer_id:
            existing = stripe.Customer.list(email=first_user.email, limit=1)
            if existing.data:
                customer_id = existing.data[0].id
            else:
                customer = stripe.Customer.create(
                    email=first_user.email,
                    metadata={"church_id": str(church.id)},
                )
                customer_id = customer.id
            church.stripe_customer_id = customer_id

        # Create a Stripe Checkout Session
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            customer=customer_id,
            client_reference_id=str(church.id),
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{APP_URL}/stripe/success",
            cancel_url=APP_URL,
        )
        checkout_url = session.url
    except stripe.StripeError as e:
        return jsonify({"error": getattr(e, "user_message", str(e))}), 502

    # Send invite email in background thread with app context
    _email = first_user.email
    _name  = church.name
    _app   = current_app._get_current_object()

    def _send_stripe_invite():
        with _app.app_context():
            send_stripe_invite_email(_email, _name, checkout_url, FROM_EMAIL, SUPPORT_EMAIL)

    threading.Thread(target=_send_stripe_invite, daemon=True).start()

    # Record invite timestamp
    now = datetime.utcnow()
    is_resend = bool(church.stripe_invite_sent_at)
    if is_resend:
        church.stripe_invite_resent_at = now
    else:
        church.stripe_invite_sent_at = now

    db.session.commit()
    return jsonify({"ok": True, "resent": is_resend, "church": _billing_row(church)})
