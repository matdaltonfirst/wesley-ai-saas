"""Structured local-practice settings: schema, validation, and prompt rendering.

Local practice is stored as one validated JSON object on the church row rather
than as a dozen rigid columns, because the field set will keep growing as
denominations are added. The trade-off is that validation must be explicit and
server-side: allowed keys only, declared types, declared length limits.

Local practice describes what *this congregation* does. It is never a statement
of what a denomination teaches — see ``docs/denominational-architecture.md``.
"""

# key → (label, kind, max_length)
#   kind "text" → single string, trimmed and truncated at max_length
#   kind "list" → list of strings, each trimmed and truncated at max_length
FIELDS: dict[str, tuple[str, str, int]] = {
    "preferred_clergy_title":       ("Preferred clergy title", "text", 80),
    "baptism_practice":             ("Baptism practice", "text", 600),
    "communion_eligibility":        ("Who may receive Communion", "text", 600),
    "communion_frequency":          ("How often Communion is served", "text", 200),
    "membership_process":           ("Membership process", "text", 600),
    "marriage_inquiry_handling":    ("How marriage inquiries are handled", "text", 600),
    "women_in_pastoral_leadership": ("Women in pastoral leadership", "text", 600),
    "governance_terminology":       ("Governance terminology", "text", 200),
    "pastor_referral_topics":       ("Topics to refer to a pastor", "list", 120),
}

# Field ordering for the settings UI and the prompt block.
FIELD_ORDER = tuple(FIELDS)

MAX_LIST_ITEMS = 20
MAX_STATEMENT_OF_FAITH = 6000


class LocalPracticeError(ValueError):
    """Raised when submitted local-practice settings fail validation."""


def validate_local_practices(raw) -> dict:
    """Return a cleaned local-practice dict, or raise ``LocalPracticeError``.

    Rejects unknown keys and wrong types outright rather than silently dropping
    them: a church admin who mistypes a field should be told, not left believing
    the assistant received something it did not.
    """
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise LocalPracticeError("Local practices must be an object.")

    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise LocalPracticeError(
            "Unknown local practice field(s): " + ", ".join(unknown)
        )

    cleaned: dict = {}
    for key, value in raw.items():
        label, kind, max_length = FIELDS[key]
        if kind == "text":
            if value is None:
                continue
            if not isinstance(value, str):
                raise LocalPracticeError(f"{label} must be text.")
            text = value.strip()
            if not text:
                continue
            if len(text) > max_length:
                raise LocalPracticeError(
                    f"{label} must be {max_length} characters or fewer."
                )
            cleaned[key] = text
        else:
            if value is None:
                continue
            if not isinstance(value, list):
                raise LocalPracticeError(f"{label} must be a list.")
            if len(value) > MAX_LIST_ITEMS:
                raise LocalPracticeError(
                    f"{label} may have at most {MAX_LIST_ITEMS} entries."
                )
            items = []
            for item in value:
                if not isinstance(item, str):
                    raise LocalPracticeError(f"{label} entries must be text.")
                entry = item.strip()
                if not entry:
                    continue
                if len(entry) > max_length:
                    raise LocalPracticeError(
                        f"Each {label.lower()} entry must be "
                        f"{max_length} characters or fewer."
                    )
                items.append(entry)
            if items:
                cleaned[key] = items
    return cleaned


def validate_statement_of_faith(raw) -> str:
    """Validate a church's local statement of faith."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise LocalPracticeError("Statement of faith must be text.")
    text = raw.strip()
    if len(text) > MAX_STATEMENT_OF_FAITH:
        raise LocalPracticeError(
            f"Statement of faith must be {MAX_STATEMENT_OF_FAITH} "
            "characters or fewer."
        )
    return text


def load_local_practices(church) -> dict:
    """Read a church's stored local practices, tolerating bad stored JSON."""
    import json

    raw = getattr(church, "local_practices", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    # Drop anything no longer in the schema so retired fields cannot reappear.
    return {k: v for k, v in parsed.items() if k in FIELDS}


def render_local_practice_block(church) -> str:
    """The approved local-practice layer of the prompt, or "" when empty.

    Framed so the model can never restate a congregation's practice as its
    denomination's official teaching.
    """
    practices = load_local_practices(church)
    statement = (getattr(church, "statement_of_faith", None) or "").strip()
    if not practices and not statement:
        return ""

    lines = [
        "",
        "",
        "--- Approved Local Church Practice (pastor-approved) ---",
        "The following describes what THIS congregation does. It is more "
        "authoritative than the denominational profile for questions about this "
        "church's own practice.",
        "It is not a statement of denominational teaching. Never convert it into "
        "one: if this congregation's practice differs from or is narrower than "
        "the denominational default, say what this congregation does AND, when "
        "you have approved denominational material, what the denomination "
        "officially teaches or permits — as two distinct things.",
    ]
    if practices:
        lines.append("")
        for key in FIELD_ORDER:
            if key not in practices:
                continue
            label = FIELDS[key][0]
            value = practices[key]
            if isinstance(value, list):
                lines.append(f"{label}: " + "; ".join(value))
            else:
                lines.append(f"{label}: {value}")
    if statement:
        lines += [
            "",
            "This church's approved statement of faith (treat as this "
            "congregation's own teaching, not a denominational claim):",
            statement,
        ]
    return "\n".join(lines)


def local_practice_schema() -> list[dict]:
    """Field descriptors for the settings UI."""
    return [
        {
            "key": key,
            "label": FIELDS[key][0],
            "kind": FIELDS[key][1],
            "max_length": FIELDS[key][2],
        }
        for key in FIELD_ORDER
    ]
