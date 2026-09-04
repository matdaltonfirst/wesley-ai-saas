"""Resolving a church's content style, with defaults for anything unset."""

from dataclasses import dataclass

from .strategies import TitleStrategy, get_title_strategy

DEFAULT_PLATFORMS = ("facebook", "instagram")

# Deliberately plain. A church that has configured nothing should sound like
# itself as far as the transcript allows, not like a marketing account.
_NEUTRAL_VOICE = (
    "Warm, plain-spoken, and unhurried. Write the way a person on staff would "
    "write to their own congregation: no marketing superlatives, no urgency "
    "language, no exclamation stacking, no emoji unless the church's own "
    "examples use them."
)


@dataclass(frozen=True)
class ResolvedProfile:
    """A church's style with every gap filled — never has a None field."""

    voice_notes: str
    title_strategy: TitleStrategy
    platforms: tuple
    hashtags: str
    call_to_action: str
    is_configured: bool   # False when the church has set nothing yet


def profile_for(church) -> ResolvedProfile:
    """The resolved content style for *church*, falling back to neutral defaults."""
    from models import ContentProfile

    row = ContentProfile.query.filter_by(church_id=church.id).first() if church else None

    platforms = DEFAULT_PLATFORMS
    if row and row.platforms:
        parsed = tuple(p.strip().lower() for p in row.platforms.split(",") if p.strip())
        if parsed:
            platforms = parsed

    return ResolvedProfile(
        voice_notes=(row.voice_notes.strip() if row and row.voice_notes else _NEUTRAL_VOICE),
        title_strategy=get_title_strategy(row.title_strategy if row else None),
        platforms=platforms,
        hashtags=(row.hashtags.strip() if row and row.hashtags else ""),
        call_to_action=(row.call_to_action.strip() if row and row.call_to_action else ""),
        is_configured=bool(row and (row.voice_notes or row.title_strategy)),
    )


def style_prompt_block(church, profile: ResolvedProfile) -> str:
    """The church-specific style layer injected into content generation."""
    lines = [
        "",
        f"--- House style for {church.name} ---",
        "This church's own settings. They describe how to write, never what is "
        "true: nothing here licenses a claim the transcript does not support.",
        "",
        "Voice:",
        profile.voice_notes,
        "",
        f"Video title strategy — {profile.title_strategy.display_name}:",
        profile.title_strategy.guidance,
        "",
        "Platforms to write posts for: " + ", ".join(profile.platforms),
    ]
    if profile.hashtags:
        lines += ["", f"Hashtags this church uses: {profile.hashtags}"]
    if profile.call_to_action:
        lines += ["", f"Standing call to action: {profile.call_to_action}"]
    if not profile.is_configured:
        lines += [
            "",
            "This church has not set a house style yet, so the neutral default "
            "above is in force. Prefer restraint over personality: it is easier "
            "for staff to add warmth to a plain post than to strip invented "
            "enthusiasm out of one.",
        ]
    return "\n".join(lines)
