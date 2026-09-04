"""Reusable denominational isolation matrix.

Two things live here so that tests, review tooling, and future profiles all use
the same definitions:

``ISOLATION_QUESTIONS``
    The standard doctrinal/polity questions every profile must answer without
    reaching into another profile.

``PROFILE_MARKERS``
    Distinctive phrases that must only ever appear in one profile's assembled
    prompt or retrieval output. These make isolation checks deterministic — no
    live model call is needed to prove separation.
"""

from .registry import PROFILES

# The standard cross-profile question set. Deliberately phrased the way a real
# visitor asks, since that is what retrieval scoring actually sees.
ISOLATION_QUESTIONS = (
    "Do you baptize infants?",
    "Who may receive Communion?",
    "Can women serve as pastors?",
    "How is a pastor selected?",
    "What authority does the denomination have over this congregation?",
    "What does the church teach about salvation?",
    "What does the church teach about marriage?",
    "Can someone be rebaptized?",
)

# Phrases that must never cross profiles. Kept lowercase; checks are
# case-insensitive substring tests against assembled prompts and chunk text.
#
# Only unambiguous, substantive markers belong here. Broad words like
# "methodist" or "wesleyan" are excluded deliberately: a profile is allowed to
# tell the model *not* to answer from a neighbouring tradition, and the product
# itself is named Wesley, so those words cannot distinguish leakage from a
# guardrail. The phrases below only ever appear when a profile's actual doctrinal
# or polity content is present.
PROFILE_MARKERS: dict[str, tuple[str, ...]] = {
    "umc": (
        "united methodist",
        "book of discipline",
        "general conference",
        "prevenient grace",
        "social principles",
        "articles of religion",
        "wesleyan quadrilateral",
        "apportioned giving",
    ),
    "sbc": (
        "southern baptist",
        "baptist faith and message",
        "cooperative program",
    ),
    "gmc": (
        "global methodist",
    ),
    "non_denominational": (),
    "custom": (),
}


# Factual cross-references a profile is allowed to make about another
# denomination's *existence*, as opposed to importing its doctrine.
#
# The United Methodist recent-history section says that some congregations which
# disaffiliated joined the Global Methodist Church, formed in 2022. That is a
# true statement about United Methodist history and belongs in the UMC profile;
# stripping it to satisfy a substring check would make reviewed content less
# accurate. It carries no Global Methodist doctrine, and the chunk is still
# tagged ``denomination="umc"``, so it can never be served to a Global Methodist
# church.
#
# Keep this map as small as possible, and never add an entry to make a leak test
# pass — only for content a reviewer has confirmed is factual and doctrine-free.
ALLOWED_CROSS_REFERENCES: dict[str, tuple[str, ...]] = {
    "umc": ("global methodist",),
}


def foreign_markers(key: str) -> tuple[str, ...]:
    """Markers belonging to every profile other than ``key``.

    Excludes the narrow, reviewed cross-references in
    ``ALLOWED_CROSS_REFERENCES``.
    """
    allowed = set(ALLOWED_CROSS_REFERENCES.get(key, ()))
    markers: list[str] = []
    for profile_key, terms in PROFILE_MARKERS.items():
        if profile_key == key:
            continue
        markers.extend(term for term in terms if term not in allowed)
    return tuple(markers)


def evaluation_questions(key: str) -> tuple[str, ...]:
    """A profile's own standard evaluation questions."""
    profile = PROFILES.get(key)
    return profile.evaluation_questions if profile else ()
