"""The public website chatbot API: no login, called from daltonfumc.com.

This module and ``public_knowledge`` are the whole public path. They may import
only what ``tests/test_public_boundary.py`` allows; the test parses this file and
fails if a staff data source, staff loader or user model is ever imported here.

The model is called with no tools, and the only data it receives is what
``public_knowledge.build_public_context`` selects from public content.
"""

import json
import logging
import re
import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, make_response, request, send_from_directory

import guest_intake
import public_limits
from gemini_client import call_gemini, friendly_gemini_error, sse_event as _sse, stream_gemini
from helpers import build_branding_dict
from documents import select_cited_sources
from models import AnswerFeedback, WidgetConversation, WidgetMessage, db
from organization import public_widget_ids
from public_knowledge import build_public_context, build_public_prompt
from usage import WIDGET, record_usage

log = logging.getLogger("wesley")

public_bp = Blueprint("public", __name__)

_BUSY = ("The chat is resting for today. Please contact the church office, "
         "or try again tomorrow.")


# ── Widget script ────────────────────────────────────────────────────────────

def _widget_js_response():
    """Serve widget-core.js with appropriate headers for public embedding."""
    resp = make_response(send_from_directory("static", "widget-core.js"))
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Access-Control-Allow-Origin"] = "*"      # a script file, not data
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@public_bp.route("/widget.js")
def serve_widget():
    return _widget_js_response()


@public_bp.route("/widget-core.js")
def serve_widget_core():
    return _widget_js_response()


# ── CORS and limits ──────────────────────────────────────────────────────────

def _cors(resp):
    origin = public_limits.cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
    return resp


def _refused_origin():
    """A 403 with no CORS header when a browser from another site calls us."""
    if public_limits.cors_origin() is None:
        return jsonify({"error": "This chat is only available on the church website."}), 403
    return None


def _preflight(methods):
    refused = _refused_origin()
    if refused:
        return refused
    resp = make_response("", 204)
    resp.headers["Access-Control-Allow-Methods"] = methods
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return _cors(resp)


def _visitor() -> str:
    return request.remote_addr or "unknown"


def _bad_public_church_id(raw) -> bool:
    """True when a public request names a church id that is not ours.

    The live website embed still sends the id it was installed with, so it is
    accepted; an absent id is also fine. Anything else is rejected.
    """
    if raw in (None, ""):
        return False
    try:
        return int(raw) not in public_widget_ids()
    except (ValueError, TypeError):
        return True


# ── Branding ─────────────────────────────────────────────────────────────────

@public_bp.route("/api/widget/branding", methods=["GET", "OPTIONS"])
def widget_branding():
    """Church branding for the embedded widget."""
    if request.method == "OPTIONS":
        return _preflight("GET, OPTIONS")
    refused = _refused_origin()
    if refused:
        return refused

    limiter = current_app.config["WIDGET_BRANDING_LIMITER"]
    if limiter.is_limited(_visitor()):
        return _cors(jsonify({"error": "Rate limit exceeded. Please try again later."})), 429
    if _bad_public_church_id(request.args.get("church_id", "").strip()):
        return _cors(jsonify({"error": "Church not found"})), 404

    resp = _cors(jsonify(build_branding_dict()))
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


# ── Chat ─────────────────────────────────────────────────────────────────────

class _WidgetTurnError(Exception):
    """A validated rejection, carrying the message and status to return."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _prepare_widget_turn(data):
    """Validate a request and build everything the model call needs.

    Shared by the blocking and streaming endpoints so the two cannot diverge on
    validation, retrieval or the assembled prompt.
    """
    question = (data.get("question") or "").strip()
    session_id = (data.get("session_id") or "").strip() or None

    if not question:
        raise _WidgetTurnError("question is required.")
    if len(question) > 2000:
        raise _WidgetTurnError("Message is too long. Please keep questions under 2,000 characters.")
    if session_id and len(session_id) > 64:
        raise _WidgetTurnError("Invalid session_id.")
    if _bad_public_church_id(data.get("church_id")):
        raise _WidgetTurnError("Church not found.", 404)

    wconv = None
    if session_id:
        wconv = WidgetConversation.query.filter_by(session_id=session_id).first()
    if not wconv:
        session_id = uuid.uuid4().hex
        wconv = WidgetConversation(session_id=session_id)
        db.session.add(wconv)
        db.session.flush()

    history = [{"role": m.role, "content": m.content} for m in wconv.messages]
    db.session.add(WidgetMessage(widget_conversation_id=wconv.id, role="user", content=question))
    # Committed here: the streaming generator runs after this request context is
    # gone, so nothing may be left pending in a session it cannot reach.
    db.session.commit()

    context, candidate_sources = build_public_context(question, current_app.config["UPLOADS_DIR"])
    return {
        "question": question,
        "session_id": session_id,
        "wconv_id": wconv.id,
        "history": history,
        "context": context,
        "candidate_sources": candidate_sources,
        "system_instruction": build_public_prompt(),
    }


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


def _save_widget_answer(turn, answer):
    """Persist the assistant turn and return its citation list and message id.

    Re-queries by id so this works from inside the streaming generator, which no
    longer shares the request's session.
    """
    wconv = WidgetConversation.query.get(turn["wconv_id"])
    sources = select_cited_sources(answer, turn["candidate_sources"])
    assistant_message = WidgetMessage(
        widget_conversation_id=turn["wconv_id"], role="assistant", content=answer,
        sources=json.dumps(sources) if sources else None,
    )
    db.session.add(assistant_message)
    if wconv:
        wconv.updated_at = datetime.utcnow()
    if _is_low_confidence(answer):
        # Surface unanswerable questions in the Feedback & Corrections inbox
        # even when the visitor never rates the answer.
        db.session.flush()
        db.session.add(AnswerFeedback(
            widget_message_id=assistant_message.id, rating="auto_flagged", status="open",
        ))
    db.session.commit()
    return sources, assistant_message.id


def _gate_chat():
    """Origin, per-visitor limit and daily cap. A response to return, or None."""
    refused = _refused_origin()
    if refused:
        return refused
    if not public_limits.allowed(_visitor()):
        return _cors(jsonify({"error": "Rate limit exceeded. Please try again later."})), 429
    if not public_limits.under_daily_cap():
        return _cors(jsonify({"error": _BUSY})), 503
    return None


@public_bp.route("/api/widget/chat/stream", methods=["POST", "OPTIONS"])
def widget_chat_stream():
    """Server-sent events version of the widget chat."""
    if request.method == "OPTIONS":
        return _preflight("POST, OPTIONS")
    gated = _gate_chat()
    if gated:
        return gated

    try:
        turn = _prepare_widget_turn(request.get_json(silent=True) or {})
    except _WidgetTurnError as e:
        db.session.rollback()
        return _cors(jsonify({"error": e.message})), e.status

    call_usage: dict = {}
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
                    yield _sse({"type": "delta", "text": piece})
            except Exception as e:
                db.session.rollback()
                message, _ = friendly_gemini_error(e)
                log.error("[WIDGET] stream failed: %s", e)
                yield _sse({"type": "error", "error": message})
                return

            answer = "".join(pieces)
            if not answer.strip():
                db.session.rollback()
                yield _sse({"type": "error", "error": "No answer was returned. Please try again."})
                return

            try:
                sources, message_id = _save_widget_answer(turn, answer)
            except Exception as e:
                db.session.rollback()
                log.error("[WIDGET] stream DB commit failed: %s", e)
                yield _sse({"type": "done", "sources": [], "session_id": turn["session_id"],
                            "message_id": None, "saved": False})
                return

            record_usage(WIDGET, call_usage)
            yield _sse({"type": "done", "sources": sources, "session_id": turn["session_id"],
                        "message_id": message_id, "saved": True})

    resp = current_app.response_class(events(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return _cors(resp)


@public_bp.route("/api/widget/chat", methods=["POST", "OPTIONS"])
def widget_chat():
    """Blocking widget chat, kept for older cached copies of widget.js."""
    if request.method == "OPTIONS":
        return _preflight("POST, OPTIONS")
    gated = _gate_chat()
    if gated:
        return gated

    try:
        turn = _prepare_widget_turn(request.get_json(silent=True) or {})
    except _WidgetTurnError as e:
        db.session.rollback()
        return _cors(jsonify({"error": e.message})), e.status

    call_usage: dict = {}
    try:
        answer = call_gemini(
            turn["question"], turn["context"], turn["history"],
            turn["system_instruction"], usage=call_usage,
        )
    except ValueError as e:
        db.session.rollback()
        return _cors(jsonify({"error": str(e)})), 500
    except Exception as e:
        db.session.rollback()
        user_msg, status = friendly_gemini_error(e)
        return _cors(jsonify({"error": user_msg})), status

    try:
        sources, message_id = _save_widget_answer(turn, answer)
    except Exception as e:
        db.session.rollback()
        log.error("[WIDGET] DB commit failed: %s", e)
        return _cors(jsonify({"error": "Failed to save conversation. Please try again."})), 500

    record_usage(WIDGET, call_usage)
    return _cors(jsonify({
        "answer": answer, "sources": sources,
        "session_id": turn["session_id"], "message_id": message_id,
    }))


# ── Visitor feedback ─────────────────────────────────────────────────────────

_FEEDBACK_REASONS = {"incorrect", "outdated", "incomplete", "confusing", "other"}


def _feedback_error(message, status=400):
    return _cors(jsonify({"error": message})), status


@public_bp.route("/api/widget/feedback", methods=["POST", "OPTIONS"])
def submit_answer_feedback():
    if request.method == "OPTIONS":
        return _preflight("POST, OPTIONS")
    refused = _refused_origin()
    if refused:
        return refused

    data = request.get_json(silent=True) or {}
    rating = (data.get("rating") or "").strip()
    reason = (data.get("reason") or "").strip() or None
    comment = (data.get("comment") or "").strip() or None
    session_id = (data.get("session_id") or "").strip()

    try:
        message_id = int(data.get("message_id"))
    except (TypeError, ValueError):
        return _feedback_error("Invalid feedback target.")
    if _bad_public_church_id(data.get("church_id")):
        return _feedback_error("Invalid feedback target.")
    if rating not in ("helpful", "not_helpful"):
        return _feedback_error("Invalid rating.")
    if reason and reason not in _FEEDBACK_REASONS:
        return _feedback_error("Invalid feedback reason.")
    if len(comment or "") > 1000:
        return _feedback_error("Feedback must be 1,000 characters or fewer.")

    message = (
        WidgetMessage.query.join(WidgetConversation)
        .filter(
            WidgetMessage.id == message_id,
            WidgetMessage.role == "assistant",
            WidgetConversation.session_id == session_id,
        )
        .first()
    )
    if not message:
        return _feedback_error("Answer not found.", 404)

    feedback = AnswerFeedback.query.filter_by(widget_message_id=message.id).first()
    if not feedback:
        feedback = AnswerFeedback(widget_message_id=message.id)
        db.session.add(feedback)
    feedback.rating = rating
    feedback.reason = reason if rating == "not_helpful" else None
    feedback.comment = comment if rating == "not_helpful" else None
    feedback.status = "open" if rating == "not_helpful" else "dismissed"
    feedback.resolved_at = datetime.utcnow() if rating == "helpful" else None
    db.session.commit()
    return _cors(jsonify({"ok": True})), 201


# ── Guest connections ────────────────────────────────────────────────────────

@public_bp.route("/api/guest-connection", methods=["POST", "OPTIONS"])
def create_guest_connection():
    """A visitor asks the church to get in touch."""
    if request.method == "OPTIONS":
        return _preflight("POST, OPTIONS")
    refused = _refused_origin()
    if refused:
        return refused

    def err(msg, status=400):
        return _cors(jsonify({"error": msg})), status

    # Unauthenticated, and it writes to the church's records, emails staff and
    # pushes a person into Planning Center, so it gets a tight budget.
    if not public_limits.allowed(_visitor(), public_limits.GUEST_LIMITS, scope="guest"):
        return err("Too many submissions. Please try again later.", 429)

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    interest = (data.get("interest_area") or "General Interest").strip()
    opening = (data.get("opening_message") or "").strip()

    if not name or not email:
        return err("name and email are required.")
    if _bad_public_church_id(data.get("church_id")):
        return err("Church not found.", 404)

    try:
        guest_intake.record_guest(name, email, phone, interest, opening)
    except Exception as e:
        log.error("[GUEST] save failed: %s", e)
        return err("Failed to save. Please try again.", 500)
    return _cors(jsonify({"ok": True})), 201
