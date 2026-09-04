"""Per-church content style — the layer that keeps one church's house style
from becoming every church's.

Same shape as the ``denominations`` package and for the same reason: a neutral
universal core, exactly one per-tenant layer over it, and the tenant's own
approved settings outranking the default. The difference is that denominations
are a fixed registry of reviewed profiles, while a content style is continuous
— every church has its own voice — so the per-church part is a database row and
only the *strategies* are a registry.
"""

from .strategies import (
    DEFAULT_TITLE_STRATEGY, TITLE_STRATEGIES, is_valid_title_strategy,
    title_strategy_options,
)
from .profile import ResolvedProfile, profile_for, style_prompt_block

__all__ = [
    "DEFAULT_TITLE_STRATEGY",
    "TITLE_STRATEGIES",
    "ResolvedProfile",
    "is_valid_title_strategy",
    "profile_for",
    "style_prompt_block",
    "title_strategy_options",
]
