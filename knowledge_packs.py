"""Built-in knowledge packs and checklist API."""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from models import (
    db, ChurchCalendar, CrawledPage, Document, KnowledgeChecklistState,
    KnowledgePackState, QnAPair, TextSnippet,
)

knowledge_bp = Blueprint("knowledge", __name__)


def _item(key, title, source, questions):
    return {"key": key, "title": title, "suggested_source": source, "test_questions": questions}


PACKS = [
    {"key": "visitor-essentials", "name": "Visitor Essentials", "audience": "guest", "description": "The basics every first-time visitor needs.", "items": [
        _item("visitor-service-times", "Service times and worship styles", "Website page or Q&A", ["What time are your services?", "What is worship like?"]),
        _item("visitor-location", "Location, directions, and parking", "Website page", ["Where should I park?", "Which entrance should I use?"]),
        _item("visitor-expect", "What first-time guests can expect", "Website page or text snippet", ["What should I expect on Sunday?", "What should I wear?"]),
        _item("visitor-accessibility", "Accessibility and accommodations", "Text snippet or Q&A", ["Is the church wheelchair accessible?"]),
        _item("visitor-contact", "Main contact information", "Website page or text snippet", ["How can I contact the church office?"]),
    ]},
    {"key": "children-families", "name": "Children & Families", "audience": "guest", "description": "Help families arrive prepared and confident.", "items": [
        _item("family-nursery", "Nursery ages, hours, and location", "Website page or Q&A", ["Is nursery available?", "Where is the nursery?"]),
        _item("family-checkin", "Children's check-in and pickup", "Website page", ["How do I check in my children?"]),
        _item("family-programs", "Children and youth programs", "Website page or calendar", ["What programs do you have for children?", "Do you have youth group?"]),
        _item("family-special-needs", "Special needs accommodations", "Text snippet", ["Can you accommodate a child with special needs?"]),
    ]},
    {"key": "worship-sacraments", "name": "Worship & Sacraments", "audience": "guest", "description": "Explain worship practices and important milestones.", "items": [
        _item("worship-communion", "Communion practices", "Q&A or website page", ["Who can take communion?"]),
        _item("worship-baptism", "Baptism information", "Q&A or website page", ["How do I schedule a baptism?"]),
        _item("worship-weddings", "Wedding and funeral information", "Public document or website page", ["Can I have a wedding at the church?", "Who do I contact about a funeral?"]),
        _item("worship-seasonal", "Seasonal and special services", "Calendar", ["When is the Christmas Eve service?"]),
    ]},
    {"key": "groups-discipleship", "name": "Groups & Discipleship", "audience": "guest", "description": "Connect people with formation and community.", "items": [
        _item("groups-small", "Small groups and Sunday school", "Website page or calendar", ["What small groups can I join?"]),
        _item("groups-membership", "Membership and next steps", "Website page or Q&A", ["How do I become a member?"]),
        _item("groups-studies", "Current classes and Bible studies", "Calendar", ["Are there any Bible studies this week?"]),
    ]},
    {"key": "events-facilities", "name": "Events & Facilities", "audience": "guest", "description": "Keep event and building information dependable.", "items": [
        _item("events-calendar", "Current events and registration", "Calendar", ["What events are coming up?", "How do I register?"]),
        _item("events-facility", "Public facility-use information", "Public document or website page", ["Can I reserve a room?"]),
        _item("events-weather", "Weather closure policy", "Text snippet or Q&A", ["How will I know if church is canceled?"]),
    ]},
    {"key": "pastoral-care", "name": "Pastoral Care", "audience": "guest", "description": "Route care requests with clear expectations.", "items": [
        _item("care-prayer", "Prayer request options", "Website page or Q&A", ["How can I submit a prayer request?"]),
        _item("care-visits", "Pastoral and hospital visits", "Text snippet", ["Can a pastor visit someone in the hospital?"]),
        _item("care-support", "Counseling, grief, and support", "Website page or text snippet", ["Does the church offer counseling?"]),
        _item("care-emergency", "After-hours care guidance", "Text snippet", ["Who should I contact in an emergency?"]),
    ]},
    {"key": "serving-giving", "name": "Serving & Giving", "audience": "guest", "description": "Make participation and generosity easy.", "items": [
        _item("serve-opportunities", "Volunteer opportunities", "Website page", ["How can I volunteer?"]),
        _item("serve-requirements", "Volunteer requirements", "Public document or Q&A", ["Do volunteers need a background check?"]),
        _item("giving-methods", "Giving methods and help", "Website page or Q&A", ["How can I give online?"]),
    ]},
    {"key": "staff-operations", "name": "Staff Operations", "audience": "staff", "description": "Core policies and everyday staff procedures.", "items": [
        _item("staff-handbook", "Staff handbook", "Staff-only document", ["What is our leave policy?"]),
        _item("staff-expenses", "Purchasing and reimbursements", "Staff-only document", ["How do I submit an expense?"]),
        _item("staff-comms", "Communications and brand procedures", "Staff-only document", ["How do I request a graphic?"]),
        _item("staff-technology", "Technology instructions", "Staff-only document", ["How do I get technology support?"]),
    ]},
    {"key": "safety-emergency", "name": "Safety & Emergency", "audience": "staff", "description": "Give authorized staff dependable response procedures.", "items": [
        _item("safety-protection", "Child and vulnerable-adult protection", "Staff-only document", ["What is our Safe Sanctuary procedure?"]),
        _item("safety-incident", "Incident reporting", "Staff-only document", ["How do I report an incident?"]),
        _item("safety-evacuation", "Evacuation and severe weather", "Staff-only document", ["Where do we go during severe weather?"]),
        _item("safety-security", "Building security and medical response", "Staff-only document", ["What is the medical emergency procedure?"]),
    ]},
    {"key": "governance-admin", "name": "Governance & Administration", "audience": "staff", "description": "Organize leadership, policy, and planning references.", "items": [
        _item("gov-structure", "Committees and leadership responsibilities", "Staff-only document", ["Which committee handles this decision?"]),
        _item("gov-denomination", "Denominational and church policies", "Staff-only document", ["What policy governs this issue?"]),
        _item("gov-meetings", "Approved meeting records", "Staff-only document", ["What was decided at the last meeting?"]),
        _item("gov-planning", "Annual calendars and planning documents", "Staff-only document", ["What are this year's major deadlines?"]),
    ]},
]

PACK_BY_KEY = {pack["key"]: pack for pack in PACKS}
ITEMS = {item["key"]: (pack, item) for pack in PACKS for item in pack["items"]}
VALID_STATUSES = {"missing", "linked", "needs_review", "not_applicable"}
SOURCE_MODELS = {"document": Document, "page": CrawledPage, "snippet": TextSnippet, "qna": QnAPair, "calendar": ChurchCalendar}


def _source_options(church_id):
    options = []
    for doc in Document.query.filter_by(church_id=church_id).order_by(Document.original_name).all():
        options.append({"type": "document", "id": doc.id, "label": doc.original_name, "audience": "guest" if doc.visibility == "staff_and_chatbot" else "staff"})
    for page in CrawledPage.query.filter_by(church_id=church_id).order_by(CrawledPage.title).all():
        options.append({"type": "page", "id": page.id, "label": page.title or page.url, "audience": "guest"})
    for snippet in TextSnippet.query.filter_by(church_id=church_id, is_active=True).order_by(TextSnippet.title).all():
        options.append({"type": "snippet", "id": snippet.id, "label": snippet.title, "audience": "guest"})
    for pair in QnAPair.query.filter_by(church_id=church_id, is_active=True).order_by(QnAPair.question).all():
        options.append({"type": "qna", "id": pair.id, "label": pair.question, "audience": "guest"})
    for cal in ChurchCalendar.query.filter_by(church_id=church_id).order_by(ChurchCalendar.label).all():
        options.append({"type": "calendar", "id": cal.id, "label": cal.label, "audience": "guest"})
    return options


def _state_map(church_id):
    return {row.item_key: row for row in KnowledgeChecklistState.query.filter_by(church_id=church_id).all()}


@knowledge_bp.route("/api/knowledge-packs")
@login_required
def list_knowledge_packs():
    active = {row.pack_key for row in KnowledgePackState.query.filter_by(church_id=current_user.church_id, is_active=True).all()}
    states = _state_map(current_user.church_id)
    options = _source_options(current_user.church_id)
    option_map = {(o["type"], o["id"]): o for o in options}
    packs = []
    for definition in PACKS:
        items = []
        for item in definition["items"]:
            state = states.get(item["key"])
            source = option_map.get((state.source_type, state.source_id)) if state and state.source_id else None
            status = state.status if state else "missing"
            if status == "linked" and not source:
                status = "needs_review"
            items.append({**item, "status": status, "source": source})
        complete = sum(i["status"] in ("linked", "not_applicable") for i in items)
        packs.append({**definition, "active": definition["key"] in active, "readiness": round(100 * complete / len(items)), "items": items})
    return jsonify({"packs": packs, "sources": options, "can_manage": current_user.role == "admin"})


@knowledge_bp.route("/api/knowledge-packs/<pack_key>/activate", methods=["POST"])
@login_required
def activate_pack(pack_key):
    if current_user.role != "admin":
        return jsonify({"error": "Only church admins can manage knowledge packs."}), 403
    if pack_key not in PACK_BY_KEY:
        return jsonify({"error": "Knowledge pack not found."}), 404
    row = KnowledgePackState.query.filter_by(church_id=current_user.church_id, pack_key=pack_key).first()
    if not row:
        row = KnowledgePackState(church_id=current_user.church_id, pack_key=pack_key)
        db.session.add(row)
    row.is_active = bool((request.get_json(silent=True) or {}).get("active", True))
    db.session.commit()
    return jsonify({"ok": True})


@knowledge_bp.route("/api/knowledge-checklist/<item_key>", methods=["POST"])
@login_required
def update_checklist_item(item_key):
    if current_user.role != "admin":
        return jsonify({"error": "Only church admins can manage the knowledge checklist."}), 403
    if item_key not in ITEMS:
        return jsonify({"error": "Checklist item not found."}), 404
    data = request.get_json(silent=True) or {}
    status = data.get("status", "missing")
    if status not in VALID_STATUSES:
        return jsonify({"error": "Invalid checklist status."}), 400
    source_type = data.get("source_type") or None
    source_id = data.get("source_id")
    if status == "linked":
        model = SOURCE_MODELS.get(source_type)
        source = model.query.filter_by(id=source_id, church_id=current_user.church_id).first() if model and source_id else None
        if not source:
            return jsonify({"error": "Select a valid knowledge source."}), 400
        pack, _ = ITEMS[item_key]
        if pack["audience"] == "guest" and source_type == "document" and source.visibility != "staff_and_chatbot":
            return jsonify({"error": "Guest knowledge must use a document shared with the chatbot."}), 400
    else:
        source_type, source_id = None, None
    row = KnowledgeChecklistState.query.filter_by(church_id=current_user.church_id, item_key=item_key).first()
    if not row:
        row = KnowledgeChecklistState(church_id=current_user.church_id, item_key=item_key)
        db.session.add(row)
    row.status, row.source_type, row.source_id = status, source_type, source_id
    db.session.commit()
    return jsonify({"ok": True})
