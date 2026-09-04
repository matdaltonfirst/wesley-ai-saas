"""Back-compatible access to the United Methodist denominational knowledge layer.

The content moved to ``denominations/umc.py`` when the platform became
multi-denominational. This module stays as a thin shim so existing imports keep
working; new code should use the denomination-aware interface instead:

    from denominations import (
        get_denomination_profile, load_denomination_chunks,
        score_denomination_chunks,
    )

and pass ``church.denomination`` so a church only ever sees its own profile.
"""

from denominations import retrieval as _retrieval
from denominations.umc import PROFILE as UMC_PROFILE

_UMC_URL = UMC_PROFILE.primary_url

# Legacy dict shape: the sections as they were exposed before profiles existed.
SECTIONS = [
    {"key": section.key, "title": section.title, "content": section.content}
    for section in UMC_PROFILE.sections
]


def load_denomination_chunks() -> list[dict]:
    """UMC facts as citable retrieval chunks (same shape as document chunks)."""
    return _retrieval.load_denomination_chunks(UMC_PROFILE.key)


def score_denomination_chunks(question: str, top_n: int = 3) -> list[tuple[int, dict]]:
    """Score UMC sections for a question.

    UMC-only by construction. Use
    ``denominations.score_denomination_chunks(question, church.denomination)``
    for denomination-aware retrieval.
    """
    return _retrieval.score_denomination_chunks(question, UMC_PROFILE.key, top_n)
