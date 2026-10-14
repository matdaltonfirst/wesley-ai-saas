"""The weekly sermon content review queue.

Each sermon's content is five pieces: YouTube (titles, description, chapters), quotes, social
posts, the blog post and the small group guide. Every piece has its own status:

    draft  ->  approved          (a person with sunday.approve says it is good)
    draft  ->  needs_changes     (with a note saying what)
    approved or needs_changes -> draft   (the moment anyone edits or regenerates it: an approval
                                          is for the exact words that were approved)

Rules this module keeps, because they are the point of a review queue:

* **Nothing publishes or sends from here.** Approval is a record that a person looked, not an action.
* **Regenerating never destroys.** The previous text of a piece is saved as a version first, and a
  version can be restored.
* **An edit voids an approval.** Approved text that someone then changes goes back to draft.
* **Quotes and chapters are not editable.** Their value is that they are provably what was preached
  and where. A person can regenerate them or write a post instead.
"""

import json
import logging
from datetime import datetime, timedelta

from models import ContentReview, ContentVersion, Sermon, SermonPacket, db
from organization import get_org

log = logging.getLogger("wesley")

STATUSES = ("draft", "needs_changes", "approved")
MAX_VERSIONS_PER_PIECE = 10
QUEUE_WINDOW_DAYS = 60

# piece key -> (label, the fields of the packet content that belong to it)
PIECES = {
    "youtube": ("YouTube title, description and chapters", ("titles", "description", "chapters")),
    "quotes":  ("Quotes", ("quotes",)),
    "social":  ("Social posts", ("social",)),
    "blog":    ("Blog post", ("blog",)),
    "guide":   ("Small group guide", ("guide",)),
}


class ReviewError(Exception):
    """A problem worth showing to staff as written."""


def load_content(packet) -> dict:
    try:
        return json.loads(packet.content) if packet.content else {}
    except (ValueError, TypeError):
        return {}


def piece_fields(content: dict, piece: str) -> dict:
    return {f: content.get(f) for f in PIECES[piece][1] if content.get(f) not in (None, "", [], {})}


def has_piece(content: dict, piece: str) -> bool:
    return bool(piece_fields(content, piece))


def _review_row(packet_id: int, piece: str):
    row = ContentReview.query.filter_by(packet_id=packet_id, piece=piece).first()
    if row is None:
        row = ContentReview(packet_id=packet_id, piece=piece, status="draft")
        db.session.add(row)
        db.session.flush()
    return row


def ensure_reviews(packet) -> dict:
    """Make sure each piece that has content has a review row. Returns {piece: row}."""
    content = load_content(packet)
    rows = {}
    for piece in PIECES:
        if has_piece(content, piece):
            rows[piece] = _review_row(packet.id, piece)
    db.session.commit()
    return rows


def _save_version(packet_id: int, piece: str, fields: dict, reason: str, user) -> None:
    if not fields:
        return
    db.session.add(ContentVersion(packet_id=packet_id, piece=piece, content=json.dumps(fields), reason=reason,
                                  created_by_id=getattr(user, "id", None)))
    db.session.flush()
    extra = (ContentVersion.query.filter_by(packet_id=packet_id, piece=piece)
             .order_by(ContentVersion.id.desc()).offset(MAX_VERSIONS_PER_PIECE).all())
    for v in extra:
        db.session.delete(v)


def _reset_to_draft(packet_id: int, piece: str) -> None:
    row = _review_row(packet_id, piece)
    row.status, row.note, row.decided_by_id, row.decided_at = "draft", None, None, None


# ── Decisions ────────────────────────────────────────────────────────────────

def decide(packet, piece: str, status: str, user, note: str = "") -> ContentReview:
    if piece not in PIECES:
        raise ReviewError("That is not a piece of this content.")
    if status not in ("approved", "needs_changes", "draft"):
        raise ReviewError("Unknown status.")
    if not has_piece(load_content(packet), piece):
        raise ReviewError("There is nothing in that piece to review yet.")
    note = (note or "").strip()
    if status == "needs_changes" and not note:
        raise ReviewError("Say what needs to change, so whoever fixes it knows.")
    row = _review_row(packet.id, piece)
    row.status = status
    row.note = note[:500] or None
    row.decided_by_id = getattr(user, "id", None) if status != "draft" else None
    row.decided_at = datetime.utcnow() if status != "draft" else None
    db.session.commit()
    return row


# ── Edits and regeneration ───────────────────────────────────────────────────

def record_edit(packet, old_content: dict, new_content: dict, user) -> list:
    """After a person edited the packet: keep what each changed piece said before, and void its approval."""
    changed = []
    for piece in PIECES:
        before, after = piece_fields(old_content, piece), piece_fields(new_content, piece)
        if before != after:
            _save_version(packet.id, piece, before, "edited", user)
            _reset_to_draft(packet.id, piece)
            changed.append(piece)
    db.session.commit()
    return changed


def _build_piece(sermon, piece: str) -> dict:
    church = get_org()
    if piece == "blog":
        from sermon_longform import build_blog
        return {"blog": build_blog(sermon, church)}
    if piece == "guide":
        from sermon_longform import build_guide
        return {"guide": build_guide(sermon, church)}
    from sermon_packet import build_packet
    built = build_packet(sermon, church)             # one model call covers all three; only this piece is kept
    return {f: built.get(f) for f in PIECES[piece][1]}


def regenerate_piece(packet, piece: str, user) -> dict:
    """Rebuild one piece from the sermon. The old text is kept as a version first; a failure changes nothing."""
    if piece not in PIECES:
        raise ReviewError("That is not a piece of this content.")
    sermon = Sermon.query.get(packet.sermon_id)
    if not sermon or not sermon.transcript:
        raise ReviewError("That sermon has no transcript to work from.")
    try:
        fresh = _build_piece(sermon, piece)
    except Exception as exc:
        log.error("Regenerating %s for packet_id=%s failed: %s", piece, packet.id, exc)
        raise ReviewError("Could not rebuild that piece. The existing text is untouched. Please try again.") from exc
    if not any(v not in (None, "", [], {}) for v in fresh.values()):
        raise ReviewError("The rebuild came back empty, so the existing text was kept.")
    content = load_content(packet)
    _save_version(packet.id, piece, piece_fields(content, piece), "regenerated", user)
    content.update(fresh)
    packet.content = json.dumps(content)
    _reset_to_draft(packet.id, piece)
    db.session.commit()
    return piece_fields(content, piece)


def restore_version(version, packet, user) -> dict:
    """Put an earlier version back. What is there now is kept as a version first."""
    content = load_content(packet)
    piece = version.piece
    _save_version(packet.id, piece, piece_fields(content, piece), "edited", user)
    content.update(json.loads(version.content))
    packet.content = json.dumps(content)
    _reset_to_draft(packet.id, piece)
    db.session.commit()
    return piece_fields(content, piece)


# ── The queue ────────────────────────────────────────────────────────────────

def overall_status(rows: dict) -> str:
    """One word for a whole sermon: approved only when every piece that exists is approved."""
    statuses = [r.status for r in rows.values()]
    if not statuses:
        return "empty"
    if "needs_changes" in statuses:
        return "needs_changes"
    if all(s == "approved" for s in statuses):
        return "approved"
    return "in_review"


def queue(days: int = QUEUE_WINDOW_DAYS) -> list:
    """Recent sermons with their content, newest first, with each piece's status."""
    since = datetime.utcnow() - timedelta(days=days)
    out = []
    pairs = (db.session.query(SermonPacket, Sermon).join(Sermon, Sermon.id == SermonPacket.sermon_id)
             .filter(SermonPacket.status == "ready", Sermon.published_at >= since)
             .order_by(Sermon.published_at.desc()).all())
    for packet, sermon in pairs:
        rows = ensure_reviews(packet)
        out.append({"packet": packet, "sermon": sermon, "rows": rows, "status": overall_status(rows)})
    return out


def waiting_for_approval(days: int = 21) -> list:
    """Sermons with content a person has not finished reviewing (for alerts)."""
    return [q for q in queue(days) if q["status"] in ("in_review", "needs_changes")]
