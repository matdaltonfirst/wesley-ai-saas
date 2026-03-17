"""Chat API routes: staff chat, conversations list, conversation messages."""

from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from models import db, Conversation, Message
from helpers import build_system_prompt, call_gemini, friendly_gemini_error
from documents import (
    load_church_documents, find_relevant_chunks, build_context_block,
)

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/api/chat", methods=["POST"])
@login_required
def chat():
    limiter = current_app.config.get("CHAT_LIMITER")
    if limiter and limiter.is_limited(str(current_user.church_id)):
        return jsonify({"error": "Too many requests. Please slow down and try again."}), 429

    data = request.get_json(silent=True)
    if not data or not data.get("question", "").strip():
        return jsonify({"error": "No question provided"}), 400

    question = data["question"].strip()
    if len(question) > 2000:
        return jsonify({"error": "Message is too long. Please keep questions under 2,000 characters."}), 400
    conversation_id = data.get("conversation_id")

    if conversation_id:
        conv = Conversation.query.filter_by(
            id=conversation_id, church_id=current_user.church_id
        ).first()
        if not conv:
            return jsonify({"error": "Conversation not found."}), 404
    else:
        title = question[:40]
        conv = Conversation(church_id=current_user.church_id, title=title)
        db.session.add(conv)
        db.session.flush()

    history = [
        {"role": m.role, "content": m.content}
        for m in conv.messages
    ]

    db.session.add(Message(conversation_id=conv.id, role="user", content=question))

    uploads_dir = current_app.config["UPLOADS_DIR"]
    chunks = load_church_documents(current_user.church_id, uploads_dir)
    context = ""
    candidate_sources = []

    if chunks:
        scored = find_relevant_chunks(question, chunks)
        if scored:
            context = build_context_block(scored)
            seen: set[tuple] = set()
            for _, chunk in scored:
                key = (chunk["source"], chunk["location"])
                if key not in seen:
                    seen.add(key)
                    candidate_sources.append({"file": chunk["source"], "location": chunk["location"]})

    system_instruction = build_system_prompt(current_user.church, widget=False)

    try:
        answer = call_gemini(question, context, history, system_instruction)
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        db.session.rollback()
        user_msg, status = friendly_gemini_error(e)
        return jsonify({"error": user_msg}), status

    db.session.add(Message(conversation_id=conv.id, role="assistant", content=answer))
    conv.updated_at = datetime.utcnow()
    db.session.commit()

    answer_lower = answer.lower()
    sources = [s for s in candidate_sources if s["file"].lower() in answer_lower]

    return jsonify({"answer": answer, "sources": sources, "conversation_id": conv.id})


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
            {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
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
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in conv.messages
        ],
    })
