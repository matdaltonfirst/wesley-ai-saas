"""Semantic retrieval — embedding, caching, and similarity ranking.

Keyword scoring cannot match a question to an answer that uses different
words: a visitor asking "do you have childcare?" scores zero against a page
reading "Nursery available for infants through age 3". Embeddings close that
gap, and this module is the whole of it.

Two rules govern everything here:

1. **Never lose an answer to an embedding failure.** Every entry point returns
   None rather than raising, and the caller falls back to keyword scoring —
   which is exactly the behaviour that shipped before this module existed.
2. **Never block a request on bulk embedding.** A church's corpus is warmed by
   a scheduled job. Until it is fully warmed, retrieval stays on keyword
   scoring rather than ranking a half-embedded corpus, which would quietly
   bury every chunk that has no vector yet.
"""

import array
import hashlib
import logging
import os
import threading

from models import db, EmbeddingCache

log = logging.getLogger("wesley")

EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
# 768 rather than the 3072 default: a quarter of the storage and of the
# dot-product work, for a difference in retrieval quality that does not show up
# at the scale of one church's website.
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

# Gemini embeds queries and documents differently; using the matching task type
# for each is worth more than any amount of threshold tuning.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"

# ── Relevance tuning ─────────────────────────────────────────────────────────
#
# Calibrated against gemini-embedding-001 on real church-page text, not guessed.
# The absolute similarity scale is compressed and offset: on a sample of church
# pages, genuinely unanswerable questions ("do you sell insurance?") still
# scored 0.55–0.57 against every page, while correct matches scored 0.68–0.71.
# So a flat threshold anywhere in that band either admits everything or nothing.
#
# Two tests instead, which separated signal from noise perfectly on that sample:
#
#   SIMILARITY_FLOOR  the best match must clear this, or nothing here is really
#                     about the question. This is the guard against handing the
#                     model four irrelevant pages and inviting it to invent an
#                     answer from them.
#   RELATIVE_BAND     keep only chunks within this fraction of the best score.
#                     When a real answer exists it stands out; when none does,
#                     everything clusters — which is what this measures.
#
# Longer real-world pages compress similarities further, so the failure mode of
# a too-high floor is falling back to keyword scoring, never going mute.
SIMILARITY_FLOOR = float(os.getenv("EMBED_SIMILARITY_FLOOR", "0.60"))
RELATIVE_BAND = float(os.getenv("EMBED_RELATIVE_BAND", "0.94"))

# The denominational layer turns on a single decisive concept and is guaranteed
# a citation slot anyway, so it is held to a slightly gentler bar.
SIMILARITY_FLOOR_DENOMINATION = float(
    os.getenv("EMBED_SIMILARITY_FLOOR_DENOMINATION", "0.58"))
RELATIVE_BAND_DENOMINATION = float(
    os.getenv("EMBED_RELATIVE_BAND_DENOMINATION", "0.90"))

# How many texts to send in one embed_content call.
BATCH_SIZE = 64

_client_lock = threading.Lock()
_client = None


def is_enabled() -> bool:
    """Whether semantic retrieval should be attempted at all.

    EMBEDDINGS_ENABLED=0 is the kill switch: it reverts every church to keyword
    scoring without a deploy.
    """
    if os.getenv("EMBEDDINGS_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    return bool(os.getenv("GEMINI_API_KEY"))


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from google import genai
            _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        return _client


# ── Vector encoding ──────────────────────────────────────────────────────────

def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalise(values) -> array.array:
    """L2-normalise so similarity is a dot product rather than a full cosine."""
    vec = array.array("f", values)
    magnitude = sum(v * v for v in vec) ** 0.5
    if magnitude:
        vec = array.array("f", (v / magnitude for v in vec))
    return vec


def _to_blob(vec: array.array) -> bytes:
    return vec.tobytes()


def _from_blob(blob: bytes) -> array.array:
    vec = array.array("f")
    vec.frombytes(blob)
    return vec


def similarity(a: array.array, b: array.array) -> float:
    """Dot product of two normalised vectors, i.e. their cosine similarity."""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ── Embedding with cache ─────────────────────────────────────────────────────

def _cached(hashes, task):
    rows = EmbeddingCache.query.filter(
        EmbeddingCache.text_hash.in_(list(hashes)),
        EmbeddingCache.model == EMBED_MODEL,
        EmbeddingCache.task == task,
    ).all()
    return {row.text_hash: _from_blob(row.vector) for row in rows}


def _embed_via_api(texts, task, usage=None):
    """Call Gemini for a batch of texts. Raises on failure; callers catch."""
    from google.genai import types

    response = _get_client().models.embed_content(
        model=EMBED_MODEL,
        contents=list(texts),
        config=types.EmbedContentConfig(
            task_type=task, output_dimensionality=EMBED_DIM,
        ),
    )
    vectors = [_normalise(e.values) for e in (response.embeddings or [])]
    if len(vectors) != len(texts):
        raise ValueError(
            f"embedding count mismatch: asked {len(texts)}, got {len(vectors)}")
    if usage is not None:
        # Embedding is billed on input only; count characters/4 as a token
        # estimate when the API reports no token statistics.
        usage["calls"] = usage.get("calls", 0) + 1
        usage["tokens"] = usage.get("tokens", 0) + sum(len(t) for t in texts) // 4
    return vectors


def embed_texts(texts, task=TASK_DOCUMENT, allow_api=True, usage=None):
    """Return a vector per text, or None if embedding is unavailable.

    Cached vectors are returned without an API call. Misses are embedded in
    batches and cached, unless *allow_api* is False — the read-only mode used
    on the request path, so a cold corpus never blocks a visitor's question.
    """
    if not texts or not is_enabled():
        return None

    hashes = [_text_hash(t) for t in texts]
    try:
        found = _cached(set(hashes), task)
    except Exception:
        log.exception("[EMBED] cache lookup failed")
        return None

    missing = [t for t, h in zip(texts, hashes) if h not in found]
    if missing and not allow_api:
        return None

    if missing:
        # Deduplicate within the batch: a church site repeats boilerplate.
        unique = list(dict.fromkeys(missing))
        try:
            for start in range(0, len(unique), BATCH_SIZE):
                batch = unique[start:start + BATCH_SIZE]
                vectors = _embed_via_api(batch, task, usage=usage)
                for text, vec in zip(batch, vectors):
                    found[_text_hash(text)] = vec
                    db.session.add(EmbeddingCache(
                        text_hash=_text_hash(text), model=EMBED_MODEL,
                        task=task, dim=len(vec), vector=_to_blob(vec),
                    ))
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("[EMBED] embedding failed; falling back to keywords")
            return None

    return [found.get(h) for h in hashes]


def embed_query(question: str, usage=None):
    """Embed one question, or return None if embedding is unavailable."""
    vectors = embed_texts([question], task=TASK_QUERY, usage=usage)
    return vectors[0] if vectors else None


# ── Ranking ──────────────────────────────────────────────────────────────────

def rank_chunks(question, chunks, top_n=8, floor=None, band=None, usage=None):
    """Score *chunks* against *question* semantically, or return None.

    None means "semantic retrieval is not available for this request" — no key,
    kill switch off, an API error, or a corpus that has not been warmed yet.
    An empty list means it ran and found nothing relevant. Callers fall back to
    keyword scoring on both. Scores are scaled to integers so every existing
    consumer (score > 0 checks, the citation floor) is unchanged.
    """
    if not chunks or not is_enabled():
        return None

    floor = SIMILARITY_FLOOR if floor is None else floor
    band = RELATIVE_BAND if band is None else band
    texts = [str(c.get("content") or "") for c in chunks]

    # Read-only for the corpus: a cold church stays on keyword scoring rather
    # than blocking this request on hundreds of embed calls.
    chunk_vectors = embed_texts(texts, task=TASK_DOCUMENT, allow_api=False)
    if chunk_vectors is None or any(v is None for v in chunk_vectors):
        return None

    query_vector = embed_query(question, usage=usage)
    if query_vector is None:
        return None

    scored = sorted(
        ((similarity(query_vector, vec), chunk)
         for vec, chunk in zip(chunk_vectors, chunks)),
        key=lambda pair: pair[0], reverse=True,
    )
    if not scored or scored[0][0] < floor:
        # Nothing here is about the question. Returning no chunks is the
        # correct answer — it is what lets the model say so instead of
        # reasoning from four pages that merely share a vocabulary.
        return []

    cutoff = max(floor, scored[0][0] * band)
    # Scaled by 1000 rather than 100: similarities cluster tightly, and at two
    # digits genuinely different matches would tie and lose their ordering.
    return [(int(score * 1000), chunk)
            for score, chunk in scored if score >= cutoff][:top_n]


# ── Warming ──────────────────────────────────────────────────────────────────

def chunk_hashes(chunks) -> set:
    """The cache keys for a set of chunks, as the warm job's live set."""
    return {_text_hash(str(c.get("content") or "")) for c in chunks}


def prune_cache(live_hashes, query_max_age_days: int = 30) -> int:
    """Drop vectors for text that no longer exists anywhere.

    Every edited page and re-crawl mints a new cache entry, so without this the
    table grows with the history of a church's website rather than its size.
    The warm job visits every church's every chunk, so the union of what it saw
    is authoritative: a document vector outside it is unreachable.

    Query vectors are not in that set by construction and are aged out instead.
    """
    from datetime import datetime, timedelta

    removed = 0
    try:
        stale_queries = EmbeddingCache.query.filter(
            EmbeddingCache.task == TASK_QUERY,
            EmbeddingCache.created_at < datetime.utcnow() - timedelta(days=query_max_age_days),
        ).delete()
        removed += stale_queries or 0

        # Compare in Python: the live set can be larger than SQLite's limit on
        # bound variables for a NOT IN clause.
        orphans = [
            row.id for row in EmbeddingCache.query
            .filter(EmbeddingCache.task == TASK_DOCUMENT)
            .with_entities(EmbeddingCache.id, EmbeddingCache.text_hash)
            if row.text_hash not in live_hashes
        ]
        for start in range(0, len(orphans), 500):
            EmbeddingCache.query.filter(
                EmbeddingCache.id.in_(orphans[start:start + 500])
            ).delete(synchronize_session=False)
        removed += len(orphans)
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("[EMBED] cache prune failed")
        return 0
    return removed


def warm_chunks(chunks, usage=None) -> int:
    """Embed and cache any of *chunks* that have no vector yet.

    Returns the number newly embedded. Called from the nightly job, never from
    a request.
    """
    if not chunks or not is_enabled():
        return 0
    texts = [str(c.get("content") or "") for c in chunks]
    hashes = {_text_hash(t) for t in texts}
    try:
        before = len(_cached(hashes, TASK_DOCUMENT))
    except Exception:
        log.exception("[EMBED] warm cache lookup failed")
        return 0
    if embed_texts(texts, task=TASK_DOCUMENT, usage=usage) is None:
        return 0
    return max(0, len(hashes) - before)
