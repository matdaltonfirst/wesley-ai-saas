"""The weekly sermon content review queue: states, versions, and who may do what."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import review
from models import AuditLog, ContentReview, ContentVersion, Sermon, SermonPacket, SermonSource, db
from tests.conftest import login, make_user

CONTENT = {
    "titles": ["IS GOD TOO STRICT?"], "description": "What the Ten Commandments are for.",
    "chapters": [{"label": "Introduction", "start": 0.0, "timestamp": "0:00"}],
    "quotes": [{"text": "Rules are a fence for a garden.", "start": 61.0, "timestamp": "1:01"}],
    "social": [{"platform": "facebook", "body": "Sunday we asked: is God too strict?"}],
    "blog": {"title": "Is God too strict?", "body": "Body text.", "words": 2, "unverified": []},
    "guide": {"document": "Questions for your group."},
}


@pytest.fixture
def packet(church):
    src = SermonSource(channel_url="u", channel_id="c"); db.session.add(src); db.session.flush()
    s = Sermon(source_id=src.id, video_id="vid1", title="Is God too strict?", published_at=datetime.utcnow() - timedelta(days=3),
               status="ingested", transcript="Rules are a fence for a garden.")
    db.session.add(s); db.session.flush()
    p = SermonPacket(sermon_id=s.id, status="ready", content=json.dumps(CONTENT), generated_at=datetime.utcnow())
    db.session.add(p); db.session.commit()
    return p


class TestStates:
    def test_every_piece_with_content_starts_as_draft(self, packet):
        rows = review.ensure_reviews(packet)
        assert set(rows) == {"youtube", "quotes", "social", "blog", "guide"} and {r.status for r in rows.values()} == {"draft"}

    def test_a_piece_with_no_content_has_no_review_row(self, packet):
        c = review.load_content(packet); c.pop("guide"); packet.content = json.dumps(c); db.session.commit()
        assert "guide" not in review.ensure_reviews(packet)

    def test_approving_records_who_and_when(self, packet):
        u = make_user("p@daltonfumc.com", ["pastoral"])
        row = review.decide(packet, "blog", "approved", u)
        assert row.status == "approved" and row.decided_by_id == u.id and row.decided_at is not None

    def test_asking_for_changes_needs_a_reason(self, packet):
        with pytest.raises(review.ReviewError, match="what needs to change"):
            review.decide(packet, "blog", "needs_changes", None, "  ")
        assert review.decide(packet, "blog", "needs_changes", None, "Too long.").note == "Too long."

    def test_an_edit_voids_the_approval_and_keeps_the_old_words(self, packet):
        review.decide(packet, "social", "approved", make_user("p@daltonfumc.com", ["pastoral"]))
        old = review.load_content(packet)
        new = json.loads(json.dumps(old)); new["social"][0]["body"] = "A different post."
        packet.content = json.dumps(new); db.session.commit()
        changed = review.record_edit(packet, old, new, None)
        assert changed == ["social"]
        assert ContentReview.query.filter_by(piece="social").one().status == "draft"
        v = ContentVersion.query.filter_by(piece="social").one()
        assert v.reason == "edited" and "Sunday we asked" in v.content

    def test_an_edit_to_one_piece_leaves_other_approvals_alone(self, packet):
        u = make_user("p@daltonfumc.com", ["pastoral"])
        review.decide(packet, "blog", "approved", u); review.decide(packet, "guide", "approved", u)
        old = review.load_content(packet); new = json.loads(json.dumps(old)); new["blog"]["body"] = "Changed."
        review.record_edit(packet, old, new, None)
        assert {r.piece: r.status for r in ContentReview.query} == {"blog": "draft", "guide": "approved"}

    def test_the_overall_status_is_approved_only_when_every_piece_is(self, packet):
        u = make_user("p@daltonfumc.com", ["pastoral"])
        rows = review.ensure_reviews(packet)
        for piece in list(rows)[:-1]:
            review.decide(packet, piece, "approved", u)
        assert review.overall_status({r.piece: r for r in ContentReview.query}) == "in_review"
        review.decide(packet, list(rows)[-1], "approved", u)
        assert review.overall_status({r.piece: r for r in ContentReview.query}) == "approved"
        review.decide(packet, "quotes", "needs_changes", u, "Pick stronger ones.")
        assert review.overall_status({r.piece: r for r in ContentReview.query}) == "needs_changes"


class TestRegenerate:
    def test_regenerating_keeps_the_old_text_as_a_version_and_goes_back_to_draft(self, packet):
        review.decide(packet, "blog", "approved", make_user("p@daltonfumc.com", ["pastoral"]))
        with patch("sermon_longform.build_blog", return_value={"title": "New", "body": "Fresh words.", "words": 2, "unverified": []}):
            fields = review.regenerate_piece(packet, "blog", None)
        assert fields["blog"]["body"] == "Fresh words."
        assert ContentReview.query.filter_by(piece="blog").one().status == "draft"
        assert json.loads(ContentVersion.query.filter_by(piece="blog").one().content)["blog"]["body"] == "Body text."

    def test_a_failed_regeneration_changes_nothing(self, packet):
        before = packet.content
        with patch("sermon_longform.build_blog", side_effect=RuntimeError("model down")):
            with pytest.raises(review.ReviewError, match="untouched"):
                review.regenerate_piece(packet, "blog", None)
        assert packet.content == before and ContentVersion.query.count() == 0

    def test_an_empty_rebuild_is_refused_and_keeps_the_old_text(self, packet):
        before = packet.content
        with patch("sermon_longform.build_blog", return_value={}):
            with pytest.raises(review.ReviewError, match="empty"):
                review.regenerate_piece(packet, "blog", None)
        assert packet.content == before

    def test_social_regeneration_takes_only_the_social_part_of_the_rebuild(self, packet):
        rebuilt = {"titles": ["SOMETHING ELSE?"], "description": "x", "chapters": [], "quotes": [], "social": [{"platform": "facebook", "body": "New post"}]}
        with patch("sermon_packet.build_packet", return_value=rebuilt):
            review.regenerate_piece(packet, "social", None)
        c = review.load_content(packet)
        assert c["social"][0]["body"] == "New post" and c["titles"] == ["IS GOD TOO STRICT?"] and c["quotes"][0]["text"].startswith("Rules")

    def test_a_version_can_be_restored_and_what_was_there_is_kept_too(self, packet):
        with patch("sermon_longform.build_blog", return_value={"title": "New", "body": "Fresh words.", "words": 2, "unverified": []}):
            review.regenerate_piece(packet, "blog", None)
        old = ContentVersion.query.filter_by(piece="blog").one()
        review.restore_version(old, packet, None)
        assert review.load_content(packet)["blog"]["body"] == "Body text."
        assert ContentVersion.query.filter_by(piece="blog").count() == 2

    def test_only_the_last_ten_versions_are_kept(self, packet):
        for n in range(14):
            with patch("sermon_longform.build_blog", return_value={"title": "T", "body": f"Draft {n}", "words": 2, "unverified": []}):
                review.regenerate_piece(packet, "blog", None)
        assert ContentVersion.query.filter_by(piece="blog").count() == review.MAX_VERSIONS_PER_PIECE


class TestRoutes:
    def as_(self, client, roles, email="x@daltonfumc.com"):
        login(client, make_user(email, roles)); return client

    def test_reading_needs_sunday_read(self, client, packet):
        self.as_(client, ["music"])
        assert client.get("/api/review").status_code == 403 and client.get("/review").status_code == 403

    def test_the_queue_lists_the_sermon_and_each_pieces_status(self, client, packet):
        self.as_(client, ["comms"])
        item = client.get("/api/review").get_json()["items"][0]
        assert item["sermon"]["title"] == "Is God too strict?" and item["status"] == "in_review" and set(item["pieces"]) >= {"blog", "social"}

    def test_the_detail_says_nothing_is_published(self, client, packet):
        self.as_(client, ["comms"])
        d = client.get(f"/api/review/{packet.id}").get_json()
        assert "Nothing here is published or sent" in d["notice"] and [p["piece"] for p in d["pieces"]][0] == "youtube"
        quotes = next(p for p in d["pieces"] if p["piece"] == "quotes")
        assert quotes["editable"] is False

    def test_comms_can_approve_and_it_is_audited(self, client, packet):
        self.as_(client, ["comms"])
        r = client.post(f"/api/review/{packet.id}/blog/decision", json={"status": "approved"})
        assert r.status_code == 200 and AuditLog.query.filter_by(action="review.approved").count() == 1

    def test_the_admin_assistant_can_read_but_not_approve_edit_or_regenerate(self, client, packet):
        self.as_(client, ["admin_assistant"])
        assert client.get("/api/review").status_code == 200
        assert client.post(f"/api/review/{packet.id}/blog/decision", json={"status": "approved"}).status_code == 403
        assert client.post(f"/api/review/{packet.id}/blog/regenerate").status_code == 403
        assert client.patch(f"/api/packets/{packet.id}", json={"description": "x"}).status_code == 403

    def test_pastoral_can_approve_but_not_edit(self, client, packet):
        self.as_(client, ["pastoral"])
        assert client.post(f"/api/review/{packet.id}/guide/decision", json={"status": "approved"}).status_code == 200
        assert client.patch(f"/api/packets/{packet.id}", json={"description": "x"}).status_code == 403

    def test_editing_through_the_existing_editor_voids_the_approval(self, client, packet):
        self.as_(client, ["comms"])
        client.post(f"/api/review/{packet.id}/youtube/decision", json={"status": "approved"})
        assert client.patch(f"/api/packets/{packet.id}", json={"description": "A new description."}).status_code == 200
        assert ContentReview.query.filter_by(piece="youtube").one().status == "draft"
        assert ContentVersion.query.filter_by(piece="youtube").count() == 1

    def test_changes_requested_needs_a_note_over_the_api_too(self, client, packet):
        self.as_(client, ["comms"])
        r = client.post(f"/api/review/{packet.id}/blog/decision", json={"status": "needs_changes", "note": ""})
        assert r.status_code == 400 and "what needs to change" in r.get_json()["error"]

    def test_unknown_pieces_and_statuses_are_refused(self, client, packet):
        self.as_(client, ["comms"])
        assert client.post(f"/api/review/{packet.id}/podcast/decision", json={"status": "approved"}).status_code == 400
        assert client.post(f"/api/review/{packet.id}/blog/decision", json={"status": "published"}).status_code == 400

    def test_there_is_no_publish_endpoint(self, app):
        assert not [r.rule for r in app.url_map.iter_rules() if "publish" in r.rule and ("review" in r.rule or "packet" in r.rule)]
