"""HTML page routes: dashboard and settings."""

import json

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user

from helpers import build_branding_dict
from organization import get_org

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
@login_required
def chat_page():
    church = get_org()
    branding = build_branding_dict()
    return render_template(
        "dashboard.html",
        church_name=church.name,
        user_email=current_user.email,
        bot_name=branding["bot_name"],
        welcome_message=branding["welcome_message"],
        primary_color=branding["primary_color"],
        starter_questions=json.dumps(branding["starter_questions"]),
    )


@pages_bp.route("/dashboard")
@login_required
def management_dashboard():
    if current_user.role == "staff":
        return redirect(url_for("pages.chat_page"))
    church = get_org()
    branding = build_branding_dict()
    return render_template(
        "settings.html",
        church_name=church.name,
        church_id=church.legacy_widget_id or church.id,
        user_email=current_user.email,
        user_role=current_user.role,
        bot_name=branding["bot_name"],
        welcome_message=branding["welcome_message"],
        primary_color=branding["primary_color"],
        church_city=branding["church_city"],
    )
