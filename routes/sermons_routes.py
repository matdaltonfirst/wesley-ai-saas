"""Sermon ingestion routes: connect a channel, list sermons, retry, manual paste."""

import logging
import threading

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

import sermons as sermon_lib
from models import db, SermonSource, Sermon
from sermons import SermonError
from helpers import iso_utc

log = logging.getLogger("wesley")

sermons_bp = Blueprint("sermons", __name__)


def _sermon_dict(s):
    return {
        "id": s.id,
        "title": s.title,
        "published_at": iso_utc(s.published_at),
        "video_url": s.video_url,
        "status": s.status,
        "error": s.error or "",
        "series": s.series or "",
        "scriptures": s.scriptures or "",
        "summary": s.summary or "",
        "has_transcript": bool(s.transcript),
    }


@sermons_bp.route("/api/sermons/status")
@login_required
def sermons_status():
    if not sermon_lib.is_configured():
        return jsonify({"configured": False, "connected": False})
    source = SermonSource.query.filter_by(church_id=current_user.church_id).first()
    if not source:
        return jsonify({"configured": True, "connected": False})
    sermon_rows = (
        Sermon.query
        .filter_by(church_id=current_user.church_id)
        .order_by(Sermon.published_at.desc())
        .limit(25)
        .all()
    )
    return jsonify({
        "configured": True,
        "connected": True,
        "channel_title": source.channel_title or "",
        "channel_url": source.channel_url,
        "last_checked_at": iso_utc(source.last_checked_at),
        "last_error": source.last_error,
        "sermons": [_sermon_dict(s) for s in sermon_rows],
    })


def _run_in_background(fn, *args):
    app_obj = current_app._get_current_object()

    def runner():
        with app_obj.app_context():
            try:
                fn(*args)
            except Exception as exc:
                log.error("Background sermon task failed: %s", exc)
    threading.Thread(target=runner, daemon=True).start()


def _backfill_source(source_id):
    source = SermonSource.query.get(source_id)
    if source:
        sermon_lib.check_source(source)


@sermons_bp.route("/api/sermons/source", methods=["POST"])
@login_required
def connect_channel():
    if not sermon_lib.is_configured():
        return jsonify({"error": "Sermon ingestion is not enabled on this server."}), 400
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url or len(url) > 500:
        return jsonify({"error": "Enter your YouTube channel link."}), 400
    if SermonSource.query.filter_by(church_id=current_user.church_id).first():
        return jsonify({"error": "A channel is already connected. Disconnect it first."}), 400

    try:
        channel = sermon_lib.resolve_channel(url)
    except SermonError as exc:
        return jsonify({"error": str(exc)}), 400

    source = SermonSource(
        church_id=current_user.church_id,
        channel_url=url,
        channel_id=channel["channel_id"],
        channel_title=channel["title"],
    )
    db.session.add(source)
    db.session.commit()

    # Backfill runs in the background — video-fallback ingestion is slow
    _run_in_background(_backfill_source, source.id)

    log.info("Sermon channel connected: church_id=%d channel=%r",
             source.church_id, source.channel_title)
    return jsonify({"ok": True, "channel_title": source.channel_title}), 201


@sermons_bp.route("/api/sermons/source", methods=["DELETE"])
@login_required
def disconnect_channel():
    source = SermonSource.query.filter_by(church_id=current_user.church_id).first()
    if source:
        db.session.delete(source)  # cascades to sermons
        db.session.commit()
    return jsonify({"ok": True})


@sermons_bp.route("/api/sermons/check", methods=["POST"])
@login_required
def check_now():
    source = SermonSource.query.filter_by(church_id=current_user.church_id).first()
    if not source:
        return jsonify({"error": "No channel connected."}), 400
    _run_in_background(_backfill_source, source.id)
    return jsonify({"ok": True, "message": "Checking for new sermons in the background."})


@sermons_bp.route("/api/sermons/<int:sermon_id>/reingest", methods=["POST"])
@login_required
def reingest(sermon_id):
    sermon = Sermon.query.filter_by(
        id=sermon_id, church_id=current_user.church_id
    ).first()
    if not sermon:
        return jsonify({"error": "Sermon not found."}), 404
    sermon.status = "pending"
    sermon.error = None
    db.session.commit()

    def _reingest(sid=sermon.id):
        target = Sermon.query.get(sid)
        if target:
            sermon_lib.ingest_sermon(target)
    _run_in_background(_reingest)
    return jsonify({"ok": True, "message": "Re-ingesting in the background."})


@sermons_bp.route("/api/sermons/<int:sermon_id>", methods=["PATCH"])
@login_required
def paste_transcript(sermon_id):
    """Manual escape hatch: staff paste a transcript and we distill from it."""
    sermon = Sermon.query.filter_by(
        id=sermon_id, church_id=current_user.church_id
    ).first()
    if not sermon:
        return jsonify({"error": "Sermon not found."}), 404
    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()
    if len(transcript) < 200:
        return jsonify({"error": "That transcript looks too short to be a sermon."}), 400

    sermon.transcript = transcript[:sermon_lib.MAX_TRANSCRIPT_CHARS]
    db.session.commit()
    ok = sermon_lib.ingest_sermon(sermon)
    return jsonify({"ok": ok, "sermon": _sermon_dict(sermon)}), (200 if ok else 502)
