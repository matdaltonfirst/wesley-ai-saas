"""Tests for the Sunday Content dashboard routes.

Two properties carry weight here: one church can never read or edit another's
packets, and quotes are not editable. An edited "quote" is no longer a quote —
its whole value is that it is provably what was preached.
"""

import json
from datetime import datetime, timedelta

import pytest
from unittest.mock import patch

from models import db, Church, Sermon, SermonPacket, SermonSource


def _content():
    return {
        "titles": ["First Title", "Second Title"],
        "description": "About the vine.",
        "chapters": [{"label": "Start", "start": 0.0, "timestamp": "0:00"}],
        "quotes": [{"text": "The branch does not strain to produce grapes.",
                    "start": 10.0, "timestamp": "0:10", "clip_url": "https://y/1"}],
        "social": [{"platform": "facebook", "body": "Rest is not laziness."}],
    }


@pytest.fixture
def packet(app, church):
    source = SermonSource(church_id=church.id, channel_url="https://y/@x",
                          channel_id="UCtest")
    db.session.add(source); db.session.flush()
    sermon = Sermon(source_id=source.id, church_id=church.id, video_id="v1",
                    title="Staying Connected", series="The Vine", status="ingested",
                    transcript="The branch does not strain to produce grapes.",
                    published_at=datetime.utcnow() - timedelta(days=1))
    db.session.add(sermon); db.session.flush()
    p = SermonPacket(church_id=church.id, sermon_id=sermon.id, status="ready",
                     content=json.dumps(_content()), generated_at=datetime.utcnow())
    db.session.add(p); db.session.commit()
    return p


class TestListing:
    def test_packets_are_listed_with_their_sermon(self, auth_client, packet):
        res = auth_client.get("/api/packets")
        assert res.status_code == 200
        rows = res.get_json()["packets"]
        assert len(rows) == 1
        assert rows[0]["sermon"]["title"] == "Staying Connected"
        assert rows[0]["content"]["titles"] == ["First Title", "Second Title"]

    def test_authentication_is_required(self, client, packet):
        assert client.get("/api/packets").status_code == 401

    def test_another_churchs_packets_are_invisible(self, auth_client, packet, app):
        other = Church(name="Other", billing_exempt=True)
        db.session.add(other); db.session.flush()
        source = SermonSource(church_id=other.id, channel_url="https://y/@o",
                              channel_id="UCother")
        db.session.add(source); db.session.flush()
        sermon = Sermon(source_id=source.id, church_id=other.id, video_id="o1",
                        title="Not Yours", status="ingested",
                        published_at=datetime.utcnow())
        db.session.add(sermon); db.session.flush()
        theirs = SermonPacket(church_id=other.id, sermon_id=sermon.id,
                              status="ready", content=json.dumps(_content()))
        db.session.add(theirs); db.session.commit()
        try:
            titles = [p["sermon"]["title"] for p in
                      auth_client.get("/api/packets").get_json()["packets"]]
            assert "Not Yours" not in titles
            assert auth_client.get(f"/api/packets/{theirs.id}").status_code == 404
        finally:
            SermonPacket.query.filter_by(church_id=other.id).delete()
            Sermon.query.filter_by(church_id=other.id).delete()
            SermonSource.query.filter_by(church_id=other.id).delete()
            Church.query.filter_by(id=other.id).delete()
            db.session.commit()


class TestEditing:
    def test_titles_and_posts_can_be_edited(self, auth_client, packet):
        res = auth_client.patch(f"/api/packets/{packet.id}", json={
            "titles": ["A Better Title"],
            "social": [{"platform": "instagram", "body": "Edited by staff."}],
            "description": "Rewritten.",
        })
        assert res.status_code == 200
        content = json.loads(SermonPacket.query.get(packet.id).content)
        assert content["titles"] == ["A Better Title"]
        assert content["social"][0]["body"] == "Edited by staff."
        assert content["description"] == "Rewritten."

    def test_quotes_cannot_be_edited(self, auth_client, packet):
        """A quote's whole value is that it is provably what was preached."""
        auth_client.patch(f"/api/packets/{packet.id}", json={
            "quotes": [{"text": "Something he never said.", "timestamp": "0:10"}],
        })
        content = json.loads(SermonPacket.query.get(packet.id).content)
        assert content["quotes"][0]["text"] == \
            "The branch does not strain to produce grapes."

    def test_chapters_cannot_be_edited(self, auth_client, packet):
        auth_client.patch(f"/api/packets/{packet.id}", json={
            "chapters": [{"label": "Fake", "timestamp": "9:99"}]})
        content = json.loads(SermonPacket.query.get(packet.id).content)
        assert content["chapters"][0]["label"] == "Start"

    def test_an_empty_post_is_dropped(self, auth_client, packet):
        auth_client.patch(f"/api/packets/{packet.id}",
                          json={"social": [{"platform": "facebook", "body": "  "}]})
        content = json.loads(SermonPacket.query.get(packet.id).content)
        assert content["social"] == []

    def test_editing_another_churchs_packet_is_refused(self, client, packet):
        assert client.patch(f"/api/packets/{packet.id}",
                            json={"titles": ["x"]}).status_code == 401


class TestRegenerate:
    def test_regenerating_replaces_the_content(self, auth_client, packet):
        fresh = _content()
        fresh["titles"] = ["Rebuilt Title"]
        with patch("sermon_packet.build_packet", return_value=fresh):
            res = auth_client.post(f"/api/packets/{packet.id}/regenerate")
        assert res.status_code == 200
        assert json.loads(SermonPacket.query.get(packet.id).content)["titles"] == \
            ["Rebuilt Title"]

    def test_a_sermon_without_a_transcript_cannot_be_rebuilt(self, auth_client, packet):
        sermon = Sermon.query.get(packet.sermon_id)
        sermon.transcript = None
        db.session.commit()
        res = auth_client.post(f"/api/packets/{packet.id}/regenerate")
        assert res.status_code == 400

    def test_a_model_failure_leaves_the_existing_packet_intact(self, auth_client, packet):
        with patch("sermon_packet.build_packet", side_effect=RuntimeError("down")):
            res = auth_client.post(f"/api/packets/{packet.id}/regenerate")
        assert res.status_code == 502
        assert json.loads(SermonPacket.query.get(packet.id).content)["titles"] == \
            ["First Title", "Second Title"]
