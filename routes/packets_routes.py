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
EDITABLE = {"titles", "description", "social", "blog", "guide"}


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
        elif field == "blog":
            # Staff edit the article before it goes on the website, so the body
            # is stored as given. The verbatim filter ran at generation; a human
            # editing their own church's post is not the risk it guards against.
            entry = value if isinstance(value, dict) else {}
            body = str(entry.get("body") or "").strip()
            if not body:
                continue
            content["blog"] = {
                "title": str(entry.get("title") or "").strip()[:300],
                "body": body[:40000],
                "words": len(body.split()),
            }
        elif field == "guide":
            entry = value if isinstance(value, dict) else {}
            existing = content.get("guide") or {}
            content["guide"] = {
                "scripture":  str(entry.get("scripture", existing.get("scripture", "")))[:120],
                "summary":    str(entry.get("summary", existing.get("summary", "")))[:800],
                "opening":    str(entry.get("opening", existing.get("opening", "")))[:400],
                "digging_in": [str(q)[:400] for q in (entry.get("digging_in") or []) if str(q).strip()][:6],
                "applying":   [str(q)[:400] for q in (entry.get("applying") or []) if str(q).strip()][:6],
                "prayer":     str(entry.get("prayer", existing.get("prayer", "")))[:500],
                "challenge":  str(entry.get("challenge", existing.get("challenge", "")))[:400],
            }
        changed = True

    if not changed:
        return jsonify({"error": "Nothing to update."}), 400

    packet.content = json.dumps(content)
    db.session.commit()
    sermon = Sermon.query.get(packet.sermon_id)
    return jsonify({"ok": True, "packet": _packet_dict(packet, sermon)})


@packets_bp.route("/api/packets/pending")
@login_required
def pending_sermons():
    """Sermons with a transcript that have no content built yet.

    Surfaced so an empty Sunday Content panel can say which messages are waiting
    rather than leaving staff to guess whether the feature is broken.
    """
    from packets import sermons_awaiting_content

    sermons = sermons_awaiting_content(current_user.church_id)
    return jsonify({"sermons": [{
        "id": s.id,
        "title": s.title,
        "series": s.series or "",
        "preached_at": iso_utc(s.published_at),
        "video_url": s.video_url,
    } for s in sermons[:25]]})


@packets_bp.route("/api/packets/generate", methods=["POST"])
@login_required
def generate_for_sermon():
    """Build content for one sermon on demand.

    The weekly job is the normal path, but it can only ever look at a window.
    Anything it missed — a transcript that arrived late, a run that failed —
    was previously unrecoverable, because the only other entry point was
    regenerating a packet that did not exist. This is that missing door.
    """
    err, status = validate_csrf_json()
    if err:
        return err, status

    data = request.get_json(silent=True) or {}
    sermon_id = data.get("sermon_id")
    sermon = Sermon.query.filter_by(
        id=sermon_id, church_id=current_user.church_id
    ).first()
    if not sermon:
        return jsonify({"error": "Sermon not found."}), 404
    if not sermon.transcript:
        return jsonify({"error": "That sermon has no transcript to work from."}), 400

    existing = SermonPacket.query.filter_by(sermon_id=sermon.id).first()
    if existing and existing.status == "ready":
        # Never silently overwrite content staff may have edited; regenerate is
        # the explicit door for that.
        return jsonify({"error": "This message already has content.",
                        "packet_id": existing.id}), 409

    from packets import generate_packet

    packet = generate_packet(sermon)
    if packet.status != "ready":
        return jsonify({"error": packet.error or "Could not build content."}), 502
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

    from sermon_longform import build_longform
    from sermon_packet import build_packet
    try:
        content = build_packet(sermon, current_user.church)
        content.update(build_longform(sermon, current_user.church))
    except Exception as exc:
        log.error("Packet regenerate failed for packet_id=%s: %s", packet_id, exc)
        return jsonify({"error": "Could not rebuild this packet. Please try again."}), 502

    packet.content = json.dumps(content)
    packet.status = "ready"
    packet.error = None
    packet.generated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "packet": _packet_dict(packet, sermon)})
