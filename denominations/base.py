"""Shared types for denominational profiles.

A profile is *data*: theological identity, doctrine, polity, and citable
knowledge sections for exactly one denomination. Profiles never import route
modules, never touch the database, and never know which church is asking — so
reviewed theological content can be added or corrected without touching
application logic.

Every church loads exactly one profile. Two profiles are never assembled into
the same prompt.
"""

from dataclasses import dataclass

# Profile content status — surfaced in settings so a church admin can see
# whether the denominational layer has reviewed material behind it yet.
REVIEWED = "reviewed"
AWAITING_CONTENT = "awaiting_approved_content"

VALID_STATUSES = (REVIEWED, AWAITING_CONTENT)


@dataclass(frozen=True)
class KnowledgeSection:
    """One reviewed, citable paragraph of denominational knowledge.

    Content is written in the platform's own words — never copyrighted text —
    and is injected as a numbered retrieval source so answers can cite it.
    """

    key: str
    title: str
    content: str
    url: str = ""


@dataclass(frozen=True)
class DenominationProfile:
    """Everything the assistant may assume about one denomination."""

    key: str
    display_name: str
    short_description: str
    version: str
    content_status: str

    # Prompt layers contributed by this profile
    identity: str                       # assistant identity / theological perspective
    doctrinal_guidance: str
    polity_guidance: str
    uncertainty_instructions: str

    local_variation_areas: tuple[str, ...] = ()
    sections: tuple[KnowledgeSection, ...] = ()
    source_urls: tuple[str, ...] = ()

    # Prefix used for citations drawn from this profile's sections, e.g.
    # "United Methodist beliefs: Baptism". Kept stable — visitors see it.
    source_label: str = ""

    # Terms that belong exclusively to this denomination. Used to keep foreign
    # denominational text (for example the platform-wide editable prompt, which
    # was originally authored for United Methodist churches) out of another
    # denomination's prompt. Lowercase.
    exclusive_terms: tuple[str, ...] = ()

    # Standard denomination-specific evaluation questions. These drive the
    # isolation test matrix and give reviewers a fixed checklist per profile.
    evaluation_questions: tuple[str, ...] = ()

    def __post_init__(self):
        if self.content_status not in VALID_STATUSES:
            raise ValueError(f"Unknown content_status: {self.content_status}")

    # ── Status ───────────────────────────────────────────────────────────────

    @property
    def awaiting_content(self) -> bool:
        """True when this profile has no reviewed theological material yet."""
        return self.content_status == AWAITING_CONTENT

    @property
    def primary_url(self) -> str:
        return self.source_urls[0] if self.source_urls else ""

    # ── Retrieval ────────────────────────────────────────────────────────────

    def chunks(self) -> list[dict]:
        """This profile's knowledge as citable retrieval chunks.

        Same shape as document chunks, so the shared citation builder handles
        them without special-casing. A profile awaiting approved content has no
        sections and therefore contributes nothing to retrieval or citations.
        """
        return [
            {
                "content": f"{section.title}\n{section.content}",
                "source": f"{self.source_label}: {section.title}",
                "location": section.url or self.primary_url,
                "type": "denomination",
                "denomination": self.key,
            }
            for section in self.sections
        ]

    # ── Prompt assembly ──────────────────────────────────────────────────────

    def prompt_block(self) -> str:
        """The single denominational layer injected into a church's prompt."""
        lines = [
            "",
            "",
            f"--- Denominational Profile: {self.display_name} "
            f"(profile key {self.key}, version {self.version}) ---",
            f"This church is affiliated with: {self.display_name}. "
            f"{self.short_description}",
            "This is the only denominational profile that applies to this "
            "church. Do not answer from, refer to, or compare against any "
            "other denomination's teaching unless the person explicitly asks "
            "about that other denomination.",
            "",
            "Theological identity and perspective:",
            self.identity.strip(),
            "",
            "Doctrinal guidance:",
            self.doctrinal_guidance.strip(),
            "",
            "Governance and polity guidance:",
            self.polity_guidance.strip(),
        ]
        if self.local_variation_areas:
            lines += [
                "",
                "Areas where this congregation's local practice may differ "
                "from the denominational default — check approved local "
                "information before answering, and say plainly when you do "
                "not know this congregation's practice:",
            ]
            lines += [f"- {area}" for area in self.local_variation_areas]
        lines += [
            "",
            "Handling uncertainty in this profile:",
            self.uncertainty_instructions.strip(),
        ]
        if self.awaiting_content:
            lines += [
                "",
                "IMPORTANT — this denominational profile is awaiting reviewed, "
                "approved theological content. You therefore have no "
                "authoritative denominational source material for this church. "
                "Do not make definitive claims about what this denomination "
                "officially teaches, requires, prohibits, or permits. Do not "
                "quote or paraphrase any denominational statement of faith, "
                "confession, constitution, bylaw, resolution, or governing "
                "document, and do not cite dates, paragraph numbers, or "
                "titles for them. Answer doctrinal and polity questions only "
                "from this church's own approved local material, and otherwise "
                "say that you cannot speak for the denomination and refer the "
                "person to church leadership.",
            ]
        if self.source_urls:
            lines += [
                "",
                "Denominational sources behind this profile: "
                + ", ".join(self.source_urls),
            ]
        return "\n".join(lines)

    # ── Serialization for settings UI ────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "short_description": self.short_description,
            "version": self.version,
            "content_status": self.content_status,
            "awaiting_content": self.awaiting_content,
            "source_urls": list(self.source_urls),
            "knowledge_section_count": len(self.sections),
            "local_variation_areas": list(self.local_variation_areas),
            "evaluation_questions": list(self.evaluation_questions),
        }
