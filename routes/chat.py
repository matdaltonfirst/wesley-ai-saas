"""Chat API routes: staff chat, conversations list, conversation messages."""

import json
import logging

from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from models import db, Conversation, Message
from helpers import (
    build_system_prompt, call_gemini, friendly_gemini_error, has_active_access,
    iso_utc, sse_event, stream_gemini,
)
from documents import (
    load_church_documents, load_curated_content, find_relevant_chunks,
    build_cited_context, select_cited_sources,
)
from calendar_feed import load_calendar_chunks, score_calendar_chunks
from sermons import load_sermon_chunks, score_sermon_chunks
from denominations import score_denomination_chunks
from usage import STAFF, record_usage

log = logging.getLogger("wesley")

chat_bp = Blueprint("chat", __name__)


class _ChatTurnError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _prepare_chat_turn(data):
    """Validate a staff request and build everything the model call needs.

    Shared by the blocking and streaming endpoints so neither can drift on
    validation, billing, or retrieval.
    """
    if not data or not data.get("question", "").strip():
        raise _ChatTurnError("No question provided")

    question = data["question"].strip()
    if len(question) > 2000:
        raise _ChatTurnError(
            "Message is too long. Please keep questions under 2,000 characters.")

    # The dashboard page redirects lapsed churches to /subscribe, but this
    # endpoint is what actually spends money — gate it too.
    if not has_active_access(current_user.church, current_user.email):
        raise _ChatTurnError(
            "Your subscription has ended. Reactivate under Settings → Billing to keep using Wesley.",
            402,
        )

    conversation_id = data.get("conversation_id")
    if conversation_id:
        conv = Conversation.query.filter_by(
            id=conversation_id, church_id=current_user.church_id
        ).first()
        if not conv:
            raise _ChatTurnError("Conversation not found.", 404)
    else:
        conv = Conversation(church_id=current_user.church_id, title=question[:40])
        db.session.add(conv)
        db.session.flush()

    history = [{"role": m.role, "content": m.content} for m in conv.messages]
    db.session.add(Message(conversation_id=conv.id, role="user", content=question))
    # Committed here rather than alongside the answer: the streaming response
    # generator runs after this request context is gone, so nothing may be left
    # pending in a session it will not be able to reach.
    db.session.commit()

    uploads_dir = current_app.config["UPLOADS_DIR"]
    chunks = (
        load_church_documents(current_user.church_id, uploads_dir)
        + load_curated_content(current_user.church_id)
    )
    context = ""
    candidate_sources = []

    scored = find_relevant_chunks(question, chunks) if chunks else []
    scored_cal = score_calendar_chunks(
        question, load_calendar_chunks(current_user.church_id)
    )
    scored_ser = score_sermon_chunks(
        question, load_sermon_chunks(current_user.church_id)
    )
    # Only this church's own denomination is ever a retrieval candidate.
    scored_denom = score_denomination_chunks(
        question, current_user.church.denomination
    )
    if scored or scored_cal or scored_ser or scored_denom:
        context, candidate_sources = build_cited_context(
            [scored, scored_cal, scored_ser, scored_denom]
        )

    # Ids, never ORM instances: the streaming generator runs outside this
    # request's session, where a live object would be detached.
    return {
        "question": question,
        "conv_id": conv.id,
        "history": history,
        "context": context,
        "candidate_sources": candidate_sources,
        "system_instruction": build_system_prompt(current_user.church, staff=True),
    }


def _save_chat_answer(turn, answer):
    """Persist the assistant turn. Re-queries by id so this works from inside
    the streaming generator, which no longer shares the request's session."""
    conv = Conversation.query.get(turn["conv_id"])
    sources = select_cited_sources(answer, turn["candidate_sources"])
    db.session.add(Message(
        conversation_id=turn["conv_id"],
        role="assistant",
        content=answer,
        sources=json.dumps(sources) if sources else None,
    ))
    if conv:
        conv.updated_at = datetime.utcnow()
    db.session.commit()
    return sources


@chat_bp.route("/api/chat", methods=["POST"])
@login_required
def chat():
    limiter = current_app.config.get("CHAT_LIMITER")
    if limiter and limiter.is_limited(str(current_user.church_id)):
        return jsonify({"error": "Too many requests. Please slow down and try again."}), 429

    try:
        turn = _prepare_chat_turn(request.get_json(silent=True))
    except _ChatTurnError as e:
        db.session.rollback()
        return jsonify({"error": e.message}), e.status

    call_usage: dict = {}
    try:
        answer = call_gemini(
            turn["question"], turn["context"], turn["history"],
            turn["system_instruction"], usage=call_usage,
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        db.session.rollback()
        user_msg, status = friendly_gemini_error(e)
        return jsonify({"error": user_msg}), status

    sources = _save_chat_answer(turn, answer)
    record_usage(current_user.church_id, STAFF, call_usage)

    return jsonify({
        "answer": answer, "sources": sources, "conversation_id": turn["conv_id"],
    })


@chat_bp.route("/api/chat/stream", methods=["POST"])
@login_required
def chat_stream():
    """Server-sent events version of staff chat.

    Staff ask for sermon outlines and drafts — the answers are long, so waiting
    for the whole thing before anything appears is the worst case for this
    endpoint, not the best.
    """
    limiter = current_app.config.get("CHAT_LIMITER")
    if limiter and limiter.is_limited(str(current_user.church_id)):
        return jsonify({"error": "Too many requests. Please slow down and try again."}), 429

    try:
        turn = _prepare_chat_turn(request.get_json(silent=True))
    except _ChatTurnError as e:
        db.session.rollback()
        return jsonify({"error": e.message}), e.status

    church_id = current_user.church_id
    conversation_id = turn["conv_id"]
    call_usage: dict = {}
    # Captured now: the generator below runs after this request context has
    # been torn down, so it opens its own.
    app_obj = current_app._get_current_object()

    def events():
        with app_obj.app_context():
            pieces = []
            try:
                for piece in stream_gemini(
                    turn["question"], turn["context"], turn["history"],
                    turn["system_instruction"], usage=call_usage,
                ):
                    pieces.append(piece)
                    yield sse_event({"type": "delta", "text": piece})
            except Exception as e:
                db.session.rollback()
                message, _ = friendly_gemini_error(e)
                log.error("[CHAT] stream failed: %s", e)
                yield sse_event({"type": "error", "error": message})
                return

            answer = "".join(pieces)
            if not answer.strip():
                db.session.rollback()
                yield sse_event({"type": "error",
                                 "error": "No answer was returned. Please try again."})
                return

            try:
                sources = _save_chat_answer(turn, answer)
            except Exception:
                db.session.rollback()
                log.exception("[CHAT] stream DB commit failed")
                yield sse_event({"type": "done", "sources": [],
                                 "conversation_id": conversation_id, "saved": False})
                return

            record_usage(church_id, STAFF, call_usage)
            yield sse_event({"type": "done", "sources": sources,
                             "conversation_id": conversation_id, "saved": True})

    resp = current_app.response_class(events(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@chat_bp.route("/api/conversations")
@login_required
def list_conversations():
    convs = (
        Conversation.query
        .filter_by(church_id=current_user.church_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return jsonify({
        "conversations": [
            {"id": c.id, "title": c.title, "updated_at": iso_utc(c.updated_at)}
            for c in convs
        ]
    })


@chat_bp.route("/api/conversations/<int:conv_id>/messages")
@login_required
def get_conversation_messages(conv_id):
    conv = Conversation.query.filter_by(
        id=conv_id, church_id=current_user.church_id
    ).first()
    if not conv:
        return jsonify({"error": "Conversation not found."}), 404
    return jsonify({
        "conversation_id": conv.id,
        "title": conv.title,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "sources": json.loads(m.sources) if m.sources else [],
                "created_at": iso_utc(m.created_at),
            }
            for m in conv.messages
        ],
    })
