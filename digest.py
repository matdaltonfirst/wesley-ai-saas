"""Weekly activity digest — stats assembly for the Monday summary email."""

import logging
from collections import Counter
from datetime import datetime, timedelta

from models import (
    db, User,
    WidgetConversation, WidgetMessage, GuestConnection, AnswerFeedback,
)

log = logging.getLogger("wesley")


def send_weekly_digests() -> int:
    """Send the weekly digest to every admin. Returns 1 if sent, else 0.

    Must run inside an app context. A quiet week sends nothing, and
    ``digest_last_sent_at`` makes the job idempotent across restarts.
    """
    from emails import send_weekly_digest_email
    from config import FROM_EMAIL, APP_URL, SUPPORT_EMAIL
    from organization import get_org

    org = get_org()
    now = datetime.utcnow()
    if org.digest_last_sent_at and (now - org.digest_last_sent_at).days < 3:
        return 0
    stats = build_weekly_digest(now - timedelta(days=7))
    if not stats:
        return 0
    admins = User.query.filter_by(role="admin").all()
    if not admins:
        return 0
    for admin in admins:
        send_weekly_digest_email(
            admin.email, org.name, stats, FROM_EMAIL, APP_URL, SUPPORT_EMAIL
        )
    org.digest_last_sent_at = now
    db.session.commit()
    return 1


def build_weekly_digest(since: datetime):
    """Assemble widget activity since ``since`` (usually 7 days).

    Returns None when there is nothing worth emailing about (no
    conversations, no new guest connections, and nothing awaiting review), so a
    quiet week is not nagged about.
    """
    from routes.widget import _categorize  # shared topic rules, avoids drift

    convs = (
        WidgetConversation.query
        .filter(WidgetConversation.created_at >= since)
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
        GuestConnection.created_at >= since,
    ).count()
    pending_guests = GuestConnection.query.filter_by(status="new").count()

    open_feedback = AnswerFeedback.query.filter_by(status="open").count()
    flagged_week = AnswerFeedback.query.filter(
        AnswerFeedback.rating.in_(("auto_flagged", "not_helpful")),
        AnswerFeedback.created_at >= since,
    ).count()
    corrections_week = AnswerFeedback.query.filter(
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
