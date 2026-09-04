"""Tests for the Monday packet.

The load-bearing property is that a quote reaching a published post was
actually said. A fabricated sentence attributed to a pastor is the one failure
that would cost a church more than the feature is worth, so the verbatim filter
gets the most attention here.
"""

import json
from datetime import datetime
from unittest.mock import patch

import pytest

import sermon_packet as packet
from models import db, Sermon, SermonSource, ContentProfile


TRANSCRIPT = (
    "Good morning church. Today we are looking at the vine and the branches. "
    "Jesus says abide in me and I will abide in you. "
    "You cannot bear fruit on your own strength, and that is good news, not bad news. "
    "The branch does not strain to produce grapes. It simply stays connected. "
    "So the question this morning is not are you working hard enough. "
    "The question is whether you are staying connected."
)

SEGMENTS = [
    {"start": 0.0,  "text": "Good morning church."},
    {"start": 4.5,  "text": "Today we are looking at the vine and the branches."},
    {"start": 11.0, "text": "Jesus says abide in me and I will abide in you."},
    {"start": 18.2, "text": "You cannot bear fruit on your own strength,"},
    {"start": 22.0, "text": "and that is good news, not bad news."},
    {"start": 27.5, "text": "The branch does not strain to produce grapes."},
    {"start": 33.0, "text": "It simply stays connected."},
    {"start": 38.0, "text": "So the question this morning is not are you working hard enough."},
    {"start": 45.0, "text": "The question is whether you are staying connected."},
]


@pytest.fixture
def sermon(app, church):
    source = SermonSource(church_id=church.id, channel_url="https://youtube.com/@x",
                          channel_id="UCtest")
    db.session.add(source)
    db.session.flush()
    s = Sermon(
        source_id=source.id, church_id=church.id, video_id="abc123",
        title="Staying Connected", published_at=datetime(2026, 9, 6, 11, 0),
        transcript=TRANSCRIPT, transcript_segments=json.dumps(SEGMENTS),
        series="The Vine", status="ingested",
    )
    db.session.add(s)
    db.session.commit()
    yield s
    Sermon.query.filter_by(church_id=church.id).delete()
    SermonSource.query.filter_by(church_id=church.id).delete()
    db.session.commit()


def _model_reply(**overrides):
    payload = {
        "titles": ["Are You Working Hard Enough?"],
        "description": "A message about staying connected.",
        "chapters": [{"label": "The vine", "quote": "Today we are looking at the vine and the branches."}],
        "quotes": ["The branch does not strain to produce grapes."],
        "social": [{"platform": "facebook", "body": "A word from Sunday."}],
    }
    payload.update(overrides)
    return json.dumps(payload)


# ── The verbatim guarantee ───────────────────────────────────────────────────

class TestQuoteVerification:
    def test_a_quote_that_was_said_passes(self):
        assert packet.verify_quote(
            "The branch does not strain to produce grapes.", TRANSCRIPT)

    def test_a_quote_that_was_not_said_fails(self):
        assert not packet.verify_quote(
            "God wants you to live your best life now.", TRANSCRIPT)

    def test_a_plausible_paraphrase_still_fails(self):
        """The dangerous case: close enough to look right, not actually said."""
        assert not packet.verify_quote(
            "The branch does not struggle to produce grapes.", TRANSCRIPT)

    def test_punctuation_and_case_differences_are_tolerated(self):
        """Auto-captions differ from written phrasing; rejecting on a comma
        would throw away quotes that genuinely were spoken."""
        assert packet.verify_quote(
            "the branch does NOT strain to produce grapes", TRANSCRIPT)

    def test_curly_apostrophes_are_tolerated(self):
        assert packet.verify_quote("You cannot bear fruit on your own strength",
                                   TRANSCRIPT.replace("cannot", "cannot"))

    def test_a_fragment_too_short_to_be_a_quote_fails(self):
        assert not packet.verify_quote("good news", TRANSCRIPT)

    def test_empty_input_fails_rather_than_raising(self):
        assert not packet.verify_quote("", TRANSCRIPT)
        assert not packet.verify_quote("something", "")


class TestFabricatedQuotesAreDropped:
    def test_invented_quotes_never_reach_the_packet(self, sermon, church):
        reply = _model_reply(quotes=[
            "The branch does not strain to produce grapes.",   # real
            "God helps those who help themselves.",            # invented
            "Your breakthrough is coming this year.",          # invented
        ])
        with patch("sermon_packet.call_gemini", return_value=reply):
            result = packet.build_packet(sermon, church)

        texts = [q["text"] for q in result["quotes"]]
        assert texts == ["The branch does not strain to produce grapes."]
        assert result["quotes_rejected"] == 2

    def test_a_packet_of_entirely_invented_quotes_yields_none(self, sermon, church):
        reply = _model_reply(quotes=["Nothing here was ever said aloud at all."])
        with patch("sermon_packet.call_gemini", return_value=reply):
            result = packet.build_packet(sermon, church)
        assert result["quotes"] == []


# ── Timing ───────────────────────────────────────────────────────────────────

class TestTimestamps:
    def test_a_quote_is_located_in_the_transcript(self):
        start = packet.locate_quote(
            "The branch does not strain to produce grapes.", SEGMENTS)
        assert start == 27.5

    def test_a_quote_spanning_two_caption_pieces_is_located(self):
        """Spoken sentences are split across caption pieces, so matching has to
        work across the join rather than piece by piece."""
        start = packet.locate_quote(
            "You cannot bear fruit on your own strength, and that is good news",
            SEGMENTS)
        assert start == 18.2

    def test_an_absent_quote_reports_no_position(self):
        assert packet.locate_quote("never spoken words here at all", SEGMENTS) == -1.0

    def test_quotes_carry_a_clip_link(self, sermon, church):
        with patch("sermon_packet.call_gemini", return_value=_model_reply()):
            result = packet.build_packet(sermon, church)
        quote = result["quotes"][0]
        assert quote["timestamp"] == "0:27"
        assert quote["clip_url"].endswith("&t=27s")

    def test_timestamp_formatting_crosses_the_hour(self):
        assert packet.format_timestamp(27.5) == "0:27"
        assert packet.format_timestamp(605) == "10:05"
        assert packet.format_timestamp(3725) == "1:02:05"
        assert packet.format_timestamp(-1) == ""


class TestChapters:
    def test_chapters_start_at_zero(self, sermon, church):
        """YouTube renders no chapters at all unless the first is 0:00."""
        with patch("sermon_packet.call_gemini", return_value=_model_reply()):
            result = packet.build_packet(sermon, church)
        assert result["chapters"][0]["timestamp"] == "0:00"

    def test_a_chapter_whose_anchor_is_not_in_the_transcript_is_dropped(self, sermon, church):
        """A guessed timestamp sends viewers to the wrong part of the message."""
        reply = _model_reply(chapters=[
            {"label": "Real", "quote": "It simply stays connected."},
            {"label": "Invented", "quote": "And now a word from our sponsors."},
        ])
        with patch("sermon_packet.call_gemini", return_value=reply):
            result = packet.build_packet(sermon, church)
        labels = [c["label"] for c in result["chapters"]]
        assert "Invented" not in labels
        assert "Real" in labels

    def test_chapters_come_out_in_time_order(self, sermon, church):
        reply = _model_reply(chapters=[
            {"label": "Later", "quote": "The question is whether you are staying connected."},
            {"label": "Earlier", "quote": "Jesus says abide in me and I will abide in you."},
        ])
        with patch("sermon_packet.call_gemini", return_value=reply):
            result = packet.build_packet(sermon, church)
        starts = [c["start"] for c in result["chapters"]]
        assert starts == sorted(starts)


class TestWithoutTimings:
    def test_a_sermon_from_the_video_fallback_still_produces_quotes(self, sermon, church):
        """Sermons transcribed by watching the video have no caption timings.
        Quotes must survive that; chapters cannot and are dropped."""
        sermon.transcript_segments = None
        db.session.commit()
        with patch("sermon_packet.call_gemini", return_value=_model_reply()):
            result = packet.build_packet(sermon, church)

        assert result["quotes"][0]["text"]
        assert result["quotes"][0]["timestamp"] == ""
        assert result["quotes"][0]["clip_url"] is None
        assert result["chapters"] == []
        assert result["has_timings"] is False


# ── House style ──────────────────────────────────────────────────────────────

class TestHouseStyle:
    def test_an_unconfigured_church_gets_the_neutral_default(self, sermon, church):
        with patch("sermon_packet.call_gemini", return_value=_model_reply()) as gem:
            packet.build_packet(sermon, church)
        prompt = gem.call_args.args[0]
        assert "Question" in prompt
        assert "has not set a house style yet" in prompt

    def test_a_church_title_strategy_reaches_the_prompt(self, sermon, church):
        db.session.add(ContentProfile(
            church_id=church.id, title_strategy="scripture_first",
            voice_notes="Plain and pastoral."))
        db.session.commit()
        try:
            with patch("sermon_packet.call_gemini", return_value=_model_reply()) as gem:
                packet.build_packet(sermon, church)
            prompt = gem.call_args.args[0]
            assert "Lead each title with the scripture reference" in prompt
            assert "Plain and pastoral." in prompt
        finally:
            ContentProfile.query.filter_by(church_id=church.id).delete()
            db.session.commit()

    def test_one_church_style_does_not_leak_into_another(self, sermon, church, app):
        """The reason the style is a per-church layer rather than a constant."""
        from models import Church
        other = Church(name="Other Church", billing_exempt=True)
        db.session.add(other)
        db.session.flush()
        db.session.add(ContentProfile(church_id=church.id,
                                      title_strategy="question_caps",
                                      voice_notes="LOUD AND PUNCHY."))
        db.session.commit()
        try:
            with patch("sermon_packet.call_gemini", return_value=_model_reply()) as gem:
                packet.build_packet(sermon, other)
            prompt = gem.call_args.args[0]
            assert "LOUD AND PUNCHY" not in prompt
            assert "FULL CAPITALS" not in prompt
        finally:
            ContentProfile.query.filter_by(church_id=church.id).delete()
            Church.query.filter_by(id=other.id).delete()
            db.session.commit()


class TestFailureModes:
    def test_a_sermon_without_a_transcript_is_refused(self, sermon, church):
        sermon.transcript = None
        db.session.commit()
        with pytest.raises(ValueError):
            packet.build_packet(sermon, church)

    def test_a_non_json_reply_raises_rather_than_saving_junk(self, sermon, church):
        with patch("sermon_packet.call_gemini", return_value="Sure! Here you go:"):
            with pytest.raises(Exception):
                packet.build_packet(sermon, church)

    def test_malformed_entries_are_skipped_not_fatal(self, sermon, church):
        reply = _model_reply(social=["not an object", {"body": ""}, {"body": "Good one."}])
        with patch("sermon_packet.call_gemini", return_value=reply):
            result = packet.build_packet(sermon, church)
        assert [s["body"] for s in result["social"]] == ["Good one."]
