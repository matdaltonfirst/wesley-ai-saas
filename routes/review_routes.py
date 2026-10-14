"""The weekly sermon content review queue: the page and its API.

Reading needs sunday.read. Editing and regenerating need sunday.write. Approving needs
sunday.approve. Nothing here publishes or sends anything.
"""

import json

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

import review
from audit import log_event
from helpers import iso_utc
from models import ContentReview, ContentVersion, Sermon, SermonPacket, User, db
from organization import get_org
from permissions import can, require

review_bp = Blueprint("review", __name__)


def _who(user_id) -> str:
    u = User.query.get(user_id) if user_id else None
    return (u.display_name or u.email) if u else ""


def _piece_dict(packet, piece, row, content) -> dict:
    fields = review.piece_fields(content, piece)
    guide = fields.get("guide")
    if isinstance(guide, dict) and not guide.get("document"):        # guides made before the single-document editor
        from sermon_longform import guide_document
        guide = dict(guide, document=guide_document(guide)); fields["guide"] = guide
    return {
        "piece": piece, "label": review.PIECES[piece][0], "status": row.status, "note": row.note or "",
        "decided_by": _who(row.decided_by_id), "decided_at": iso_utc(row.decided_at),
        "editable": piece in ("youtube", "social", "blog", "guide"),
        "fields": fields,
        "versions": ContentVersion.query.filter_by(packet_id=packet.id, piece=piece).count(),
        "error": content.get(f"{piece}_error") or "",
    }


@review_bp.route("/review")
@require("sunday.read")
def review_page():
    return render_template("review.html", church_name=get_org().name, user_email=current_user.email,
                           can_write=can("sunday.write"), can_approve=can("sunday.approve"))


@review_bp.route("/api/review")
@require("sunday.read")
def review_queue():
    out = []
    for q in review.queue():
        packet, sermon = q["packet"], q["sermon"]
        out.append({
            "packet_id": packet.id, "status": q["status"],
            "sermon": {"id": sermon.id, "title": sermon.title, "series": sermon.series or "",
                       "preached_at": iso_utc(sermon.published_at), "video_url": sermon.video_url},
            "pieces": {p: r.status for p, r in q["rows"].items()},
        })
    return jsonify({"items": out, "labels": {k: v[0] for k, v in review.PIECES.items()}})


@review_bp.route("/api/review/<int:packet_id>")
@require("sunday.read")
def review_detail(packet_id):
    packet = SermonPacket.query.get(packet_id)
    if not packet:
        return jsonify({"error": "Not found."}), 404
    sermon = Sermon.query.get(packet.sermon_id)
    rows = review.ensure_reviews(packet)
    content = review.load_content(packet)
    return jsonify({
        "packet_id": packet.id, "status": review.overall_status(rows),
        "sermon": {"id": sermon.id if sermon else None, "title": sermon.title if sermon else "",
                   "series": (sermon.series or "") if sermon else "", "preached_at": iso_utc(sermon.published_at) if sermon else None,
                   "video_url": sermon.video_url if sermon else None},
        "pieces": [_piece_dict(packet, p, r, content) for p, r in rows.items()],
        "notice": "Nothing here is published or sent. Approving records that a person reviewed the words.",
    })


@review_bp.route("/api/review/<int:packet_id>/<piece>/decision", methods=["POST"])
@require("sunday.read")
def decide(packet_id, piece):
    packet = SermonPacket.query.get(packet_id)
    if not packet:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status == "approved" and not can("sunday.approve"):
        return jsonify({"error": "Approving needs the approve permission."}), 403
    if status == "needs_changes" and not (can("sunday.approve") or can("sunday.write")):
        return jsonify({"error": "You do not have permission to do that."}), 403
    if status == "draft" and not can("sunday.write"):
        return jsonify({"error": "You do not have permission to do that."}), 403
    try:
        row = review.decide(packet, piece, status, current_user, data.get("note", ""))
    except review.ReviewError as exc:
        return jsonify({"error": str(exc)}), 400
    log_event(f"review.{status}", source=f"packet:{packet_id}", detail={"piece": piece})
    return jsonify({"ok": True, "status": row.status})


@review_bp.route("/api/review/<int:packet_id>/<piece>/regenerate", methods=["POST"])
@require("sunday.write")
def regenerate(packet_id, piece):
    packet = SermonPacket.query.get(packet_id)
    if not packet:
        return jsonify({"error": "Not found."}), 404
    try:
        fields = review.regenerate_piece(packet, piece, current_user)
    except review.ReviewError as exc:
        return jsonify({"error": str(exc)}), 502
    log_event("review.regenerate", source=f"packet:{packet_id}", detail={"piece": piece})
    return jsonify({"ok": True, "fields": fields})


@review_bp.route("/api/review/<int:packet_id>/<piece>/versions")
@require("sunday.read")
def versions(packet_id, piece):
    rows = (ContentVersion.query.filter_by(packet_id=packet_id, piece=piece).order_by(ContentVersion.id.desc()).all())
    return jsonify({"versions": [{"id": v.id, "reason": v.reason, "at": iso_utc(v.created_at), "by": _who(v.created_by_id),
                                  "fields": json.loads(v.content)} for v in rows]})


@review_bp.route("/api/review/versions/<int:version_id>/restore", methods=["POST"])
@require("sunday.write")
def restore(version_id):
    version = ContentVersion.query.get(version_id)
    packet = SermonPacket.query.get(version.packet_id) if version else None
    if not packet:
        return jsonify({"error": "Not found."}), 404
    fields = review.restore_version(version, packet, current_user)
    log_event("review.restore", source=f"packet:{packet.id}", detail={"piece": version.piece})
    return jsonify({"ok": True, "fields": fields})
