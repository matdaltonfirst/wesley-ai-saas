"""Weekly activity digest — stats assembly for the Monday summary email."""

import logging
from collections import Counter
from datetime import datetime, timedelta

from models import (
    db, Church, User,
    WidgetConversation, WidgetMessage, GuestConnection, AnswerFeedback,
)

log = logging.getLogger("wesley")


def send_weekly_digests() -> int:
    """Send the weekly digest to every church admin. Returns churches emailed.

    Must run inside an app context. Quiet churches are skipped, and
    ``digest_last_sent_at`` makes the job idempotent across restarts.
    """
    from emails import send_weekly_digest_email
    from config import FROM_EMAIL, APP_URL, SUPPORT_EMAIL

    now = datetime.utcnow()
    sent = 0
    for church in Church.query.all():
        if church.digest_last_sent_at and (now - church.digest_last_sent_at).days < 3:
            continue
        stats = build_weekly_digest(church, now - timedelta(days=7))
        if not stats:
            continue
        admins = User.query.filter_by(church_id=church.id, role="admin").all()
        if not admins:
            continue
        for admin in admins:
            send_weekly_digest_email(
                admin.email, church.name, stats, FROM_EMAIL, APP_URL, SUPPORT_EMAIL
            )
        church.digest_last_sent_at = now
        sent += 1
    db.session.commit()
    return sent


def build_weekly_digest(church, since: datetime):
    """Assemble one church's widget activity since ``since`` (usually 7 days).

    Returns None when there is nothing worth emailing about — no
    conversations, no new guest connections, and nothing awaiting review —
    so quiet churches are not nagged.
    """
    from routes.widget import _categorize  # shared topic rules, avoids drift

    convs = (
        WidgetConversation.query
        .filter(
            WidgetConversation.church_id == church.id,
            WidgetConversation.created_at >= since,
        )
        .all()
    )

    questions = 0
    topic_counter: Counter = Counter()
    for conv in convs:
        user_msgs = [m for m in conv.messages if m.role == "user"]
        questions += len(user_msgs)
        if user_msgs:
            topic_counter[_categorize(user_msgs[0].content)] += 1

    new_guests = GuestConnection.query.filter(
        GuestConnection.church_id == church.id,
        GuestConnection.created_at >= since,
    ).count()
    pending_guests = GuestConnection.query.filter_by(
        church_id=church.id, status="new",
    ).count()

    open_feedback = AnswerFeedback.query.filter_by(
        church_id=church.id, status="open",
    ).count()
    flagged_week = AnswerFeedback.query.filter(
        AnswerFeedback.church_id == church.id,
        AnswerFeedback.rating.in_(("auto_flagged", "not_helpful")),
        AnswerFeedback.created_at >= since,
    ).count()
    corrections_week = AnswerFeedback.query.filter(
        AnswerFeedback.church_id == church.id,
        AnswerFeedback.status == "corrected",
        AnswerFeedback.resolved_at >= since,
    ).count()

    if not convs and not new_guests and not open_feedback:
        return None

    return {
        "week_start": since.strftime("%B %-d"),
        "week_end": datetime.utcnow().strftime("%B %-d, %Y"),
        "conversations": len(convs),
        "questions": questions,
        "top_topics": topic_counter.most_common(3),
        "new_guests": new_guests,
        "pending_guests": pending_guests,
        "open_feedback": open_feedback,
        "flagged_week": flagged_week,
        "corrections_week": corrections_week,
    }
