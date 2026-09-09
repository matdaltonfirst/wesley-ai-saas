"""The weekly article and the small group guide.

The property that matters most is the one the packet already enforces for
quotes: nothing published under the church's name may contain a sentence the
preacher did not say. These pieces are long-form prose, so the filter runs over
the whole body rather than over a list of quotes.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from models import db, Sermon, SermonSource
import sermon_longform
from sermon_longform import (BLOG_MIN_WORDS, MIN_FLAGGED_WORDS, build_blog,
                             build_guide, build_longform, unverified_spans,
                             _split_title)


TRANSCRIPT = (
    "I am the vine, you are the branches. The branch does not strain to produce "
    "grapes. It simply stays connected to the vine. So many of us are exhausted "
    "because we have been trying to bear fruit while disconnected from the "
    "source. Busyness is not the same thing as fruitfulness. Rest is not "
    "laziness; rest is trust. " * 3
)


@pytest.fixture
def sermon(app, church):
    src = SermonSource(church_id=church.id, channel_url="https://y/@x", channel_id="UCx")
    db.session.add(src); db.session.flush()
    s = Sermon(source_id=src.id, church_id=church.id, video_id="v1",
               title="Staying Connected", series="The Vine", status="ingested",
               transcript=TRANSCRIPT,
               published_at=datetime.utcnow() - timedelta(days=1))
    db.session.add(s); db.session.commit()
    return s


def _article(words=900, extra=""):
    body = "## A Section That Carries An Idea\n\n" + ("word " * words)
    return "# Rest Is Not Laziness: What The Vine Teaches\n\n" + body + extra


class TestTitleSplit:
    def test_heading_is_separated_from_the_body(self):
        title, body = _split_title("# The Title\n\nThe body text.")
        assert title == "The Title"
        assert body == "The body text."

    def test_code_fences_are_stripped(self):
        title, _ = _split_title("```markdown\n# Fenced\n\nBody.\n```")
        assert title == "Fenced"

    def test_bracketed_citations_are_removed(self):
        """The shared retrieval path can prepend citation instructions."""
        _, body = _split_title("# T\n\nGrace is unfair [1]. It always was [2].")
        assert "[1]" not in body and "[2]" not in body


class TestBlog:
    def test_a_clean_article_comes_back_whole(self, sermon, church):
        with patch.object(sermon_longform, "call_gemini", return_value=_article()):
            blog = build_blog(sermon, church)
        assert blog["title"] == "Rest Is Not Laziness: What The Vine Teaches"
        assert blog["words"] >= BLOG_MIN_WORDS

    def test_a_short_article_is_rejected(self, sermon, church):
        with patch.object(sermon_longform, "call_gemini", return_value=_article(words=40)):
            with pytest.raises(ValueError, match="too short"):
                build_blog(sermon, church)

    def test_a_real_quotation_is_allowed(self, sermon, church):
        real = _article(extra='\n\nAs it was put: "Busyness is not the same thing as fruitfulness."')
        with patch.object(sermon_longform, "call_gemini", return_value=real):
            assert build_blog(sermon, church)["words"] >= BLOG_MIN_WORDS

    def test_an_unverifiable_quotation_is_flagged_not_discarded(self, sermon, church):
        """An article is a draft a person edits, and the passage a sermon is
        about is usually scripture rather than the preacher's own words. So the
        quotation is surfaced for checking rather than costing 1,600 words."""
        fake = _article(extra='\n\nAs it is written, "God helps those who help themselves, always."')
        with patch.object(sermon_longform, "call_gemini", return_value=fake):
            blog = build_blog(sermon, church)
        assert blog["words"] >= BLOG_MIN_WORDS
        assert any("God helps those" in q for q in blog["unverified"])

    def test_a_verbatim_quotation_is_not_flagged(self, sermon, church):
        real = _article(extra='\n\nAs it was put: "Busyness is not the same thing as fruitfulness."')
        with patch.object(sermon_longform, "call_gemini", return_value=real):
            assert build_blog(sermon, church)["unverified"] == []

    def test_a_short_fragment_is_not_flagged(self, sermon, church):
        """Three words in quotes is emphasis, not an attributed quotation."""
        frag = _article(extra='\n\nHe called it "scorching heat" and moved on.')
        with patch.object(sermon_longform, "call_gemini", return_value=frag):
            assert build_blog(sermon, church)["unverified"] == []

    def test_a_sermon_without_a_transcript_is_refused(self, sermon, church):
        sermon.transcript = None; db.session.commit()
        with pytest.raises(ValueError, match="no transcript"):
            build_blog(sermon, church)


def _guide(**over):
    base = {
        "scripture": "John 15",
        "summary": "A short setup for the leader to read aloud.",
        "opening": "When do you feel most rushed?",
        "digging_in": ["What does the vine image suggest?", "What does the branch do?"],
        "applying": ["Where are you striving rather than abiding?"],
        "prayer": "Pray for rest this week.",
        "challenge": "Take ten minutes before anything else.",
    }
    base.update(over)
    import json
    return json.dumps(base)


class TestGuide:
    def test_a_clean_guide_parses(self, sermon, church):
        with patch.object(sermon_longform, "call_gemini", return_value=_guide()):
            g = build_guide(sermon, church)
        assert g["scripture"] == "John 15"
        assert len(g["digging_in"]) == 2 and len(g["applying"]) == 1

    def test_question_counts_are_capped(self, sermon, church):
        many = _guide(digging_in=["q%d" % i for i in range(9)])
        with patch.object(sermon_longform, "call_gemini", return_value=many):
            assert len(build_guide(sermon, church)["digging_in"]) == 4

    def test_a_guide_with_no_questions_is_rejected(self, sermon, church):
        with patch.object(sermon_longform, "call_gemini",
                          return_value=_guide(digging_in=[], applying=[])):
            with pytest.raises(ValueError, match="no questions"):
                build_guide(sermon, church)

    def test_an_unverifiable_quotation_is_flagged(self, sermon, church):
        bad = _guide(opening='He said "Nobody ever preached this sentence at all".')
        with patch.object(sermon_longform, "call_gemini", return_value=bad):
            g = build_guide(sermon, church)
        assert any("Nobody ever preached" in q for q in g["unverified"])


class TestIsolation:
    def test_a_failed_guide_does_not_cost_the_article(self, sermon, church):
        """Each piece is a separate call and must fail on its own."""
        def fake(prompt, *a, **k):
            if "discussion guide" in prompt:
                raise RuntimeError("model refused")
            return _article()
        with patch.object(sermon_longform, "call_gemini", side_effect=fake):
            out = build_longform(sermon, church)
        assert out["blog"] is not None
        assert out["guide"] is None
        assert "model refused" in out["guide_error"]

    def test_a_failed_article_does_not_cost_the_guide(self, sermon, church):
        def fake(prompt, *a, **k):
            if "weekly article" in prompt:
                raise RuntimeError("model refused")
            return _guide()
        with patch.object(sermon_longform, "call_gemini", side_effect=fake):
            out = build_longform(sermon, church)
        assert out["guide"] is not None
        assert out["blog"] is None


class TestFlagging:
    """The packet's quotes and social posts keep their hard filter; only the
    long-form drafts trade it for a visible warning."""

    def test_short_spans_are_ignored(self):
        assert unverified_spans('He said "not there" today.', "unrelated text") == []

    def test_a_full_sentence_is_reported(self):
        spans = unverified_spans('She wrote "this sentence appears nowhere at all".',
                                 "unrelated text")
        assert spans == ["this sentence appears nowhere at all"]

    def test_a_verbatim_sentence_is_not_reported(self):
        t = "Busyness is not the same thing as fruitfulness."
        assert unverified_spans('As put: "%s"' % t, t) == []

    def test_the_threshold_is_the_documented_one(self):
        short = " ".join(["word"] * (MIN_FLAGGED_WORDS - 1))
        long_ = " ".join(["word"] * MIN_FLAGGED_WORDS)
        assert unverified_spans('x "%s" y' % short, "nothing") == []
        assert unverified_spans('x "%s" y' % long_, "nothing") == [long_]
