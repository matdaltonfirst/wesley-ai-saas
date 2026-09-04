"""Title strategies a church can choose between.

These exist as a registry because a video title is the one piece of generated
content where the *approach* is a genuine strategic choice rather than a matter
of voice, and where churches differ sharply. A question title earns clicks from
people searching for an answer; a scripture-first title serves a congregation
looking up what was preached on a passage. Neither is correct in general.

Adding a strategy means adding an entry here. Nothing else changes.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TitleStrategy:
    key: str
    display_name: str
    description: str          # shown to church staff choosing one
    guidance: str             # injected into the prompt


TITLE_STRATEGIES = {
    "question": TitleStrategy(
        key="question",
        display_name="Question",
        description=(
            "Phrase the title as the question the message answers. Reaches "
            "people searching for that question rather than for your church."
        ),
        guidance=(
            "Write each title as a direct question the message actually answers, "
            "in the words someone would type into a search box. Keep it under 70 "
            "characters. Do not include the church name, the series name, or a date."
        ),
    ),
    "question_caps": TitleStrategy(
        key="question_caps",
        display_name="Question (all caps)",
        description=(
            "A question title in full capitals. Higher contrast in a crowded "
            "feed; some audiences read it as shouting."
        ),
        guidance=(
            "Write each title as a direct question the message actually answers, "
            "in the words someone would type into a search box, then render it "
            "in FULL CAPITALS. Keep it under 70 characters. Do not include the "
            "church name, the series name, or a date."
        ),
    ),
    "topical": TitleStrategy(
        key="topical",
        display_name="Topical statement",
        description=(
            "State the subject plainly. Reads as steady rather than urgent, and "
            "ages well in a sermon archive."
        ),
        guidance=(
            "Write each title as a plain statement of the message's subject, "
            "under 70 characters. No question marks, no capitalised shouting, no "
            "clickbait framing. It should still read well in a list two years from now."
        ),
    ),
    "scripture_first": TitleStrategy(
        key="scripture_first",
        display_name="Scripture first",
        description=(
            "Lead with the passage preached. Best for congregations who look "
            "sermons up by text, and for expository series."
        ),
        guidance=(
            "Lead each title with the scripture reference preached from, then a "
            "short phrase naming the message's subject, e.g. "
            "\"John 15:1-8 — Staying Connected\". Under 70 characters. Use only "
            "references the transcript actually preaches from."
        ),
    ),
}

DEFAULT_TITLE_STRATEGY = "question"


def is_valid_title_strategy(key) -> bool:
    return isinstance(key, str) and key in TITLE_STRATEGIES


def get_title_strategy(key) -> TitleStrategy:
    """Always returns a strategy; an unknown or missing key falls back."""
    if is_valid_title_strategy(key):
        return TITLE_STRATEGIES[key]
    return TITLE_STRATEGIES[DEFAULT_TITLE_STRATEGY]


def title_strategy_options() -> list[dict]:
    """Friendly options for the settings UI — names, never internal keys."""
    return [
        {
            "key": s.key,
            "display_name": s.display_name,
            "description": s.description,
        }
        for s in TITLE_STRATEGIES.values()
    ]
