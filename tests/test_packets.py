"""Tests for generating and delivering the Monday packet.

The delivery rules that matter are about restraint: one packet per sermon
however often the job runs, one church's failure never blocking another's, and
nothing sent when there is nothing worth sending. A Monday email that arrives
empty teaches staff the Monday email is not worth opening.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import packets
from models import db, Church, Sermon, SermonPacket, SermonSource, User


TRANSCRIPT = (
    "Good morning church. Today we are looking at the vine and the branches. "
    "The branch does not strain to produce grapes. It simply stays connected. "
    "The question is whether you are staying connected to the vine today."
)


@pytest.fixture
def source(app, church):
    s = SermonSource(church_id=church.id, channel_url="https://youtube.com/@x",
                     channel_id="UCtest")
    db.session.add(s)
    db.session.commit()
    return s


def _sermon(church, source, video_id="v1", days_ago=1, transcript=TRANSCRIPT,
            status="ingested"):
    s = Sermon(source_id=source.id, church_id=church.id, video_id=video_id,
               title=f"Message {video_id}", status=status, transcript=transcript,
               published_at=datetime.utcnow() - timedelta(days=days_ago))
    db.session.add(s)
    db.session.commit()
    return s


def _content(quotes=1, social=1):
    return {
        "titles": ["A Title"],
        "description": "About the vine.",
        "chapters": [{"label": "Start", "start": 0.0, "timestamp": "0:00"}],
        "quotes": [{"text": "The branch does not strain to produce grapes.",
                    "start": 10.0, "timestamp": "0:10", "clip_url": "https://y/1"}] * quotes,
        "social": [{"platform": "facebook", "body": "Rest is not laziness."}] * social,
    }


# ── Choosing what to build ───────────────────────────────────────────────────

class TestSelection:
    def test_a_recent_transcribed_sermon_is_picked_up(self, church, source):
        s = _sermon(church, source)
        assert [x.id for x in packets.sermons_needing_packets(church.id)] == [s.id]

    def test_a_sermon_without_a_transcript_is_skipped(self, church, source):
        _sermon(church, source, transcript=None)
        assert packets.sermons_needing_packets(church.id) == []

    def test_an_old_sermon_is_not_picked_up(self, church, source):
        """Connecting a channel should not email a church about March."""
        _sermon(church, source, days_ago=packets.LOOKBACK_DAYS + 5)
        assert packets.sermons_needing_packets(church.id) == []

    def test_an_excluded_sermon_is_skipped(self, church, source):
        _sermon(church, source, status="excluded")
        assert packets.sermons_needing_packets(church.id) == []

    def test_a_sermon_that_already_has_a_packet_is_not_repeated(self, church, source):
        s = _sermon(church, source)
        db.session.add(SermonPacket(church_id=church.id, sermon_id=s.id, status="ready"))
        db.session.commit()
        assert packets.sermons_needing_packets(church.id) == []

    def test_a_failed_packet_is_not_retried_forever(self, church, source):
        """Whatever the outcome, one attempt per sermon — otherwise a sermon
        that always fails is retried every night for ever."""
        s = _sermon(church, source)
        db.session.add(SermonPacket(church_id=church.id, sermon_id=s.id,
                                    status="failed", error="boom"))
        db.session.commit()
        assert packets.sermons_needing_packets(church.id) == []


# ── Generation ───────────────────────────────────────────────────────────────

class TestGeneration:
    def test_a_successful_packet_is_stored_ready(self, church, source):
        s = _sermon(church, source)
        with patch("sermon_packet.build_packet", return_value=_content()):
            packet = packets.generate_packet(s)
        assert packet.status == "ready"
        assert packet.generated_at is not None
        assert json.loads(packet.content)["titles"] == ["A Title"]

    def test_a_failure_is_recorded_not_raised(self, church, source):
        """One church's bad sermon must not stop every other church's packet."""
        s = _sermon(church, source)
        with patch("sermon_packet.build_packet", side_effect=RuntimeError("model down")):
            packet = packets.generate_packet(s)
        assert packet.status == "failed"
        assert "model down" in packet.error

    def test_one_church_failing_does_not_stop_the_next(self, church, source):
        from models import Church as C
        other = C(name="Second Church", billing_exempt=True)
        db.session.add(other); db.session.flush()
        other_source = SermonSource(church_id=other.id, channel_url="https://y/@z",
                                    channel_id="UCother")
        db.session.add(other_source); db.session.flush()
        bad = _sermon(church, source, video_id="bad")
        good = _sermon(other, other_source, video_id="good")

        def build(sermon, ch):
            if sermon.id == bad.id:
                raise RuntimeError("boom")
            return _content()

        try:
            with patch("sermon_packet.build_packet", side_effect=build), \
                 patch("packets.send_packet_email", return_value=1):
                result = packets.run_monday_packets()
            assert result["generated"] == 1
            assert result["failed"] == 1
        finally:
            SermonPacket.query.filter_by(church_id=other.id).delete()
            Sermon.query.filter_by(church_id=other.id).delete()
            SermonSource.query.filter_by(church_id=other.id).delete()
            C.query.filter_by(id=other.id).delete()
            db.session.commit()


# ── Delivery ─────────────────────────────────────────────────────────────────

class TestDelivery:
    def test_every_admin_is_emailed(self, church, source):
        for n in range(3):
            db.session.add(User(email=f"admin{n}@x.org", password_hash="x",
                                church_id=church.id, role="admin"))
        db.session.add(User(email="staff@x.org", password_hash="x",
                            church_id=church.id, role="staff"))
        db.session.commit()
        s = _sermon(church, source)
        packet = SermonPacket(church_id=church.id, sermon_id=s.id, status="ready",
                              content=json.dumps(_content()))
        db.session.add(packet); db.session.commit()

        with patch("emails.send_sermon_packet_email") as send:
            count = packets.send_packet_email(packet)
        assert count == 3
        recipients = sorted(call.args[0] for call in send.call_args_list)
        assert recipients == ["admin0@x.org", "admin1@x.org", "admin2@x.org"]
        assert packet.emailed_at is not None

    def test_an_empty_packet_is_not_emailed(self, church, source):
        """An empty Monday email teaches staff it is not worth opening."""
        db.session.add(User(email="a@x.org", password_hash="x",
                            church_id=church.id, role="admin"))
        db.session.commit()
        _sermon(church, source)
        with patch("sermon_packet.build_packet",
                   return_value=_content(quotes=0, social=0)), \
             patch("emails.send_sermon_packet_email") as send:
            result = packets.run_monday_packets()
        assert result["generated"] == 1
        assert result["emailed"] == 0
        send.assert_not_called()

    def test_a_failed_packet_is_never_emailed(self, church, source):
        db.session.add(User(email="a@x.org", password_hash="x",
                            church_id=church.id, role="admin"))
        db.session.commit()
        _sermon(church, source)
        with patch("sermon_packet.build_packet", side_effect=RuntimeError("boom")), \
             patch("emails.send_sermon_packet_email") as send:
            packets.run_monday_packets()
        send.assert_not_called()

    def test_a_church_with_no_admins_is_skipped_quietly(self, church, source):
        s = _sermon(church, source)
        packet = SermonPacket(church_id=church.id, sermon_id=s.id, status="ready",
                              content=json.dumps(_content()))
        db.session.add(packet); db.session.commit()
        assert packets.send_packet_email(packet) == 0


class TestIdempotence:
    def test_running_twice_sends_one_email(self, church, source):
        """The job may run again after a restart; a church must not receive
        Sunday's packet twice."""
        db.session.add(User(email="a@x.org", password_hash="x",
                            church_id=church.id, role="admin"))
        db.session.commit()
        _sermon(church, source)
        with patch("sermon_packet.build_packet", return_value=_content()), \
             patch("emails.send_sermon_packet_email") as send:
            first = packets.run_monday_packets()
            second = packets.run_monday_packets()
        assert first["emailed"] == 1
        assert second == {"generated": 0, "emailed": 0, "failed": 0}
        assert send.call_count == 1
