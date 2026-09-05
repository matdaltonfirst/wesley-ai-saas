"""Sermon packet routes — the Monday packet in the dashboard.

Staff read the packet here, edit it, and mark it done. Editing writes back to
the stored content so the email and the dashboard never disagree, and so a
correction survives to the next time someone opens it.
"""

import json
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from helpers import iso_utc, validate_csrf_json
from models import db, Sermon, SermonPacket

log = logging.getLogger("wesley")

packets_bp = Blueprint("packets", __name__)

# Fields staff may edit. Quotes and chapters are deliberately absent: their
# value is that they are provably what was preached and provably where, and an
# edited "quote" is no longer a quote. Staff who want different words can write
# a post instead.
EDITABLE = {"titles", "description", "social"}


def _packet_dict(packet, sermon) -> dict:
    try:
        content = json.loads(packet.content) if packet.content else {}
    except (ValueError, TypeError):
        content = {}
    return {
        "id": packet.id,
        "status": packet.status,
        "error": packet.error or "",
        "generated_at": iso_utc(packet.generated_at),
        "emailed_at": iso_utc(packet.emailed_at),
        "sermon": {
            "id": sermon.id if sermon else None,
            "title": sermon.title if sermon else "",
            "series": (sermon.series or "") if sermon else "",
            "preached_at": iso_utc(sermon.published_at) if sermon else None,
            "video_url": sermon.video_url if sermon else None,
        },
        "content": content,
    }


@packets_bp.route("/api/packets")
@login_required
def list_packets():
    rows = (
        db.session.query(SermonPacket, Sermon)
        .outerjoin(Sermon, Sermon.id == SermonPacket.sermon_id)
        .filter(SermonPacket.church_id == current_user.church_id)
        .order_by(SermonPacket.created_at.desc())
        .limit(50)
        .all()
    )
    return jsonify({"packets": [_packet_dict(p, s) for p, s in rows]})


@packets_bp.route("/api/packets/<int:packet_id>")
@login_required
def get_packet(packet_id):
    packet = SermonPacket.query.filter_by(
        id=packet_id, church_id=current_user.church_id
    ).first()
    if not packet:
        return jsonify({"error": "Packet not found."}), 404
    sermon = Sermon.query.get(packet.sermon_id)
    return jsonify(_packet_dict(packet, sermon))


@packets_bp.route("/api/packets/<int:packet_id>", methods=["PATCH"])
@login_required
def update_packet(packet_id):
    err, status = validate_csrf_json()
    if err:
        return err, status

    packet = SermonPacket.query.filter_by(
        id=packet_id, church_id=current_user.church_id
    ).first()
    if not packet:
        return jsonify({"error": "Packet not found."}), 404

    data = request.get_json(silent=True) or {}
    try:
        content = json.loads(packet.content) if packet.content else {}
    except (ValueError, TypeError):
        content = {}

    changed = False
    for field in EDITABLE:
        if field not in data:
            continue
        value = data[field]
        if field == "description":
            content["description"] = str(value)[:5000]
        elif field == "titles":
            content["titles"] = [str(t)[:200] for t in value if str(t).strip()][:5]
        elif field == "social":
            posts = []
            for entry in value:
                if not isinstance(entry, dict):
                    continue
                body = str(entry.get("body") or "").strip()
                if not body:
                    continue
                posts.append({
                    "platform": str(entry.get("platform") or "facebook")[:30],
                    "body": body[:5000],
                })
            content["social"] = posts
        changed = True

    if not changed:
        return jsonify({"error": "Nothing to update."}), 400

    packet.content = json.dumps(content)
    db.session.commit()
    sermon = Sermon.query.get(packet.sermon_id)
    return jsonify({"ok": True, "packet": _packet_dict(packet, sermon)})


@packets_bp.route("/api/packets/<int:packet_id>/regenerate", methods=["POST"])
@login_required
def regenerate_packet(packet_id):
    """Rebuild a packet from its sermon, discarding edits.

    Kept explicit rather than automatic: regenerating silently would throw away
    staff edits, and the nightly job deliberately never touches a sermon twice.
    """
    err, status = validate_csrf_json()
    if err:
        return err, status

    packet = SermonPacket.query.filter_by(
        id=packet_id, church_id=current_user.church_id
    ).first()
    if not packet:
        return jsonify({"error": "Packet not found."}), 404

    sermon = Sermon.query.get(packet.sermon_id)
    if not sermon or not sermon.transcript:
        return jsonify({"error": "That sermon has no transcript to work from."}), 400

    from sermon_packet import build_packet
    try:
        content = build_packet(sermon, current_user.church)
    except Exception as exc:
        log.error("Packet regenerate failed for packet_id=%s: %s", packet_id, exc)
        return jsonify({"error": "Could not rebuild this packet. Please try again."}), 502

    packet.content = json.dumps(content)
    packet.status = "ready"
    packet.error = None
    packet.generated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "packet": _packet_dict(packet, sermon)})
