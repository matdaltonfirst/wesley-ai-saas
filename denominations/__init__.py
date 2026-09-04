"""Denominational profile service.

Layered answer model:

    USER QUESTION
      → Wesley AI core            (denominationally neutral; helpers.py)
      → Selected denominational profile   (exactly one, from this package)
      → Approved local church context     (local_practice.py + church Q&A)
      → ANSWER

Strict isolation is the point: a church assigned one denomination must never
receive another denomination's prompt instructions or retrieved denominational
sources. Nothing here loads more than one profile for a church, and no caller
needs to know which denomination it is dealing with.

Public interface — the only names other modules should import:

    DEFAULT_DENOMINATION
    get_denomination_profile(key)      church_profile(church)
    is_valid_denomination(key)         denomination_options()
    load_denomination_chunks(key)      score_denomination_chunks(q, key)
    render_local_practice_block(church)
    validate_local_practices(raw)      validate_statement_of_faith(raw)
    local_practice_schema()            LocalPracticeError
    contains_foreign_denomination_text(text, key)
"""

from .base import (
    AWAITING_CONTENT,
    REVIEWED,
    DenominationProfile,
    KnowledgeSection,
)
from .local_practice import (
    LocalPracticeError,
    load_local_practices,
    local_practice_schema,
    render_local_practice_block,
    validate_local_practices,
    validate_statement_of_faith,
)
from .matrix import ISOLATION_QUESTIONS, PROFILE_MARKERS, foreign_markers
from .registry import (
    DEFAULT_DENOMINATION,
    PROFILES,
    VALID_KEYS,
    church_profile,
    contains_foreign_denomination_text,
    denomination_options,
    get_denomination_profile,
    is_valid_denomination,
)
from .retrieval import load_denomination_chunks, score_denomination_chunks

__all__ = [
    "AWAITING_CONTENT",
    "DEFAULT_DENOMINATION",
    "ISOLATION_QUESTIONS",
    "PROFILES",
    "PROFILE_MARKERS",
    "REVIEWED",
    "VALID_KEYS",
    "DenominationProfile",
    "KnowledgeSection",
    "LocalPracticeError",
    "church_profile",
    "contains_foreign_denomination_text",
    "denomination_options",
    "foreign_markers",
    "get_denomination_profile",
    "is_valid_denomination",
    "load_denomination_chunks",
    "load_local_practices",
    "local_practice_schema",
    "render_local_practice_block",
    "score_denomination_chunks",
    "validate_local_practices",
    "validate_statement_of_faith",
]
