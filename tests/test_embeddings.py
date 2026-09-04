"""Tests for semantic retrieval.

The bug this exists to fix: a visitor asks "do you have childcare?" and the
church's page says "Nursery available for infants through age 3". Those share
no words, so keyword scoring returns nothing and the bot says it doesn't know —
on one of the most common questions a church website gets.

The fake embedder below maps a handful of words onto a tiny concept space, so
these tests exercise the real caching, thresholding, and fallback logic without
a network call. It is not a model: it only has to make paraphrases close and
unrelated topics far apart.
"""

from unittest.mock import patch

import pytest

import embeddings
from documents import find_relevant_chunks, find_relevant_chunks_by_keyword
from embeddings import (
    TASK_DOCUMENT, TASK_QUERY, embed_texts, rank_chunks, warm_chunks,
)
from models import db, EmbeddingCache

# word -> concept axis. Words on the same axis are paraphrases of each other.
_CONCEPTS = {
    "childcare": 0, "nursery": 0, "infants": 0, "toddler": 0,
    "worship": 1, "service": 1, "sunday": 1,
    "giving": 2, "tithe": 2, "donate": 2,
    "baptize": 3, "baptism": 3,
}
_DIMS = 4


def _fake_vectors(texts, task, usage=None):
    """Deterministic stand-in for the Gemini embedding API."""
    out = []
    for text in texts:
        vec = [0.0] * _DIMS
        lowered = text.lower()
        for word, axis in _CONCEPTS.items():
            if word in lowered:
                vec[axis] += 1.0
        if not any(vec):
            vec = [0.001] * _DIMS  # unrelated text: near-orthogonal to everything
        out.append(embeddings._normalise(vec))
    if usage is not None:
        usage["calls"] = usage.get("calls", 0) + 1
    return out


@pytest.fixture
def semantic(app):
    """Enable semantic retrieval with the fake embedder, and clear its cache."""
    EmbeddingCache.query.delete()
    db.session.commit()
    with patch.object(embeddings, "is_enabled", return_value=True), \
         patch.object(embeddings, "_embed_via_api", side_effect=_fake_vectors):
        yield
    EmbeddingCache.query.delete()
    db.session.commit()


def _chunk(content, source="Page", location="https://church.org/p"):
    return {"content": content, "source": source, "location": location}


# ── Vector mechanics ─────────────────────────────────────────────────────────

class TestVectorMechanics:
    def test_normalise_produces_a_unit_vector(self):
        vec = embeddings._normalise([3.0, 4.0])
        assert embeddings.similarity(vec, vec) == pytest.approx(1.0, abs=1e-6)

    def test_blob_round_trip_preserves_values(self):
        vec = embeddings._normalise([0.5, 0.25, 0.125])
        restored = embeddings._from_blob(embeddings._to_blob(vec))
        assert embeddings.similarity(vec, restored) == pytest.approx(1.0, abs=1e-6)

    def test_mismatched_dimensions_score_zero_rather_than_raising(self):
        assert embeddings.similarity(
            embeddings._normalise([1.0, 0.0]),
            embeddings._normalise([1.0, 0.0, 0.0]),
        ) == 0.0


# ── Caching ──────────────────────────────────────────────────────────────────

class TestEmbeddingCache:
    def test_second_call_is_served_from_cache(self, semantic):
        texts = ["nursery available for infants"]
        embed_texts(texts, task=TASK_DOCUMENT)
        with patch.object(embeddings, "_embed_via_api") as api:
            embed_texts(texts, task=TASK_DOCUMENT)
        api.assert_not_called()

    def test_query_and_document_vectors_are_cached_separately(self, semantic):
        """Gemini embeds the same text differently for the two task types;
        collapsing them into one cache entry would silently degrade ranking."""
        embed_texts(["baptism"], task=TASK_DOCUMENT)
        embed_texts(["baptism"], task=TASK_QUERY)
        tasks = {row.task for row in EmbeddingCache.query.all()}
        assert tasks == {TASK_DOCUMENT, TASK_QUERY}

    def test_repeated_text_in_one_batch_is_embedded_once(self, semantic):
        """Church sites repeat boilerplate across every page."""
        with patch.object(embeddings, "_embed_via_api", side_effect=_fake_vectors) as api:
            embed_texts(["same text", "same text", "same text"], task=TASK_DOCUMENT)
        assert api.call_count == 1
        assert len(api.call_args.args[0]) == 1

    def test_api_failure_returns_none_rather_than_raising(self, semantic):
        with patch.object(embeddings, "_embed_via_api",
                          side_effect=RuntimeError("embedding service down")):
            assert embed_texts(["anything"], task=TASK_DOCUMENT) is None


# ── Ranking ──────────────────────────────────────────────────────────────────

class TestSemanticRanking:
    def test_paraphrase_is_found_where_keywords_fail(self, semantic):
        """The headline case: childcare/nursery share no words."""
        chunks = [
            _chunk("Nursery available for infants through age 3."),
            _chunk("Giving and tithe options are listed online."),
        ]
        warm_chunks(chunks)

        assert find_relevant_chunks_by_keyword("do you have childcare?", chunks) == []

        ranked = rank_chunks("do you have childcare?", chunks)
        assert ranked is not None
        assert len(ranked) == 1
        assert "Nursery" in ranked[0][1]["content"]

    def test_unrelated_content_stays_below_the_threshold(self, semantic):
        chunks = [_chunk("Giving and tithe options are listed online.")]
        warm_chunks(chunks)
        assert rank_chunks("do you have childcare?", chunks) == []

    def test_near_miss_chunks_are_dropped_by_the_relative_band(self, semantic):
        """Only the best match and its close peers survive — a chunk that is
        merely in the same topic area does not ride along."""
        # Vectors are normalised, so relevance is direction, not magnitude:
        # the near-miss has to point partly at another concept to sit below the
        # best match rather than exactly on top of it.
        best = _chunk("Nursery for infants.")                    # childcare only
        near = _chunk("Nursery hours and tithe envelopes.")       # childcare + giving
        warm_chunks([best, near])
        kept = rank_chunks("childcare", [best, near])
        assert [c["content"] for _, c in kept] == [best["content"]]


class TestCalibration:
    """The relevance constants were measured against gemini-embedding-001 on
    real church-page text, where unanswerable questions still scored 0.55-0.57
    against every page and correct matches scored 0.68-0.71. These pin the
    conclusions so a later tweak cannot quietly reopen the gap.
    """

    def test_floor_sits_between_the_noise_ceiling_and_real_matches(self):
        assert 0.57 < embeddings.SIMILARITY_FLOOR < 0.68

    def test_band_is_tight_enough_to_exclude_a_second_place_topic_match(self):
        # "Do you have childcare?" scored nursery 0.68, kids-classes 0.63.
        # The band must exclude 0.63 as a fraction of 0.68.
        assert 0.63 < embeddings.RELATIVE_BAND * 0.68

    def test_band_is_loose_enough_to_keep_a_genuine_multi_match(self):
        # "What is there for my kids?" scored 0.68 / 0.67 / 0.64 across three
        # pages that should all be returned.
        assert embeddings.RELATIVE_BAND * 0.68 <= 0.64

    def test_denominational_bar_is_gentler_than_the_general_one(self):
        assert embeddings.SIMILARITY_FLOOR_DENOMINATION < embeddings.SIMILARITY_FLOOR
        assert embeddings.RELATIVE_BAND_DENOMINATION < embeddings.RELATIVE_BAND

    def test_scores_are_positive_integers(self, semantic):
        """Downstream code checks score > 0 and the citation floor sorts on it."""
        chunks = [_chunk("Nursery available for infants.")]
        warm_chunks(chunks)
        score, _ = rank_chunks("childcare?", chunks)[0]
        assert isinstance(score, int) and score > 0

    def test_a_cold_corpus_falls_back_instead_of_blocking(self, semantic):
        """Nothing warmed yet: the request must not embed the corpus inline."""
        chunks = [_chunk("Nursery available for infants through age 3.")]
        with patch.object(embeddings, "_embed_via_api", side_effect=_fake_vectors) as api:
            assert rank_chunks("childcare?", chunks) is None
        api.assert_not_called()

    def test_partially_warmed_corpus_falls_back(self, semantic):
        """Ranking a half-embedded corpus would bury every chunk without a
        vector, which is worse than ranking none of it semantically."""
        warmed = _chunk("Nursery available for infants.")
        warm_chunks([warmed])
        assert rank_chunks("childcare?", [warmed, _chunk("Brand new page")]) is None


# ── Fallback ─────────────────────────────────────────────────────────────────

class TestKeywordFallback:
    def test_disabled_embeddings_use_keyword_scoring(self, app):
        chunks = [_chunk("Worship service times", source="Worship Times")]
        with patch.object(embeddings, "is_enabled", return_value=False):
            semantic_result = find_relevant_chunks("worship times", chunks)
        assert semantic_result == find_relevant_chunks_by_keyword("worship times", chunks)
        assert semantic_result

    def test_kill_switch_env_var_disables_semantic_retrieval(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "present")
        monkeypatch.setenv("EMBEDDINGS_ENABLED", "0")
        assert embeddings.is_enabled() is False

    def test_missing_api_key_disables_semantic_retrieval(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("EMBEDDINGS_ENABLED", raising=False)
        assert embeddings.is_enabled() is False

    def test_semantic_finding_nothing_falls_through_to_keywords(self, semantic):
        """A miscalibrated floor must cost the paraphrase win, not the answer.
        Semantic ranking is strictly additive: it can never remove recall that
        keyword scoring would have found."""
        chunks = [_chunk("Worship service times", source="Worship Times")]
        warm_chunks(chunks)
        with patch.object(embeddings, "rank_chunks", return_value=[]):
            result = find_relevant_chunks("worship times", chunks)
        assert result == find_relevant_chunks_by_keyword("worship times", chunks)
        assert result

    def test_embedding_error_mid_request_still_returns_context(self, semantic):
        """An outage must cost latency, never the answer."""
        chunks = [_chunk("Worship service times", source="Worship Times")]
        warm_chunks(chunks)
        with patch.object(embeddings, "_embed_via_api",
                          side_effect=RuntimeError("service down")):
            result = find_relevant_chunks("worship times", chunks)
        assert result  # keyword scoring answered instead


# ── Warming ──────────────────────────────────────────────────────────────────

class TestWarming:
    def test_warm_reports_only_newly_embedded_chunks(self, semantic):
        chunks = [_chunk("Nursery available"), _chunk("Tithe options")]
        assert warm_chunks(chunks) == 2
        assert warm_chunks(chunks) == 0

    def test_warm_is_a_no_op_when_disabled(self, app):
        with patch.object(embeddings, "is_enabled", return_value=False):
            assert warm_chunks([_chunk("Nursery available")]) == 0


class TestCachePruning:
    def test_vectors_for_deleted_text_are_removed(self, semantic):
        """A re-crawl mints a new entry per edited page; without pruning the
        table grows with the site's history rather than its size."""
        current = _chunk("Nursery available for infants.")
        removed = _chunk("A page that has since been deleted.")
        warm_chunks([current, removed])
        assert EmbeddingCache.query.count() == 2

        embeddings.prune_cache(embeddings.chunk_hashes([current]))
        remaining = EmbeddingCache.query.all()
        assert len(remaining) == 1
        assert remaining[0].text_hash == embeddings._text_hash(current["content"])

    def test_live_vectors_survive_pruning(self, semantic):
        chunks = [_chunk("Nursery available."), _chunk("Tithe options.")]
        warm_chunks(chunks)
        embeddings.prune_cache(embeddings.chunk_hashes(chunks))
        assert EmbeddingCache.query.count() == 2

    def test_recent_query_vectors_are_kept(self, semantic):
        embed_texts(["do you have childcare?"], task=TASK_QUERY)
        embeddings.prune_cache(set())
        assert EmbeddingCache.query.filter_by(task=TASK_QUERY).count() == 1

    def test_old_query_vectors_are_aged_out(self, semantic):
        from datetime import datetime, timedelta
        embed_texts(["do you have childcare?"], task=TASK_QUERY)
        row = EmbeddingCache.query.filter_by(task=TASK_QUERY).one()
        row.created_at = datetime.utcnow() - timedelta(days=90)
        db.session.commit()

        embeddings.prune_cache(set())
        assert EmbeddingCache.query.filter_by(task=TASK_QUERY).count() == 0


# ── End to end ───────────────────────────────────────────────────────────────

class TestSemanticRetrievalThroughTheWidget:
    def test_widget_answers_a_paraphrased_question(self, semantic, client, church):
        web_pages = [
            _chunk("Nursery available for infants through age 3.", source="Families"),
            _chunk("Giving and tithe options are listed online.", source="Give"),
        ]
        warm_chunks(web_pages)

        with patch("routes.widget.load_chatbot_documents", return_value=[]), \
             patch("routes.widget.load_curated_content", return_value=[]), \
             patch("routes.widget.load_church_web_content", return_value=web_pages), \
             patch("routes.widget.call_gemini") as gemini:
            gemini.return_value = "Yes, we have a nursery. [1]"
            res = client.post("/api/widget/chat", json={
                "church_id": church.id, "question": "Do you have childcare?",
            })

        assert res.status_code == 200
        model_context = gemini.call_args.args[1]
        assert "Nursery" in model_context
        assert "tithe" not in model_context.lower()
