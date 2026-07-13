from werkzeug.security import generate_password_hash
import pytest

from models import db, Document, KnowledgeChecklistState, KnowledgePackState, User


@pytest.fixture(autouse=True)
def clean_knowledge_rows():
    yield
    KnowledgeChecklistState.query.delete()
    KnowledgePackState.query.delete()
    Document.query.delete()
    db.session.commit()


def test_lists_builtin_packs(auth_client, church):
    res = auth_client.get("/api/knowledge-packs")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["packs"]) == 10
    assert {p["audience"] for p in data["packs"]} == {"guest", "staff"}
    assert data["can_manage"] is True


def test_admin_can_activate_pack(auth_client, church):
    res = auth_client.post("/api/knowledge-packs/visitor-essentials/activate", json={"active": True})
    assert res.status_code == 200
    state = KnowledgePackState.query.filter_by(church_id=church.id, pack_key="visitor-essentials").one()
    assert state.is_active is True


def test_guest_item_rejects_private_document(auth_client, church):
    doc = Document(church_id=church.id, filename="private.pdf", original_name="Private.pdf", size_bytes=10, visibility="staff_only")
    db.session.add(doc)
    db.session.commit()
    res = auth_client.post("/api/knowledge-checklist/visitor-service-times", json={
        "status": "linked", "source_type": "document", "source_id": doc.id,
    })
    assert res.status_code == 400
    assert "shared with the chatbot" in res.get_json()["error"]


def test_public_document_updates_readiness(auth_client, church):
    doc = Document(church_id=church.id, filename="public.pdf", original_name="Visitor Guide.pdf", size_bytes=10, visibility="staff_and_chatbot")
    db.session.add(doc)
    db.session.commit()
    auth_client.post("/api/knowledge-packs/visitor-essentials/activate", json={"active": True})
    res = auth_client.post("/api/knowledge-checklist/visitor-service-times", json={
        "status": "linked", "source_type": "document", "source_id": doc.id,
    })
    assert res.status_code == 200
    data = auth_client.get("/api/knowledge-packs").get_json()
    pack = next(p for p in data["packs"] if p["key"] == "visitor-essentials")
    assert pack["readiness"] == 20
    assert pack["items"][0]["source"]["label"] == "Visitor Guide.pdf"


def test_staff_cannot_modify_packs(auth_client, church):
    staff = User(email="knowledge-staff@example.org", password_hash=generate_password_hash("password", method="pbkdf2:sha256"), church_id=church.id, role="staff")
    db.session.add(staff)
    db.session.commit()
    with auth_client.session_transaction() as session:
        session["_user_id"] = str(staff.id)
        session["_fresh"] = True
    from flask import g
    g.pop("_login_user", None)
    assert auth_client.post("/api/knowledge-packs/visitor-essentials/activate", json={"active": True}).status_code == 403
    assert auth_client.post("/api/knowledge-checklist/visitor-service-times", json={"status": "not_applicable"}).status_code == 403
    assert auth_client.get("/api/knowledge-packs").get_json()["can_manage"] is False


def test_cannot_link_another_church_source(auth_client, church):
    from models import Church
    other = Church(name="Other Church")
    db.session.add(other)
    db.session.flush()
    doc = Document(church_id=other.id, filename="other.pdf", original_name="Other.pdf", size_bytes=10, visibility="staff_and_chatbot")
    db.session.add(doc)
    db.session.commit()
    res = auth_client.post("/api/knowledge-checklist/visitor-service-times", json={
        "status": "linked", "source_type": "document", "source_id": doc.id,
    })
    assert res.status_code == 400
    assert KnowledgeChecklistState.query.filter_by(church_id=church.id).count() == 0
