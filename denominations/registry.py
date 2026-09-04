"""Denomination profile registry.

Adding a denomination means adding a profile module and one line to ``_MODULES``.
No route, prompt-assembly, or retrieval code changes.
"""

import logging

from . import custom, gmc, non_denominational, sbc, umc
from .base import DenominationProfile

log = logging.getLogger("wesley")


# Existing churches predate the denomination field and are all United Methodist,
# so a missing value means "legacy United Methodist church".
DEFAULT_DENOMINATION = "umc"

# A key that is present but unrecognised is a bug or a hand-edited row, not a
# legacy church. Handing such a church a full theological profile is the exact
# failure this architecture exists to prevent, so the fail-safe is the profile
# that asserts no denominational theology at all.
FAILSAFE_DENOMINATION = "non_denominational"

_MODULES = (umc, sbc, gmc, non_denominational, custom)

# Display order in onboarding and settings. Keys are stable and internal;
# never show them to users.
PROFILES: dict[str, DenominationProfile] = {}
for _module in _MODULES:
    _profile = _module.PROFILE
    if _profile.key in PROFILES:
        raise ValueError(f"Duplicate denomination key: {_profile.key}")
    PROFILES[_profile.key] = _profile

VALID_KEYS = frozenset(PROFILES)


def is_valid_denomination(key) -> bool:
    """True when *key* is a known, stable internal profile key."""
    return isinstance(key, str) and key in VALID_KEYS


def get_denomination_profile(key) -> DenominationProfile:
    """Return exactly one profile, with explicit fallbacks for bad input.

    Missing value  → the default profile: a church row that predates the
                     denomination column is a legacy United Methodist church.
    Unknown value  → the fail-safe profile, which claims no denominational
                     theology. Logged as an error, because reaching here means
                     something wrote a key that validation should have rejected.
    """
    if is_valid_denomination(key):
        return PROFILES[key]
    if key in (None, ""):
        return PROFILES[DEFAULT_DENOMINATION]
    log.error(
        "Unrecognised denomination key %r — falling back to %r, which asserts "
        "no denominational theology", key, FAILSAFE_DENOMINATION,
    )
    return PROFILES[FAILSAFE_DENOMINATION]


def church_profile(church) -> DenominationProfile:
    """The profile for a Church row (or anything with a ``denomination``)."""
    return get_denomination_profile(getattr(church, "denomination", None))


def denomination_options() -> list[dict]:
    """Friendly options for onboarding and settings — display names, not keys."""
    return [
        {
            "key": profile.key,
            "display_name": profile.display_name,
            "short_description": profile.short_description,
            "version": profile.version,
            "content_status": profile.content_status,
            "awaiting_content": profile.awaiting_content,
        }
        for profile in PROFILES.values()
    ]


def foreign_terms(key) -> tuple[str, ...]:
    """Terms owned exclusively by denominations *other than* ``key``.

    Used to keep text authored for one denomination — most importantly the
    platform-wide editable prompt, which was originally written for United
    Methodist churches — out of another denomination's prompt.
    """
    own = get_denomination_profile(key).key
    terms: list[str] = []
    for profile in PROFILES.values():
        if profile.key == own:
            continue
        terms.extend(profile.exclusive_terms)
    return tuple(terms)


def contains_foreign_denomination_text(text: str, key) -> bool:
    """True when *text* mentions another denomination's exclusive terminology."""
    lowered = (text or "").lower()
    return any(term in lowered for term in foreign_terms(key))
