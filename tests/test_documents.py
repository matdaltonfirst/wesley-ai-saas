"""Tests for document API routes: list, upload, delete, patch visibility."""

import io

import pytest
from models import db, Document


# Minimal valid file bytes for each supported type.
# The upload endpoint validates magic bytes, so these must be realistic.
_PDF_BYTES  = b"%PDF-1.4 fake content for testing"
_DOCX_BYTES = b"PK\x03\x04 fake docx zip content for testing"
_BAD_BYTES  = b"NOTAVALIDFILE plain text content"


def _upload(client, filename, content, visibility=None):
    """Helper: POST a file upload to /api/documents/upload."""
    data = {"file": (io.BytesIO(content), filename)}
    if visibility:
        data["visibility"] = visibility
    return client.post(
        "/api/documents/upload",
        data=data,
        content_type="multipart/form-data",
    )


# ── List documents ────────────────────────────────────────────────────────────

class TestListDocuments:
    def test_list_requires_auth(self, client):
        res = client.get("/api/documents")
        assert res.status_code == 401

    def test_list_empty(self, auth_client):
        res = auth_client.get("/api/documents")
        assert res.status_code == 200
        assert res.get_json()["documents"] == []

    def test_list_response_shape(self, auth_client, church):
        doc = Document(
            church_id=church.id,
            filename="abc123.pdf",
            original_name="bulletin.pdf",
            size_bytes=1024,
            visibility="staff_only",
        )
        db.session.add(doc)
        db.session.commit()

        res = auth_client.get("/api/documents")
        assert res.status_code == 200
        items = res.get_json()["documents"]
        assert len(items) == 1
        item = items[0]
        assert item["name"] == "bulletin.pdf"
        assert item["type"] == "pdf"
        assert "size_kb" in item
        assert item["visibility"] == "staff_only"

        db.session.delete(doc)
        db.session.commit()


# ── Upload documents ──────────────────────────────────────────────────────────

class TestUploadDocument:
    def test_upload_requires_auth(self, client):
        res = _upload(client, "test.pdf", _PDF_BYTES)
        assert res.status_code == 401

    def test_upload_no_file(self, auth_client):
        res = auth_client.post("/api/documents/upload", data={},
                               content_type="multipart/form-data")
        assert res.status_code == 400
        assert "no file" in res.get_json()["error"].lower()

    def test_upload_unsupported_extension(self, auth_client):
        res = _upload(auth_client, "notes.txt", b"plain text")
        assert res.status_code == 400
        assert "pdf" in res.get_json()["error"].lower()

    def test_upload_valid_pdf(self, auth_client, church):
        res = _upload(auth_client, "bulletin.pdf", _PDF_BYTES)
        assert res.status_code == 201
        data = res.get_json()
        assert data["ok"] is True
        assert data["type"] == "pdf"
        assert "id" in data

        doc = Document.query.get(data["id"])
        if doc:
            db.session.delete(doc)
            db.session.commit()

    def test_upload_valid_docx(self, auth_client, church):
        res = _upload(auth_client, "sermon_notes.docx", _DOCX_BYTES)
        assert res.status_code == 201
        data = res.get_json()
        assert data["ok"] is True
        assert data["type"] == "docx"

        doc = Document.query.get(data["id"])
        if doc:
            db.session.delete(doc)
            db.session.commit()

    def test_upload_pdf_with_wrong_mime_bytes(self, auth_client):
        """A .pdf file whose contents are not actually a PDF must be rejected."""
        res = _upload(auth_client, "evil.pdf", _BAD_BYTES)
        assert res.status_code == 400
        assert "file contents" in res.get_json()["error"].lower()

    def test_upload_docx_with_wrong_mime_bytes(self, auth_client):
        """A .docx file whose contents are not actually a ZIP/DOCX must be rejected."""
        res = _upload(auth_client, "evil.docx", _BAD_BYTES)
        assert res.status_code == 400
        assert "file contents" in res.get_json()["error"].lower()

    def test_upload_default_visibility_is_staff_only(self, auth_client, church):
        res = _upload(auth_client, "internal.pdf", _PDF_BYTES)
        assert res.status_code == 201
        assert res.get_json()["visibility"] == "staff_only"

        doc = Document.query.get(res.get_json()["id"])
        if doc:
            db.session.delete(doc)
            db.session.commit()

    def test_upload_explicit_visibility(self, auth_client, church):
        res = _upload(auth_client, "public.pdf", _PDF_BYTES, visibility="staff_and_chatbot")
        assert res.status_code == 201
        assert res.get_json()["visibility"] == "staff_and_chatbot"

        doc = Document.query.get(res.get_json()["id"])
        if doc:
            db.session.delete(doc)
            db.session.commit()


# ── Delete document ───────────────────────────────────────────────────────────

class TestDeleteDocument:
    def test_delete_requires_auth(self, client, church):
        doc = Document(
            church_id=church.id, filename="x.pdf",
            original_name="x.pdf", size_bytes=100,
        )
        db.session.add(doc)
        db.session.commit()

        res = client.delete(f"/api/documents/{doc.id}")
        assert res.status_code == 401

        db.session.delete(doc)
        db.session.commit()

    def test_delete_not_found(self, auth_client):
        res = auth_client.delete("/api/documents/999999")
        assert res.status_code == 404

    def test_delete_success(self, auth_client, church):
        # Upload a real file so delete has something to remove from disk too
        res = _upload(auth_client, "todelete.pdf", _PDF_BYTES)
        assert res.status_code == 201
        doc_id = res.get_json()["id"]

        del_res = auth_client.delete(f"/api/documents/{doc_id}")
        assert del_res.status_code == 200
        assert del_res.get_json()["ok"] is True

        # Should be gone from DB
        assert Document.query.get(doc_id) is None

    def test_delete_cannot_delete_other_churchs_doc(self, auth_client):
        """A document not belonging to the user's church returns 404, not 403."""
        from models import Church
        from datetime import timedelta
        from datetime import datetime
        other_church = Church(
            name="Other Church",
            trial_ends_at=datetime.utcnow() + timedelta(days=14),
        )
        db.session.add(other_church)
        db.session.flush()
        doc = Document(
            church_id=other_church.id, filename="other.pdf",
            original_name="other.pdf", size_bytes=100,
        )
        db.session.add(doc)
        db.session.commit()

        res = auth_client.delete(f"/api/documents/{doc.id}")
        assert res.status_code == 404

        db.session.delete(doc)
        db.session.delete(other_church)
        db.session.commit()


# ── Patch visibility ──────────────────────────────────────────────────────────

class TestPatchVisibility:
    def test_patch_requires_auth(self, client, church):
        doc = Document(
            church_id=church.id, filename="y.pdf",
            original_name="y.pdf", size_bytes=100,
        )
        db.session.add(doc)
        db.session.commit()

        res = client.patch(f"/api/documents/{doc.id}",
                           json={"visibility": "staff_and_chatbot"})
        assert res.status_code == 401

        db.session.delete(doc)
        db.session.commit()

    def test_patch_not_found(self, auth_client):
        res = auth_client.patch("/api/documents/999999",
                                json={"visibility": "staff_and_chatbot"})
        assert res.status_code == 404

    def test_patch_invalid_visibility(self, auth_client, church):
        doc = Document(
            church_id=church.id, filename="z.pdf",
            original_name="z.pdf", size_bytes=100,
        )
        db.session.add(doc)
        db.session.commit()

        res = auth_client.patch(f"/api/documents/{doc.id}",
                                json={"visibility": "public"})
        assert res.status_code == 400

        db.session.delete(doc)
        db.session.commit()

    def test_patch_valid_visibility(self, auth_client, church):
        doc = Document(
            church_id=church.id, filename="v.pdf",
            original_name="v.pdf", size_bytes=100,
            visibility="staff_only",
        )
        db.session.add(doc)
        db.session.commit()

        res = auth_client.patch(f"/api/documents/{doc.id}",
                                json={"visibility": "staff_and_chatbot"})
        assert res.status_code == 200
        assert res.get_json()["visibility"] == "staff_and_chatbot"

        db.session.refresh(doc)
        assert doc.visibility == "staff_and_chatbot"

        db.session.delete(doc)
        db.session.commit()
