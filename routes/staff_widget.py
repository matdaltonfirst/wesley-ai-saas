"""Staff-side views of what the public chatbot collects and says, and the
approved Q&A and snippets that shape it. Everything here needs a permission.

The public chatbot itself lives in ``routes/public_api.py``; this module may
import from it, never the other way round.
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user
from sqlalchemy.orm import joinedload

from helpers import iso_utc
from models import (
    AnswerFeedback, GuestConnection, PcoConnection, QnAPair, TextSnippet,
    WidgetConversation, WidgetMessage, db,
)
from permissions import require
from pco import person_url as pco_person_url
from routes.public_api import _is_low_confidence

log = logging.getLogger("wesley")

staff_widget_bp = Blueprint("staff_widget", __name__)


"""Widget routes: public CORS endpoints for branding, chat, and JS serving."""


@staff_widget_bp.route("/api/widget/conversations")
@require("chatlogs.read")
def list_widget_conversations():
    wconvs = (
        WidgetConversation.query
        .options(joinedload(WidgetConversation.messages))
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


@staff_widget_bp.route("/api/widget/conversations/<int:wconv_id>/messages")
@require("chatlogs.read")
def get_widget_conversation_messages(wconv_id):
    wconv = WidgetConversation.query.get(wconv_id)
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


@staff_widget_bp.route("/api/feedback")
@require("chatlogs.read")
def list_answer_feedback():
    status_filter = request.args.get("status", "open").strip()
    query = AnswerFeedback.query
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
                status="open"
            ).count(),
            "helpful": AnswerFeedback.query.filter_by(
                rating="helpful"
            ).count(),
            "not_helpful": AnswerFeedback.query.filter_by(
                rating="not_helpful"
            ).count(),
            "corrected": AnswerFeedback.query.filter_by(
                status="corrected"
            ).count(),
        },
        "items": [_feedback_dict(item) for item in items],
    })


@staff_widget_bp.route("/api/feedback/<int:feedback_id>/correct", methods=["POST"])
@require("chatlogs.write")
def correct_answer_feedback(feedback_id):
    feedback = AnswerFeedback.query.filter_by(
        id=feedback_id
    ).first()
    if not feedback:
        return jsonify({"error": "Feedback not found."}), 404

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not question or not answer:
        return jsonify({"error": "Question and corrected answer are required."}), 400

    pair = QnAPair(
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


@staff_widget_bp.route("/api/feedback/<int:feedback_id>/dismiss", methods=["POST"])
@require("chatlogs.write")
def dismiss_answer_feedback(feedback_id):
    feedback = AnswerFeedback.query.filter_by(
        id=feedback_id
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


def _categorize(text):
    t = text.lower()
    for name, keywords in _TOPIC_CATEGORIES[:-1]:
        if any(kw in t for kw in keywords):
            return name
    return "Other"


def _load_convs():
    return (
        WidgetConversation.query
        .options(joinedload(WidgetConversation.messages))
        .all()
    )


@staff_widget_bp.route("/api/analytics/chats")
@require("chatlogs.read")
def analytics_chats():
    convs = _load_convs()
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


@staff_widget_bp.route("/api/analytics/topics")
@require("chatlogs.read")
def analytics_topics():
    convs = _load_convs()

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


@staff_widget_bp.route("/api/analytics/sentiment")
@require("chatlogs.read")
def analytics_sentiment():
    convs = _load_convs()

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


@staff_widget_bp.route("/api/guest-connections")
@require("guests.read")
def list_guest_connections():
    status_filter = request.args.get("status", "").strip()
    q = GuestConnection.query
    if status_filter in ("new", "contacted", "connected"):
        q = q.filter_by(status=status_filter)
    connections = q.order_by(GuestConnection.created_at.desc()).all()

    new_count       = GuestConnection.query.filter_by(status="new").count()
    contacted_count = GuestConnection.query.filter_by(status="contacted").count()
    connected_count = GuestConnection.query.filter_by(status="connected").count()

    return jsonify({
        "pco_connected": PcoConnection.query.first() is not None,
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
                "pco_person_id": gc.pco_person_id or "",
                "pco_url": pco_person_url(gc.pco_person_id) if gc.pco_person_id else "",
                "pco_sync_error": gc.pco_sync_error or "",
                "pco_sync_status": gc.pco_sync_status or "",
                "pco_sync_attempts": gc.pco_sync_attempts or 0,
            }
            for gc in connections
        ],
    })


@staff_widget_bp.route("/api/guest-connection/<int:gc_id>", methods=["PATCH"])
@require("guests.write")
def update_guest_connection(gc_id):
    gc = GuestConnection.query.filter_by(id=gc_id).first()
    if not gc:
        return jsonify({"error": "Not found."}), 404

    data = request.get_json(silent=True) or {}
    if "status" in data and data["status"] in ("new", "contacted", "connected"):
        gc.status = data["status"]
    if "notes" in data:
        gc.notes = data["notes"]

    db.session.commit()
    return jsonify({"ok": True})


_SNIPPET_CATEGORIES = [
    "Staff & Leadership",
    "Service & Worship",
    "Events & Programs",
    "Practical Info",
    "Beliefs & Values",
    "Other",
]


@staff_widget_bp.route("/api/snippets", methods=["GET"])
@require("kb.read")
def list_snippets():
    snippets = TextSnippet.query.order_by(TextSnippet.created_at.desc()).all()
    return jsonify({
        "snippets": [_snippet_dict(s) for s in snippets],
        "categories": _SNIPPET_CATEGORIES,
    })


@staff_widget_bp.route("/api/snippets", methods=["POST"])
@require("kb.write")
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
        title=title[:200],
        content=content[:1000],
        category=category,
        audience=_audience(data.get("audience")),
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({"ok": True, "snippet": _snippet_dict(s)}), 201


@staff_widget_bp.route("/api/snippets/<int:sid>", methods=["PATCH"])
@require("kb.write")
def update_snippet(sid):
    s = TextSnippet.query.filter_by(id=sid).first()
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
    if "audience" in data:
        s.audience = _audience(data["audience"])
    if "is_active" in data:
        s.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"ok": True, "snippet": _snippet_dict(s)})


@staff_widget_bp.route("/api/snippets/<int:sid>", methods=["DELETE"])
@require("kb.write")
def delete_snippet(sid):
    s = TextSnippet.query.filter_by(id=sid).first()
    if not s:
        return jsonify({"error": "Not found."}), 404
    db.session.delete(s)
    db.session.commit()
    return jsonify({"ok": True})


def _audience(value) -> str:
    """"public" content may reach the website chatbot; "staff" never does.

    A missing value means public, because these screens have always been the
    chatbot's knowledge. Any other value is treated as staff-only.
    """
    return "public" if value in (None, "public") else "staff"


def _snippet_dict(s):
    return {
        "id": s.id,
        "title": s.title,
        "content": s.content,
        "category": s.category or "",
        "audience": s.audience,
        "is_active": s.is_active,
        "created_at": iso_utc(s.created_at),
    }


@staff_widget_bp.route("/api/qna", methods=["GET"])
@require("kb.read")
def list_qna():
    pairs = QnAPair.query.order_by(QnAPair.created_at.desc()).all()
    return jsonify({"pairs": [_qna_dict(p) for p in pairs]})


@staff_widget_bp.route("/api/qna", methods=["POST"])
@require("kb.write")
def create_qna():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    answer   = (data.get("answer") or "").strip()
    if not question or not answer:
        return jsonify({"error": "Question and answer are required."}), 400
    p = QnAPair(
        question=question[:500],
        answer=answer,
        audience=_audience(data.get("audience")),
        is_active=bool(data.get("is_active", True)),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "pair": _qna_dict(p)}), 201


@staff_widget_bp.route("/api/qna/<int:pid>", methods=["PATCH"])
@require("kb.write")
def update_qna(pid):
    p = QnAPair.query.filter_by(id=pid).first()
    if not p:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    if "question" in data:
        p.question = (data["question"] or "").strip()[:500]
    if "answer" in data:
        p.answer = (data["answer"] or "").strip()
    if "audience" in data:
        p.audience = _audience(data["audience"])
    if "is_active" in data:
        p.is_active = bool(data["is_active"])
    db.session.commit()
    return jsonify({"ok": True, "pair": _qna_dict(p)})


@staff_widget_bp.route("/api/qna/<int:pid>", methods=["DELETE"])
@require("kb.write")
def delete_qna(pid):
    p = QnAPair.query.filter_by(id=pid).first()
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
        "audience": p.audience,
        "is_active": p.is_active,
        "created_at": iso_utc(p.created_at),
    }
