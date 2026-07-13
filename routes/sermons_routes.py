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
        .filter(
            Sermon.church_id == current_user.church_id,
            Sermon.status != "excluded",
        )
        .order_by(Sermon.published_at.desc())
        .limit(25)
        .all()
    )
    # Self-heal: a deploy can kill the backfill/rebuild thread mid-run, leaving
    # sermons stranded in "pending". Seeing them here restarts the work.
    if any(s.status == "pending" for s in sermon_rows) and _try_claim_recovery(source.id):
        _run_in_background(_backfill_source, source.id)

    return jsonify({
        "configured": True,
        "connected": True,
        "channel_title": source.channel_title or "",
        "channel_url": source.channel_url,
        "last_checked_at": iso_utc(source.last_checked_at),
        "last_error": source.last_error,
        "sermons": [_sermon_dict(s) for s in sermon_rows],
    })


# Sources with an ingestion run currently in flight (single-worker process),
# so repeated status polls don't stack duplicate recovery threads.
_active_recoveries = set()
_recovery_lock = threading.Lock()


def _try_claim_recovery(source_id) -> bool:
    with _recovery_lock:
        if source_id in _active_recoveries:
            return False
        _active_recoveries.add(source_id)
        return True


def _release_recovery(source_id):
    with _recovery_lock:
        _active_recoveries.discard(source_id)


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
    try:
        source = SermonSource.query.get(source_id)
        if source:
            sermon_lib.check_source(source)
    finally:
        _release_recovery(source_id)


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
    _try_claim_recovery(source.id)
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
    if not _try_claim_recovery(source.id):
        return jsonify({"ok": True, "message": "A check is already running."})
    _run_in_background(_backfill_source, source.id)
    return jsonify({"ok": True, "message": "Checking for new sermons in the background."})


@sermons_bp.route("/api/sermons/reingest-all", methods=["POST"])
@login_required
def reingest_all():
    """Regenerate every sermon's summary (e.g. after a distillation improvement)."""
    sermon_rows = Sermon.query.filter(
        Sermon.church_id == current_user.church_id,
        Sermon.status != "excluded",
    ).all()
    if not sermon_rows:
        return jsonify({"error": "No sermons to rebuild."}), 400
    ids = []
    for s in sermon_rows:
        s.status = "pending"
        s.error = None
        ids.append(s.id)
    db.session.commit()

    def _reingest_batch(sermon_ids=tuple(ids)):
        for sid in sermon_ids:
            target = Sermon.query.get(sid)
            if target:
                sermon_lib.ingest_sermon(target)
    _run_in_background(_reingest_batch)
    return jsonify({"ok": True, "count": len(ids),
                    "message": "Rebuilding summaries in the background."})


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


@sermons_bp.route("/api/sermons/<int:sermon_id>", methods=["DELETE"])
@login_required
def exclude_sermon(sermon_id):
    """Remove a video from Wesley's knowledge. The row is kept (status
    "excluded") so the daily channel check never re-ingests it."""
    sermon = Sermon.query.filter_by(
        id=sermon_id, church_id=current_user.church_id
    ).first()
    if not sermon:
        return jsonify({"error": "Sermon not found."}), 404
    sermon.status = "excluded"
    sermon.transcript = None
    sermon.summary = None
    sermon.main_points = None
    sermon.error = None
    db.session.commit()
    log.info("Sermon excluded: %r (church_id=%d)", sermon.title, sermon.church_id)
    return jsonify({"ok": True})
