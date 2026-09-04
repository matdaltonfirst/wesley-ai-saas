"""Denomination-aware retrieval.

Callers pass the church's denomination key; they never choose chunks themselves
and never branch on which denomination it is. A church assigned one denomination
can only ever receive that denomination's chunks — the loader has no path to any
other profile's sections.
"""

from .registry import get_denomination_profile


def load_denomination_chunks(denomination) -> list[dict]:
    """Citable retrieval chunks for exactly one denomination.

    Returns an empty list for profiles with no reviewed content, so those
    churches contribute nothing to retrieval candidates, model context, or
    citations rather than borrowing another denomination's material.
    """
    return get_denomination_profile(denomination).chunks()


def score_denomination_chunks(
    question: str, denomination, top_n: int = 3, usage: dict = None
) -> list[tuple[int, dict]]:
    """Score one denomination's sections against a question.

    Uses a gentler threshold than general retrieval, on both the semantic and
    keyword paths: a doctrine question often turns on exactly one decisive
    concept ("homosexuality", "baptize"), and missing it means the model falls
    back to stale training data — the failure this layer exists to prevent.
    """
    from documents import extract_keywords, score_chunk
    from embeddings import (
        RELATIVE_BAND_DENOMINATION, SIMILARITY_FLOOR_DENOMINATION, rank_chunks,
    )

    chunks = load_denomination_chunks(denomination)
    if not chunks:
        return []

    semantic = rank_chunks(
        question, chunks, top_n=top_n,
        floor=SIMILARITY_FLOOR_DENOMINATION,
        band=RELATIVE_BAND_DENOMINATION,
        usage=usage,
    )
    if semantic:
        return semantic

    keywords = extract_keywords(question)
    if not keywords:
        return []
    scored = [(score_chunk(chunk, keywords), chunk) for chunk in chunks]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [(score, chunk) for score, chunk in scored[:top_n] if score > 0]
