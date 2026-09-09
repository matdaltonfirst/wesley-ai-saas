"""Generating and delivering the Monday packet.

The job body lives here rather than in app.py so it can be tested without the
scheduler, and so the two halves — generate, then send — stay separable. A
packet that generated fine but failed to email should not be regenerated on the
next run, and a packet that failed to generate should not block the email for
every other church.
"""

import json
import logging
from datetime import datetime, timedelta

from models import db, Church, Sermon, SermonPacket, User

log = logging.getLogger("wesley")

# Two different windows, because generating and emailing carry different risks.
#
# LOOKBACK_DAYS bounds what is worth building content for at all. It used to be
# 8, which quietly lost sermons: this job runs weekly, YouTube captions often
# arrive days late, and the transcript backfill keeps trying for 120 days. A
# sermon whose captions landed on day 9 was therefore never offered a packet and
# never would be — the queue only ever looked at the last 8 days, so once a
# sermon aged out of it, it was gone for good.
#
# EMAIL_WINDOW_DAYS bounds what is worth *mailing*. That is the constraint the
# old 8 days was really expressing: nobody wants Monday's mail about a sermon
# from March. Catch-ups still reach the dashboard; they just arrive quietly.
LOOKBACK_DAYS = 45
EMAIL_WINDOW_DAYS = 8

# A newly connected channel ingests its whole back catalogue at once. Without a
# cap the first run after connecting would spend a model call per sermon, so
# take only the newest few per church per run and let the rest follow on later
# runs — or on demand, from the dashboard.
MAX_PER_CHURCH_PER_RUN = 3


def sermons_awaiting_content(church_id: int = None, since_days: int = None):
    """Ingested sermons that have a transcript but no packet, newest first.

    The dashboard uses this to show what it *could* build content from, which is
    what makes an empty Sunday Content panel explainable rather than mysterious.
    """
    query = (
        Sermon.query
        .outerjoin(SermonPacket, SermonPacket.sermon_id == Sermon.id)
        .filter(
            Sermon.status == "ingested",
            Sermon.transcript.isnot(None),
            SermonPacket.id.is_(None),
        )
        .order_by(Sermon.published_at.desc())
    )
    if since_days is not None:
        query = query.filter(
            Sermon.published_at >= datetime.utcnow() - timedelta(days=since_days))
    if church_id:
        query = query.filter(Sermon.church_id == church_id)
    return query.all()


def sermons_needing_packets(church_id: int = None):
    """What the weekly job should build, capped per church."""
    sermons = sermons_awaiting_content(church_id, since_days=LOOKBACK_DAYS)
    if church_id:
        return sermons[:MAX_PER_CHURCH_PER_RUN]

    per_church, out = {}, []
    for sermon in sermons:                        # already newest-first
        seen = per_church.get(sermon.church_id, 0)
        if seen >= MAX_PER_CHURCH_PER_RUN:
            continue
        per_church[sermon.church_id] = seen + 1
        out.append(sermon)
    return out


def generate_packet(sermon) -> SermonPacket:
    """Build and store the packet for one sermon.

    A failure is recorded on the row rather than raised, so one church's bad
    sermon cannot stop every other church's packet.
    """
    from sermon_packet import build_packet

    packet = SermonPacket(church_id=sermon.church_id, sermon_id=sermon.id,
                          status="pending")
    db.session.add(packet)
    db.session.commit()

    try:
        church = Church.query.get(sermon.church_id)
        content = build_packet(sermon, church)
        packet.content = json.dumps(content)
        packet.status = "ready"
        packet.error = None
        packet.generated_at = datetime.utcnow()
        log.info("Packet ready for sermon_id=%s (%r): %d quote(s), %d post(s)",
                 sermon.id, sermon.title,
                 len(content.get("quotes", [])), len(content.get("social", [])))
    except Exception as exc:
        db.session.rollback()
        packet.status = "failed"
        packet.error = str(exc)[:500]
        log.error("Packet failed for sermon_id=%s: %s", sermon.id, exc)
    db.session.commit()
    return packet


def send_packet_email(packet) -> int:
    """Email a ready packet to every admin. Returns how many were emailed."""
    from config import APP_URL, FROM_EMAIL, SUPPORT_EMAIL
    from emails import send_sermon_packet_email

    sermon = Sermon.query.get(packet.sermon_id)
    church = Church.query.get(packet.church_id)
    admins = User.query.filter_by(church_id=packet.church_id, role="admin").all()
    if not admins or not sermon or not church:
        return 0

    try:
        content = json.loads(packet.content) if packet.content else {}
    except (ValueError, TypeError):
        return 0

    for admin in admins:
        send_sermon_packet_email(
            admin.email, church.name, sermon, content,
            FROM_EMAIL, APP_URL, SUPPORT_EMAIL,
        )
    packet.emailed_at = datetime.utcnow()
    db.session.commit()
    return len(admins)


def run_monday_packets() -> dict:
    """Generate packets for last week's sermons and email them.

    Idempotent through the unique sermon_id on SermonPacket: a sermon that
    already has a packet is not picked up again, whatever its outcome was.
    """
    generated = emailed = failed = 0
    for sermon in sermons_needing_packets():
        packet = generate_packet(sermon)
        if packet.status != "ready":
            failed += 1
            continue
        generated += 1
        # An empty packet is worse than no email: it teaches staff that the
        # Monday mail is not worth opening.
        try:
            content = json.loads(packet.content or "{}")
        except (ValueError, TypeError):
            content = {}
        if not (content.get("quotes") or content.get("social")):
            log.info("Packet for sermon_id=%s has nothing worth sending.", sermon.id)
            continue
        # A catch-up for an older sermon still belongs in the dashboard, but
        # mailing it would be noise — it is not this week's news.
        if sermon.published_at < datetime.utcnow() - timedelta(days=EMAIL_WINDOW_DAYS):
            log.info("Packet for sermon_id=%s built as a catch-up; not emailing.",
                     sermon.id)
            continue
        if send_packet_email(packet):
            emailed += 1

    if generated or failed:
        log.info("Monday packets: %d generated, %d emailed, %d failed.",
                 generated, emailed, failed)
    return {"generated": generated, "emailed": emailed, "failed": failed}
