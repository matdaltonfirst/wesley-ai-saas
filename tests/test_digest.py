"""Tests for the weekly activity digest (digest.py)."""

from datetime import datetime, timedelta
from unittest.mock import patch

from models import (
    db, WidgetConversation, WidgetMessage, GuestConnection, AnswerFeedback,
)
from digest import build_weekly_digest, send_weekly_digests


def _seed_conversation(church, question, answer="Here you go!", days_ago=1):
    created = datetime.utcnow() - timedelta(days=days_ago)
    wconv = WidgetConversation(
        church_id=church.id, session_id=f"digest-{question[:20]}-{days_ago}",
        created_at=created,
    )
    db.session.add(wconv)
    db.session.flush()
    db.session.add(WidgetMessage(
        widget_conversation_id=wconv.id, role="user", content=question,
        created_at=created,
    ))
    db.session.add(WidgetMessage(
        widget_conversation_id=wconv.id, role="assistant", content=answer,
        created_at=created,
    ))
    db.session.commit()
    return wconv


def _cleanup_widget_data(church):
    for wconv in WidgetConversation.query.filter_by(church_id=church.id).all():
        db.session.delete(wconv)
    GuestConnection.query.filter_by(church_id=church.id).delete()
    db.session.commit()


class TestBuildWeeklyDigest:
    def test_quiet_church_returns_none(self, app, church):
        since = datetime.utcnow() - timedelta(days=7)
        assert build_weekly_digest(church, since) is None

    def test_counts_conversations_questions_and_topics(self, app, church):
        _seed_conversation(church, "What time is Sunday worship service?")
        _seed_conversation(church, "When does the fall festival event start?")
        old = _seed_conversation(church, "Old question", days_ago=30)

        since = datetime.utcnow() - timedelta(days=7)
        stats = build_weekly_digest(church, since)

        assert stats["conversations"] == 2
        assert stats["questions"] == 2
        topic_names = [name for name, _ in stats["top_topics"]]
        assert "Service Times" in topic_names or "Events & Programs" in topic_names
        assert old.id not in []  # old conversation excluded from the window
        _cleanup_widget_data(church)

    def test_counts_guests_and_feedback(self, app, church):
        _seed_conversation(church, "Hi there")
        wmsg = WidgetMessage.query.filter_by(role="assistant").first()
        db.session.add(GuestConnection(
            church_id=church.id, name="Pat Visitor", email="pat@example.org",
        ))
        db.session.add(AnswerFeedback(
            church_id=church.id, widget_message_id=wmsg.id,
            rating="auto_flagged", status="open",
        ))
        db.session.commit()

        stats = build_weekly_digest(church, datetime.utcnow() - timedelta(days=7))
        assert stats["new_guests"] == 1
        assert stats["pending_guests"] == 1
        assert stats["open_feedback"] == 1
        assert stats["flagged_week"] == 1
        _cleanup_widget_data(church)


class TestSendWeeklyDigests:
    def test_sends_to_admin_and_marks_church(self, app, church, admin_user):
        _seed_conversation(church, "What time is worship?")

        with patch("emails.send_weekly_digest_email") as mock_send:
            sent = send_weekly_digests()

        assert sent == 1
        assert mock_send.call_count == 1
        assert mock_send.call_args.args[0] == admin_user.email
        assert mock_send.call_args.args[1] == church.name
        assert church.digest_last_sent_at is not None
        _cleanup_widget_data(church)

    def test_idempotent_within_three_days(self, app, church, admin_user):
        _seed_conversation(church, "What time is worship?")
        church.digest_last_sent_at = datetime.utcnow() - timedelta(days=1)
        db.session.commit()

        with patch("emails.send_weekly_digest_email") as mock_send:
            sent = send_weekly_digests()

        assert sent == 0
        mock_send.assert_not_called()
        church.digest_last_sent_at = None
        db.session.commit()
        _cleanup_widget_data(church)

    def test_quiet_church_not_emailed(self, app, church, admin_user):
        with patch("emails.send_weekly_digest_email") as mock_send:
            sent = send_weekly_digests()

        assert sent == 0
        mock_send.assert_not_called()
        assert church.digest_last_sent_at is None
