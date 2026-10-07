"""HTML page routes: the staff chat and the management dashboard."""

import json

from flask import Blueprint, render_template
from flask_login import current_user

from helpers import build_branding_dict
from organization import get_org
from permissions import can, require, require_any

pages_bp = Blueprint("pages", __name__)

# Seeing the management dashboard needs at least one of these.
DASHBOARD_PERMISSIONS = (
    "sunday.read", "guests.read", "chatlogs.read", "kb.read", "sources.write",
    "integrations.read", "team.manage", "instructions.read", "audit.read",
)


@pages_bp.route("/home")
@require_any("chat.use", *DASHBOARD_PERMISSIONS)
def home_page():
    """Where each person lands: what their roles need first, with live counts."""
    from models import AnswerFeedback, CommsRequest, GuestConnection, SermonPacket
    cards = []
    if can("guests.read"):
        cards.append({"title": "Guests to follow up", "link": "/dashboard#guest-connections",
                      "count": GuestConnection.query.filter_by(status="new").count(),
                      "note": "new website guest connections"})
    if can("requests.manage"):
        cards.append({"title": "Communications queue", "link": "/comms/admin",
                      "count": CommsRequest.query.filter(
                          CommsRequest.status.in_(["in_queue", "in_progress"])).count(),
                      "note": "requests waiting or in progress"})
    elif can("requests.submit"):
        cards.append({"title": "Request something from communications", "link": "/comms/new",
                      "count": None, "note": "flyers, graphics and video"})
    if can("sunday.read"):
        cards.append({"title": "Sunday Content", "link": "/dashboard#sermon-packets",
                      "count": SermonPacket.query.filter_by(status="ready").count(),
                      "note": "packets ready to review"})
    if can("chatlogs.write"):
        cards.append({"title": "Chatbot answers to correct", "link": "/dashboard#feedback",
                      "count": AnswerFeedback.query.filter_by(status="open").count(),
                      "note": "flagged or thumbs-down answers"})
    if can("chat.use"):
        cards.append({"title": "Ask Wesley", "link": "/", "count": None,
                      "note": "questions about policy, events, sermons and more"})
    if can("team.manage"):
        cards.append({"title": "People and roles", "link": "/dashboard#team", "count": None,
                      "note": "who can sign in and what they can do"})
    from permissions import ROLES
    return render_template(
        "home.html", church_name=get_org().name, user_email=current_user.email,
        name=current_user.display_name or current_user.email,
        role_labels=[ROLES[r] for r in sorted(current_user.roles) if r in ROLES],
        cards=cards, show_dashboard_link=any(can(p) for p in DASHBOARD_PERMISSIONS),
    )


@pages_bp.route("/")
@require("chat.use")
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
        show_dashboard_link=any(can(p) for p in DASHBOARD_PERMISSIONS),
    )


@pages_bp.route("/dashboard")
@require_any(*DASHBOARD_PERMISSIONS)
def management_dashboard():
    church = get_org()
    branding = build_branding_dict()
    return render_template(
        "settings.html",
        church_name=church.name,
        church_id=church.legacy_widget_id or church.id,
        user_email=current_user.email,
        user_role="admin" if can("team.manage") else "staff",
        perms=sorted(p for p in __import__("permissions").PERMISSIONS if can(p)),
        bot_name=branding["bot_name"],
        welcome_message=branding["welcome_message"],
        primary_color=branding["primary_color"],
        church_city=branding["church_city"],
    )
