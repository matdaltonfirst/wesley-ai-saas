"""A sermon whose transcript arrives late must still be able to get a packet.

The original queue looked only at the last 8 days from *publication*, while the
transcript backfill keeps trying for 120. Anything whose captions landed after
day 8 was therefore never offered a packet and never would be — these tests pin
that gap closed, and pin the email window that the 8 days was really protecting.
"""

import json
from datetime import datetime, timedelta

import pytest
from unittest.mock import patch

from models import db, Church, Sermon, SermonPacket, SermonSource
import packets as packets_mod
from packets import (EMAIL_WINDOW_DAYS, LOOKBACK_DAYS, MAX_PER_CHURCH_PER_RUN,
                     sermons_awaiting_content, sermons_needing_packets)


@pytest.fixture
def second_church(app):
    """A second tenant, torn down like the shared `church` fixture.

    Without the teardown its sermons survive into later tests, where
    run_monday_packets() — which walks every church — picks them up.
    """
    from tests.conftest import _delete_church_rows
    c = Church(name="Second Church", billing_exempt=True)
    db.session.add(c); db.session.commit(); db.session.refresh(c)
    yield c
    _delete_church_rows(c.id)


def _source(church):
    src = SermonSource.query.filter_by(church_id=church.id).first()
    if not src:
        src = SermonSource(church_id=church.id, channel_url="https://y/@x",
                           channel_id="UC%d" % church.id)
        db.session.add(src); db.session.flush()
    return src


def _sermon(church, days_ago, vid, transcript="He said something worth quoting."):
    s = Sermon(source_id=_source(church).id, church_id=church.id, video_id=vid,
               title="Sermon " + vid, status="ingested", transcript=transcript,
               published_at=datetime.utcnow() - timedelta(days=days_ago))
    db.session.add(s); db.session.commit()
    return s


class TestQueue:
    def test_late_transcript_is_still_picked_up(self, app, church):
        """The regression: day 9 used to fall out of the queue permanently."""
        s = _sermon(church, 9, "late")
        assert s.id in [x.id for x in sermons_needing_packets(church.id)]

    def test_sermon_beyond_the_lookback_is_left_alone(self, app, church):
        _sermon(church, LOOKBACK_DAYS + 5, "ancient")
        assert sermons_needing_packets(church.id) == []

    def test_a_sermon_with_a_packet_is_not_requeued(self, app, church):
        s = _sermon(church, 2, "done")
        db.session.add(SermonPacket(church_id=church.id, sermon_id=s.id,
                                    status="ready", content="{}"))
        db.session.commit()
        assert sermons_needing_packets(church.id) == []

    def test_a_sermon_without_a_transcript_is_not_queued(self, app, church):
        s = _sermon(church, 2, "notext"); s.transcript = None; db.session.commit()
        assert sermons_needing_packets(church.id) == []

    def test_back_catalogue_is_capped_per_run(self, app, church):
        """Connecting a channel must not spend a model call per sermon."""
        for i in range(MAX_PER_CHURCH_PER_RUN + 4):
            _sermon(church, i + 1, "bulk%d" % i)
        assert len(sermons_needing_packets(church.id)) == MAX_PER_CHURCH_PER_RUN

    def test_cap_applies_per_church_not_globally(self, app, church, second_church):
        for i in range(MAX_PER_CHURCH_PER_RUN + 2):
            _sermon(church, i + 1, "a%d" % i)
            _sermon(second_church, i + 1, "b%d" % i)
        by_church = {}
        for s in sermons_needing_packets():
            by_church[s.church_id] = by_church.get(s.church_id, 0) + 1
        assert by_church[church.id] == MAX_PER_CHURCH_PER_RUN
        assert by_church[second_church.id] == MAX_PER_CHURCH_PER_RUN

    def test_awaiting_content_ignores_the_window(self, app, church):
        """The dashboard should still offer to build very old messages."""
        s = _sermon(church, 300, "old")
        assert s.id in [x.id for x in sermons_awaiting_content(church.id)]


class TestEmailWindow:
    def _run(self, church, days_ago):
        _sermon(church, days_ago, "v%d" % days_ago)
        content = {"quotes": [{"text": "q"}], "social": [{"platform": "facebook",
                                                          "body": "b"}]}
        with patch("sermon_packet.build_packet", return_value=content), \
             patch("packets.send_packet_email", return_value=1) as send:
            result = packets_mod.run_monday_packets()
        return result, send

    def test_recent_sermon_is_emailed(self, app, church):
        result, send = self._run(church, 1)
        assert result["generated"] == 1
        assert send.called

    def test_catch_up_is_built_but_not_emailed(self, app, church):
        """An old catch-up belongs in the dashboard, not in Monday's mail."""
        result, send = self._run(church, EMAIL_WINDOW_DAYS + 3)
        assert result["generated"] == 1
        assert not send.called
