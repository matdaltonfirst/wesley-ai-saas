"""Document API routes: list, upload, delete, patch visibility."""

import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, Document
from config import ALLOWED_EXTENSIONS
from documents import get_church_dir, evict_doc_cache

documents_bp = Blueprint("documents", __name__)

# Magic bytes that identify each supported file type.
# Checked against the raw upload bytes before anything is written to disk.
_MIME_SIGNATURES: dict[str, bytes] = {
    ".pdf":  b"%PDF",
    ".docx": b"PK\x03\x04",  # DOCX (and all Office Open XML formats) are ZIP files
}


def _valid_mime(content: bytes, suffix: str) -> bool:
    """Return True if *content* starts with the expected magic bytes for *suffix*."""
    sig = _MIME_SIGNATURES.get(suffix)
    return sig is not None and content[:len(sig)] == sig


@documents_bp.route("/api/documents")
@login_required
def list_documents():
    docs = (
        Document.query
        .filter_by(church_id=current_user.church_id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return jsonify({
        "documents": [
            {
                "id": d.id,
                "name": d.original_name,
                "size_kb": round(d.size_bytes / 1024, 1),
                "type": Path(d.original_name).suffix.lower().lstrip("."),
                "visibility": d.visibility,
            }
            for d in docs
        ]
    })


@documents_bp.route("/api/documents/upload", methods=["POST"])
@login_required
def upload_document():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    original_name = file.filename
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only PDF and DOCX files are supported."}), 400

    content = file.read()
    size_bytes = len(content)

    if not _valid_mime(content, suffix):
        return jsonify({"error": "File contents do not match the declared file type."}), 400

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    uploads_dir = current_app.config["UPLOADS_DIR"]
    church_dir = get_church_dir(current_user.church_id, uploads_dir)
    (church_dir / stored_name).write_bytes(content)

    display_name = secure_filename(original_name) or stored_name

    visibility = request.form.get("visibility", "staff_only")
    if visibility not in ("staff_only", "staff_and_chatbot"):
        visibility = "staff_only"

    doc = Document(
        church_id=current_user.church_id,
        filename=stored_name,
        original_name=display_name,
        size_bytes=size_bytes,
        visibility=visibility,
    )
    db.session.add(doc)
    db.session.commit()

    return jsonify({
        "ok": True,
        "id": doc.id,
        "name": doc.original_name,
        "size_kb": round(size_bytes / 1024, 1),
        "type": suffix.lstrip("."),
        "visibility": doc.visibility,
    }), 201


@documents_bp.route("/api/documents/<int:doc_id>", methods=["DELETE"])
@login_required
def delete_document(doc_id):
    doc = Document.query.filter_by(id=doc_id, church_id=current_user.church_id).first()
    if not doc:
        return jsonify({"error": "Document not found."}), 404

    uploads_dir = current_app.config["UPLOADS_DIR"]
    filepath = get_church_dir(current_user.church_id, uploads_dir) / doc.filename
    if filepath.exists():
        filepath.unlink()

    evict_doc_cache(doc.id, doc.uploaded_at)
    db.session.delete(doc)
    db.session.commit()
    return jsonify({"ok": True})


@documents_bp.route("/api/documents/<int:doc_id>", methods=["PATCH"])
@login_required
def update_document_visibility(doc_id):
    doc = Document.query.filter_by(id=doc_id, church_id=current_user.church_id).first()
    if not doc:
        return jsonify({"error": "Document not found."}), 404

    data = request.get_json(silent=True) or {}
    visibility = data.get("visibility")
    if visibility not in ("staff_only", "staff_and_chatbot"):
        return jsonify({"error": "Invalid visibility value."}), 400

    doc.visibility = visibility
    db.session.commit()
    return jsonify({"ok": True, "visibility": doc.visibility})
