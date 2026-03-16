"""Widget routes: public CORS endpoints for branding, chat, and JS serving."""

import uuid
import logging
from datetime import datetime

from flask import Blueprint, request, jsonify, make_response, send_from_directory, current_app
from sqlalchemy.orm import joinedload
from flask_login import login_required, current_user

from models import db, Church, WidgetConversation, WidgetMessage
from helpers import build_branding_dict, build_system_prompt, call_gemini, friendly_gemini_error
from documents import (
    load_church_web_content, load_chatbot_documents,
    extract_keywords, score_chunk, find_relevant_chunks, build_context_block,
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
            "created_at": wc.created_at.isoformat(),
            "updated_at": wc.updated_at.isoformat(),
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
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
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
    doc_chunks = load_chatbot_documents(church_id, uploads_dir)

    scored_docs: list[tuple[int, dict]] = []
    if doc_chunks:
        keywords = extract_keywords(question)
        if keywords:
            scored_docs = sorted(
                [(score_chunk(c, keywords), c) for c in doc_chunks],
                key=lambda x: x[0], reverse=True,
            )[:MAX_DOC_CHUNKS]
        else:
            scored_docs = [(0, c) for c in doc_chunks[:MAX_DOC_CHUNKS]]

    scored_web = find_relevant_chunks(question, web_chunks, top_n=MAX_WEB_CHUNKS) if web_chunks else []

    context_parts = []
    if scored_docs:
        context_parts.append(build_context_block(scored_docs))
    if scored_web:
        context_parts.append(build_context_block(scored_web))
    context = "\n".join(context_parts)

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

    db.session.add(WidgetMessage(
        widget_conversation_id=wconv.id, role="assistant", content=answer
    ))
    wconv.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.error("[WIDGET] DB commit failed: %s", e)
        return cors_err("Failed to save conversation. Please try again.", 500)

    resp = jsonify({"answer": answer, "session_id": session_id})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp
