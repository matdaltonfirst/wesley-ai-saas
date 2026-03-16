"""HTML page routes: dashboard, onboarding, settings."""

import json

from flask import Blueprint, request, jsonify, render_template, redirect, url_for
from flask_login import login_required, current_user

from models import db
from helpers import build_branding_dict, require_active

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
@login_required
def chat_page():
    church = current_user.church
    if not church.onboarding_complete:
        return redirect(url_for("pages.onboarding_page"))
    check = require_active()
    if check:
        return check
    branding = build_branding_dict(church)
    return render_template(
        "dashboard.html",
        church_name=church.name,
        user_email=current_user.email,
        bot_name=branding["bot_name"],
        welcome_message=branding["welcome_message"],
        primary_color=branding["primary_color"],
        starter_questions=json.dumps(branding["starter_questions"]),
    )


@pages_bp.route("/onboarding")
@login_required
def onboarding_page():
    if current_user.church.onboarding_complete:
        return redirect(url_for("pages.chat_page"))
    church = current_user.church
    return render_template(
        "onboarding.html",
        church_name=church.name,
        church_id=church.id,
    )


@pages_bp.route("/api/onboarding/step1", methods=["POST"])
@login_required
def onboarding_step1():
    """Save church name + city and mark onboarding complete."""
    data = request.get_json(silent=True) or {}
    church_name = (data.get("church_name") or "").strip()
    church_city = (data.get("church_city") or "").strip()

    if not church_name:
        return jsonify({"error": "Church name cannot be empty."}), 400

    church = current_user.church
    church.name = church_name[:200]
    church.church_city = church_city[:200] if church_city else None
    church.onboarding_complete = True
    db.session.commit()
    return jsonify({"ok": True})


@pages_bp.route("/dashboard")
@login_required
def management_dashboard():
    if current_user.role == "staff":
        return redirect(url_for("pages.chat_page"))
    check = require_active()
    if check:
        return check
    church = current_user.church
    branding = build_branding_dict(church)
    return render_template(
        "settings.html",
        church_name=church.name,
        church_id=current_user.church_id,
        user_email=current_user.email,
        user_role=current_user.role,
        bot_name=branding["bot_name"],
        welcome_message=branding["welcome_message"],
        primary_color=branding["primary_color"],
        church_city=branding["church_city"],
        has_stripe_sub=bool(church.stripe_subscription_id),
    )
