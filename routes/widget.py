"""Widget routes: public CORS endpoints for branding, chat, and JS serving."""

import json
import re
import threading
import uuid
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, make_response, send_from_directory, current_app
from sqlalchemy.orm import joinedload
from flask_login import login_required, current_user

from models import (
    db, Church, User, WidgetConversation, WidgetMessage, GuestConnection,
    TextSnippet, QnAPair, AnswerFeedback,
)
from helpers import build_branding_dict, build_system_prompt, call_gemini, friendly_gemini_error, iso_utc
from config import FROM_EMAIL, APP_URL, SUPPORT_EMAIL
from emails import send_guest_connection_email
from documents import (
    load_church_web_content, load_chatbot_documents, load_curated_content,
    find_relevant_chunks,
    build_cited_context, select_cited_sources,
)

log = logging.getLogger("wesley")

widget_bp = Blueprint("widget", __name__)


# ── Widget JS serving ────────────────────────────────────────────────────────

def _widget_js_response():
    """Serve widget-core.js with appropriate headers for public embedding."""
    resp = make_response(send_from_directory("static", "widget-core.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@widget_bp.route("/widget.js")
def serve_widget():
    return _widget_js_response()


@widget_bp.route("/widget-core.js")
def serve_widget_core():
    return _widget_js_response()


# ── Widget Branding API (public, CORS) ───────────────────────────────────────

@widget_bp.route("/api/widget/branding", methods=["GET", "OPTIONS"])
def widget_branding():
    """Public endpoint returning church branding config for embedded widgets."""
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    branding_limiter = current_app.config["WIDGET_BRANDING_LIMITER"]
    if branding_limiter.is_limited(request.remote_addr or "unknown"):
        resp = jsonify({"error": "Rate limit exceeded. Please try again later."})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 429

    church_id = request.args.get("church_id", "").strip()
    if not church_id:
        return jsonify({"error": "church_id is required"}), 400

    try:
        church_id_int = int(church_id)
    except ValueError:
        return jsonify({"error": "Invalid church_id"}), 400

    church = Church.query.get(church_id_int)
    if not church:
        return jsonify({"error": "Church not found"}), 404

    resp = jsonify(build_branding_dict(church))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


# ── Widget Conversations API (staff dashboard, authenticated) ────────────────

@widget_bp.route("/api/widget/conversations")
@login_required
def list_widget_conversations():
    wconvs = (
        WidgetConversation.query
        .options(joinedload(WidgetConversation.messages))
        .filter_by(church_id=current_user.church_id)
        .order_by(WidgetConversation.updated_at.desc())
        .all()
    )
    result = []
    for wc in wconvs:
        first_msg = next((m for m in wc.messages if m.role == "user"), None)
        preview = (first_msg.content[:80] + "…") if first_msg and len(first_msg.content) > 80 else (first_msg.content if first_msg else "")
        result.append({
            "id": wc.id,
            "session_id": wc.session_id,
            "created_at": iso_utc(wc.created_at),
            "updated_at": iso_utc(wc.updated_at),
            "preview": preview,
            "message_count": len(wc.messages),
        })
    return jsonify({"conversations": result})


@widget_bp.route("/api/widget/conversations/<int:wconv_id>/messages")
@login_required
def get_widget_conversation_messages(wconv_id):
    wconv = WidgetConversation.query.filter_by(
        id=wconv_id, church_id=current_user.church_id
    ).first()
    if not wconv:
        return jsonify({"error": "Widget conversation not found."}), 404
    return jsonify({
        "id": wconv.id,
        "session_id": wconv.session_id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "sources": json.loads(m.sources) if m.sources else [],
                "created_at": iso_utc(m.created_at),
            }
            for m in wconv.messages
        ],
    })


# ── Widget Chat API (public, CORS) ───────────────────────────────────────────

@widget_bp.route("/api/widget/chat", methods=["POST", "OPTIONS"])
def widget_chat():
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    chat_limiter = current_app.config["WIDGET_CHAT_LIMITER"]
    if chat_limiter.is_limited(request.remote_addr or "unknown"):
        resp = jsonify({"error": "Rate limit exceeded. Please try again later."})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 429

    data = request.get_json(silent=True) or {}
    church_id_raw = data.get("church_id")
    question = (data.get("question") or "").strip()
    session_id = (data.get("session_id") or "").strip() or None

    def cors_err(msg, status=400):
        resp = jsonify({"error": msg})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, status

    if not church_id_raw or not question:
        return cors_err("church_id and question are required.")
    if len(question) > 2000:
        return cors_err("Message is too long. Please keep questions under 2,000 characters.")
    if session_id and len(session_id) > 64:
        return cors_err("Invalid session_id.")

    try:
        church_id = int(church_id_raw)
    except (ValueError, TypeError):
        return cors_err("Invalid church_id.")

    church = Church.query.get(church_id)
    if not church:
        return cors_err("Church not found.", 404)

    wconv = None
    if session_id:
        wconv = WidgetConversation.query.filter_by(
            church_id=church_id, session_id=session_id
        ).first()
    if not wconv:
        session_id = uuid.uuid4().hex
        wconv = WidgetConversation(church_id=church_id, session_id=session_id)
        db.session.add(wconv)
        db.session.flush()

    history = [
        {"role": m.role, "content": m.content}
        for m in wconv.messages
    ]

    db.session.add(WidgetMessage(
        widget_conversation_id=wconv.id, role="user", content=question
    ))

    MAX_DOC_CHUNKS = 5
    MAX_WEB_CHUNKS = 5

    uploads_dir = current_app.config["UPLOADS_DIR"]
    web_chunks = load_church_web_content(church_id)
    doc_chunks = load_chatbot_documents(church_id, uploads_dir) + load_curated_content(church_id)

    scored_docs = find_relevant_chunks(question, doc_chunks, top_n=MAX_DOC_CHUNKS) if doc_chunks else []
    scored_web = find_relevant_chunks(question, web_chunks, top_n=MAX_WEB_CHUNKS) if web_chunks else []

    context, candidate_sources = build_cited_context([scored_docs, scored_web])

    system_instruction = build_system_prompt(church, widget=True)

    try:
        answer = call_gemini(question, context, history, system_instruction)
    except ValueError as e:
        db.session.rollback()
        return cors_err(str(e), 500)
    except Exception as e:
        db.session.rollback()
        user_msg, status = friendly_gemini_error(e)
        return cors_err(user_msg, status)

    sources = select_cited_sources(answer, candidate_sources)
    assistant_message = WidgetMessage(
        widget_conversation_id=wconv.id,
        role="assistant",
        content=answer,
        sources=json.dumps(sources) if sources else None,
    )
    db.session.add(assistant_message)
    wconv.updated_at = datetime.utcnow()
    if _is_low_confidence(answer):
        # Surface unanswerable questions in the Feedback & Corrections inbox
        # even when the visitor never rates the answer.
        db.session.flush()
        db.session.add(AnswerFeedback(
            church_id=church_id,
            widget_message_id=assistant_message.id,
            rating="auto_flagged",
            status="open",
        ))
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.error("[WIDGET] DB commit failed: %s", e)
        return cors_err("Failed to save conversation. Please try again.", 500)

    resp = jsonify({
        "answer": answer,
        "sources": sources,
        "session_id": session_id,
        "message_id": assistant_message.id,
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ── Answer Feedback API ──────────────────────────────────────────────────────

_FEEDBACK_REASONS = {"incorrect", "outdated", "incomplete", "confusing", "other"}


@widget_bp.route("/api/widget/feedback", methods=["POST", "OPTIONS"])
def submit_answer_feedback():
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    data = request.get_json(silent=True) or {}
    rating = (data.get("rating") or "").strip()
    reason = (data.get("reason") or "").strip() or None
    comment = (data.get("comment") or "").strip() or None
    session_id = (data.get("session_id") or "").strip()

    try:
        church_id = int(data.get("church_id"))
        message_id = int(data.get("message_id"))
    except (TypeError, ValueError):
        return _feedback_cors_error("Invalid feedback target.")

    if rating not in ("helpful", "not_helpful"):
        return _feedback_cors_error("Invalid rating.")
    if reason and reason not in _FEEDBACK_REASONS:
        return _feedback_cors_error("Invalid feedback reason.")
    if len(comment or "") > 1000:
        return _feedback_cors_error("Feedback must be 1,000 characters or fewer.")

    message = (
        WidgetMessage.query
        .join(WidgetConversation)
        .filter(
            WidgetMessage.id == message_id,
            WidgetMessage.role == "assistant",
            WidgetConversation.church_id == church_id,
            WidgetConversation.session_id == session_id,
        )
        .first()
    )
    if not message:
        return _feedback_cors_error("Answer not found.", 404)

    feedback = AnswerFeedback.query.filter_by(widget_message_id=message.id).first()
    if not feedback:
        feedback = AnswerFeedback(church_id=church_id, widget_message_id=message.id)
        db.session.add(feedback)
    feedback.rating = rating
    feedback.reason = reason if rating == "not_helpful" else None
    feedback.comment = comment if rating == "not_helpful" else None
    feedback.status = "open" if rating == "not_helpful" else "dismissed"
    if rating == "helpful":
        feedback.resolved_at = datetime.utcnow()
    else:
        feedback.resolved_at = None
    db.session.commit()

    resp = jsonify({"ok": True})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp, 201


def _feedback_cors_error(message, status=400):
    resp = jsonify({"error": message})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp, status


@widget_bp.route("/api/feedback")
@login_required
def list_answer_feedback():
    status_filter = request.args.get("status", "open").strip()
    query = AnswerFeedback.query.filter_by(church_id=current_user.church_id)
    if status_filter in ("open", "corrected", "dismissed"):
        query = query.filter_by(status=status_filter)
    if status_filter == "dismissed":
        # Helpful ratings are stored as dismissed bookkeeping rows; only staff-
        # dismissed items (thumbs-down or auto-flagged) belong in this tab.
        query = query.filter(AnswerFeedback.rating != "helpful")
    items = query.order_by(AnswerFeedback.created_at.desc()).all()

    return jsonify({
        "stats": {
            "open": AnswerFeedback.query.filter_by(
                church_id=current_user.church_id, status="open"
            ).count(),
            "helpful": AnswerFeedback.query.filter_by(
                church_id=current_user.church_id, rating="helpful"
            ).count(),
            "not_helpful": AnswerFeedback.query.filter_by(
                church_id=current_user.church_id, rating="not_helpful"
            ).count(),
            "corrected": AnswerFeedback.query.filter_by(
                church_id=current_user.church_id, status="corrected"
            ).count(),
        },
        "items": [_feedback_dict(item) for item in items],
    })


@widget_bp.route("/api/feedback/<int:feedback_id>/correct", methods=["POST"])
@login_required
def correct_answer_feedback(feedback_id):
    feedback = AnswerFeedback.query.filter_by(
        id=feedback_id, church_id=current_user.church_id
    ).first()
    if not feedback:
        return jsonify({"error": "Feedback not found."}), 404

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not question or not answer:
        return jsonify({"error": "Question and corrected answer are required."}), 400

    pair = QnAPair(
        church_id=current_user.church_id,
        question=question[:500],
        answer=answer,
        is_active=True,
    )
    db.session.add(pair)
    db.session.flush()
    feedback.status = "corrected"
    feedback.corrected_answer = answer
    feedback.qna_pair_id = pair.id
    feedback.resolved_by_id = current_user.id
    feedback.resolved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "pair": _qna_dict(pair)})


@widget_bp.route("/api/feedback/<int:feedback_id>/dismiss", methods=["POST"])
@login_required
def dismiss_answer_feedback(feedback_id):
    feedback = AnswerFeedback.query.filter_by(
        id=feedback_id, church_id=current_user.church_id
    ).first()
    if not feedback:
        return jsonify({"error": "Feedback not found."}), 404
    feedback.status = "dismissed"
    feedback.resolved_by_id = current_user.id
    feedback.resolved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


def _feedback_dict(feedback):
    message = feedback.widget_message
    question_message = (
        WidgetMessage.query
        .filter(
            WidgetMessage.widget_conversation_id == message.widget_conversation_id,
            WidgetMessage.role == "user",
            WidgetMessage.id < message.id,
        )
        .order_by(WidgetMessage.id.desc())
        .first()
    )
    return {
        "id": feedback.id,
        "rating": feedback.rating,
        "reason": feedback.reason or "",
        "comment": feedback.comment or "",
        "status": feedback.status,
        "question": question_message.content if question_message else "",
        "answer": message.content,
        "sources": json.loads(message.sources) if message.sources else [],
        "corrected_answer": feedback.corrected_answer or "",
        "qna_pair_id": feedback.qna_pair_id,
        "created_at": iso_utc(feedback.created_at),
        "resolved_at": iso_utc(feedback.resolved_at),
    }


# ── Analytics API (authenticated) ────────────────────────────────────────────

_TOPIC_CATEGORIES = [
    ("Events & Programs",    ["event", "events", "coming up", "happening", "when", "schedule"]),
    ("Service Times",        ["service", "worship", "time", "sunday", "start", "begin"]),
    ("Food & Fellowship",    ["dinner", "food", "lunch", "meal", "eat", "fellowship"]),
    ("Prayer & Care",        ["prayer", "pray", "sick", "hospital", "need", "help", "care"]),
    ("Giving & Finance",     ["give", "giving", "donate", "tithe", "offering"]),
    ("Directions & Location",["where", "address", "located", "directions", "parking", "find"]),
    ("Beliefs & Theology",   ["believe", "belief", "communion", "baptism", "what does"]),
    ("Livestream & Media",   ["livestream", "live stream", "watch", "online", "video"]),
    ("Other",                []),
]

# Patterns tolerate the model's phrasing variations, e.g. "I don't have that
# specific information" or "I do not currently have details about that".
_LOW_CONFIDENCE_RE = re.compile(
    r"i (?:don'?t|do not) (?:\w+ ){0,3}(?:have|know)"
    r"|i'?m not (?:\w+ )?sure"
    r"|couldn'?t find|could not find"
    r"|no (?:\w+ )?information (?:is )?(?:available|about|on)"
    r"|(?:information|that) is(?:n'?t| not) available"
    r"|i apologize"
)


def _is_low_confidence(text):
    return bool(_LOW_CONFIDENCE_RE.search((text or "").lower()))


def _categorize(text):
    t = text.lower()
    for name, keywords in _TOPIC_CATEGORIES[:-1]:
        if any(kw in t for kw in keywords):
            return name
    return "Other"


def _load_convs(church_id):
    return (
        WidgetConversation.query
        .options(joinedload(WidgetConversation.messages))
        .filter_by(church_id=church_id)
        .all()
    )


@widget_bp.route("/api/analytics/chats")
@login_required
def analytics_chats():
    convs = _load_convs(current_user.church_id)
    now = datetime.utcnow()

    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total_this_month = sum(1 for c in convs if c.created_at >= first_of_month)
    total_all_time = len(convs)
    avg_messages = round(
        sum(len(c.messages) for c in convs) / total_all_time, 1
    ) if convs else 0

    week_ago = now - timedelta(days=7)
    week_convs = [c for c in convs if c.created_at >= week_ago]
    day_counts = Counter(c.created_at.strftime("%A") for c in week_convs)
    most_active_day = day_counts.most_common(1)[0][0] if day_counts else "N/A"

    thirty_days_ago = now - timedelta(days=30)
    recent_convs = [c for c in convs if c.created_at >= thirty_days_ago]
    daily_counter = Counter(c.created_at.strftime("%Y-%m-%d") for c in recent_convs)
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    daily_counts = [{"date": d, "count": daily_counter.get(d, 0)} for d in dates]

    hourly_counter = Counter(c.created_at.hour for c in convs)
    hourly_counts = [{"hour": h, "count": hourly_counter.get(h, 0)} for h in range(24)]

    recent = sorted(convs, key=lambda c: c.updated_at, reverse=True)[:20]
    recent_list = []
    for c in recent:
        first_msg = next((m for m in c.messages if m.role == "user"), None)
        preview = ""
        if first_msg:
            preview = (first_msg.content[:80] + "…") if len(first_msg.content) > 80 else first_msg.content
        recent_list.append({
            "id": c.id,
            "preview": preview or "(no messages)",
            "message_count": len(c.messages),
            "created_at": iso_utc(c.created_at),
            "updated_at": iso_utc(c.updated_at),
        })

    return jsonify({
        "total_this_month": total_this_month,
        "total_all_time": total_all_time,
        "avg_messages": avg_messages,
        "most_active_day": most_active_day,
        "daily_counts": daily_counts,
        "hourly_counts": hourly_counts,
        "recent_conversations": recent_list,
    })


@widget_bp.route("/api/analytics/topics")
@login_required
def analytics_topics():
    convs = _load_convs(current_user.church_id)

    cat_examples = defaultdict(list)
    for conv in convs:
        first_msg = next((m for m in conv.messages if m.role == "user"), None)
        if first_msg:
            cat = _categorize(first_msg.content)
            cat_examples[cat].append(first_msg.content)

    total = sum(len(v) for v in cat_examples.values())
    categories = []
    for cat_name, _ in _TOPIC_CATEGORIES:
        items = cat_examples.get(cat_name, [])
        count = len(items)
        categories.append({
            "name": cat_name,
            "count": count,
            "percentage": round(count / total * 100, 1) if total else 0,
            "examples": items[-3:],
        })
    categories.sort(key=lambda x: x["count"], reverse=True)

    return jsonify({"categories": categories, "total": total})


@widget_bp.route("/api/analytics/sentiment")
@login_required
def analytics_sentiment():
    convs = _load_convs(current_user.church_id)

    needs_attention = []
    confident_count = 0
    gap_cats: Counter = Counter()

    for conv in convs:
        first_user = next((m for m in conv.messages if m.role == "user"), None)
        bot_msgs = [m for m in conv.messages if m.role == "assistant"]

        flagged = False
        for bot_msg in bot_msgs:
            if _is_low_confidence(bot_msg.content):
                flagged = True
                if first_user:
                    cat = _categorize(first_user.content)
                    gap_cats[cat] += 1
                    needs_attention.append({
                        "question": first_user.content[:150],
                        "response_snippet": bot_msg.content[:200],
                        "date": iso_utc(conv.created_at),
                        "category": cat,
                    })
                break
        if not flagged:
            confident_count += 1

    total = len(convs)
    attention_count = len(needs_attention)
    needs_attention.sort(key=lambda x: x["date"], reverse=True)

    return jsonify({
        "total": total,
        "confident_count": confident_count,
        "confident_pct": round(confident_count / total * 100, 1) if total else 0,
        "attention_count": attention_count,
        "attention_pct": round(attention_count / total * 100, 1) if total else 0,
        "needs_attention": needs_attention,
        "suggested_topics": [cat for cat, _ in gap_cats.most_common(3)],
    })


# ── Guest Connections API ────────────────────────────────────────────────────

@widget_bp.route("/api/guest-connection", methods=["POST", "OPTIONS"])
def create_guest_connection():
    """Public CORS endpoint — widget submits guest contact info here."""
    if request.method == "OPTIONS":
        resp = make_response("", 204)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    def cors_err(msg, status=400):
        r = jsonify({"error": msg})
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r, status

    data = request.get_json(silent=True) or {}
    church_id_raw = data.get("church_id")
    name  = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    interest_area   = (data.get("interest_area") or "General Interest").strip()
    opening_message = (data.get("opening_message") or "").strip()

    if not church_id_raw or not name or not email:
        return cors_err("church_id, name, and email are required.")

    try:
        church_id = int(church_id_raw)
    except (ValueError, TypeError):
        return cors_err("Invalid church_id.")

    church = Church.query.get(church_id)
    if not church:
        return cors_err("Church not found.", 404)

    gc = GuestConnection(
        church_id=church_id,
        name=name,
        email=email,
        phone=phone or None,
        interest_area=interest_area,
        opening_message=opening_message or None,
    )
    db.session.add(gc)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.error("[GUEST] DB commit failed: %s", e)
        return cors_err("Failed to save. Please try again.", 500)

    # Notify all admin users for this church (non-blocking)
    admin_users = User.query.filter_by(church_id=church_id, role="admin").all()
    dashboard_url = APP_URL.rstrip("/") + "/dashboard#guest-connections"
    _app = current_app._get_current_object()
    _church_name = church.name
    for admin in admin_users:
        def _send(to=admin.email, cn=_church_name, gn=name, ge=email, gp=phone,
                  ia=interest_area, om=opening_message, du=dashboard_url):
            with _app.app_context():
                send_guest_connection_email(to, cn, gn, ge, gp, ia, om, du, FROM_EMAIL, SUPPORT_EMAIL)
        threading.Thread(target=_send, daemon=True).start()

    resp = jsonify({"ok": True})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp, 201


@widget_bp.route("/api/guest-connections")
@login_required
def list_guest_connections():
    status_filter = request.args.get("status", "").strip()
    q = GuestConnection.query.filter_by(church_id=current_user.church_id)
    if status_filter in ("new", "contacted", "connected"):
        q = q.filter_by(status=status_filter)
    connections = q.order_by(GuestConnection.created_at.desc()).all()

    new_count       = GuestConnection.query.filter_by(church_id=current_user.church_id, status="new").count()
    contacted_count = GuestConnection.query.filter_by(church_id=current_user.church_id, status="contacted").count()
    connected_count = GuestConnection.query.filter_by(church_id=current_user.church_id, status="connected").count()

    return jsonify({
        "stats": {
            "new": new_count,
            "contacted": contacted_count,
            "connected": connected_count,
        },
        "connections": [
            {
                "id": gc.id,
                "name": gc.name,
                "email": gc.email,
                "phone": gc.phone or "",
                "interest_area": gc.interest_area or "General Interest",
                "opening_message": gc.opening_message or "",
                "status": gc.status,
                "notes": gc.notes or "",
                "created_at": iso_utc(gc.created_at),
            }
            for gc in connections
        ],
    })


@widget_bp.route("/api/guest-connection/<int:gc_id>", methods=["PATCH"])
@login_required
def update_guest_connection(gc_id):
    gc = GuestConnection.query.filter_by(id=gc_id, church_id=current_user.church_id).first()
    if not gc:
        return jsonify({"error": "Not found."}), 404

    data = request.get_json(silent=True) or {}
    if "status" in data and data["status"] in ("new", "contacted", "connected"):
        gc.status = data["status"]
    if "notes" in data:
        gc.notes = data["notes"]

    db.session.commit()
    return jsonify({"ok": True})


# ── Text Snippets API (authenticated) ────────────────────────────────────────

_SNIPPET_CATEGORIES = [
    "Staff & Leadership",
    "Service & Worship",
    "Events & Programs",
    "Practical Info",
    "Beliefs & Values",
    "Other",
]


@widget_bp.route("/api/snippets", methods=["GET"])
@login_required
def list_snippets():
    snippets = TextSnippet.query.filter_by(
        church_id=current_user.church_id
    ).order_by(TextSnippet.created_at.desc()).all()
    return jsonify({
        "snippets": [_snippet_dict(s) for s in snippets],
        "categories": _SNIPPET_CATEGORIES,
    })


@widget_bp.route("/api/snippets", methods=["POST"])
@login_required
def create_snippet():
    data = request.get_json(silent=True) or {}
    title   = (data.get("title") or "").strip()
    content = (data.get("content") or "").strip()
    if not title or not content:
        return jsonify({"error": "Title and content are required."}), 400
    category = (data.get("category") or "").strip() or None
    if category and category not in _SNIPPET_CATEGORIES:
        category = "Other"
    s = TextSnippet(
        church_id=current_user.church_id,
        title=title[:200],
        content=content[:1000],
        category=category,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({"ok": True, "snippet": _snippet_dict(s)}), 201


@widget_bp.route("/api/snippets/<int:sid>", methods=["PATCH"])
@login_required
def update_snippet(sid):
    s = TextSnippet.query.filter_by(id=sid, church_id=current_user.church_id).first()
    if not s:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    if "title" in data:
        s.title = (data["title"] or "").strip()[:200]
    if "content" in data:
        s.content = (data["content"] or "").strip()[:1000]
    if "category" in data:
        cat = (data["category"] or "").strip() or None
        s.category = cat if (cat is None or cat in _SNIPPET_CATEGORIES) else "Other"
    if "is_active" in data:
        s.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"ok": True, "snippet": _snippet_dict(s)})


@widget_bp.route("/api/snippets/<int:sid>", methods=["DELETE"])
@login_required
def delete_snippet(sid):
    s = TextSnippet.query.filter_by(id=sid, church_id=current_user.church_id).first()
    if not s:
        return jsonify({"error": "Not found."}), 404
    db.session.delete(s)
    db.session.commit()
    return jsonify({"ok": True})


def _snippet_dict(s):
    return {
        "id": s.id,
        "title": s.title,
        "content": s.content,
        "category": s.category or "",
        "is_active": s.is_active,
        "created_at": iso_utc(s.created_at),
    }


# ── Q&A Pairs API (authenticated) ─────────────────────────────────────────────

@widget_bp.route("/api/qna", methods=["GET"])
@login_required
def list_qna():
    pairs = QnAPair.query.filter_by(
        church_id=current_user.church_id
    ).order_by(QnAPair.created_at.desc()).all()
    return jsonify({"pairs": [_qna_dict(p) for p in pairs]})


@widget_bp.route("/api/qna", methods=["POST"])
@login_required
def create_qna():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    answer   = (data.get("answer") or "").strip()
    if not question or not answer:
        return jsonify({"error": "Question and answer are required."}), 400
    p = QnAPair(
        church_id=current_user.church_id,
        question=question[:500],
        answer=answer,
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "pair": _qna_dict(p)}), 201


@widget_bp.route("/api/qna/<int:pid>", methods=["PATCH"])
@login_required
def update_qna(pid):
    p = QnAPair.query.filter_by(id=pid, church_id=current_user.church_id).first()
    if not p:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    if "question" in data:
        p.question = (data["question"] or "").strip()[:500]
    if "answer" in data:
        p.answer = (data["answer"] or "").strip()
    if "is_active" in data:
        p.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"ok": True, "pair": _qna_dict(p)})


@widget_bp.route("/api/qna/<int:pid>", methods=["DELETE"])
@login_required
def delete_qna(pid):
    p = QnAPair.query.filter_by(id=pid, church_id=current_user.church_id).first()
    if not p:
        return jsonify({"error": "Not found."}), 404
    db.session.delete(p)
    db.session.commit()
    return jsonify({"ok": True})


def _qna_dict(p):
    return {
        "id": p.id,
        "question": p.question,
        "answer": p.answer,
        "is_active": p.is_active,
        "created_at": iso_utc(p.created_at),
    }
