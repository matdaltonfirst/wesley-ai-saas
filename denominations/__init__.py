"""Theology for this church: Wesleyan United Methodist.

This app serves one church, so there is exactly one profile and nothing to
select. The layered answer model is:

    USER QUESTION
      -> Wesley AI core              (behaviour only; helpers.py)
      -> United Methodist profile    (this package, ``umc.py``)
      -> Approved local practice     (local_practice.py + church Q&A)
      -> ANSWER

Public interface:

    PROFILE
    load_denomination_chunks()      score_denomination_chunks(question)
    render_local_practice_block(org)
    validate_local_practices(raw)   validate_statement_of_faith(raw)
    local_practice_schema()         LocalPracticeError
"""

from .base import AWAITING_CONTENT, REVIEWED, DenominationProfile, KnowledgeSection
from .local_practice import (
    LocalPracticeError,
    load_local_practices,
    local_practice_schema,
    render_local_practice_block,
    validate_local_practices,
    validate_statement_of_faith,
)
from .retrieval import load_denomination_chunks, score_denomination_chunks
from .umc import PROFILE

__all__ = [
    "AWAITING_CONTENT",
    "PROFILE",
    "REVIEWED",
    "DenominationProfile",
    "KnowledgeSection",
    "LocalPracticeError",
    "load_denomination_chunks",
    "load_local_practices",
    "local_practice_schema",
    "render_local_practice_block",
    "score_denomination_chunks",
    "validate_local_practices",
    "validate_statement_of_faith",
]
