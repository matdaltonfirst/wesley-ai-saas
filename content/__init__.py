"""The church's house style for generated content.

A universal core with one editable layer over it (the ``ContentProfile`` row),
plus a registry of title strategies."""

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
