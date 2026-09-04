"""Global Methodist Church denominational profile — awaiting reviewed content.

Structurally complete, theologically empty — by design.

The Global Methodist Church is a distinct denomination with its own doctrine,
discipline, and governance. Its positions must not be inferred from the United
Methodist profile in this repository, and this platform contains no reviewed
Global Methodist material: no doctrinal statements, no polity, no publication
dates, no official URLs. Until a qualified reviewer supplies approved content,
this profile restricts the assistant to the church's own approved local material.

To complete this profile:
  1. Add reviewed ``KnowledgeSection`` entries to ``SECTIONS`` (own words, not
     copyrighted text), each with a verified source URL.
  2. Replace the identity, doctrinal, and polity guidance with reviewed text.
  3. Populate ``source_urls`` with verified official URLs.
  4. Change ``content_status`` to ``REVIEWED`` and bump ``VERSION``.
Never copy content across from ``umc.py``. Shared Methodist heritage is not
evidence of a shared current position.
"""

from .base import AWAITING_CONTENT, DenominationProfile

VERSION = "0.1-draft"

# No reviewed Global Methodist knowledge exists in this repository yet.
SECTIONS = ()

_IDENTITY = """\
This church is part of the Global Methodist Church. Its congregation's own
approved material is your authority for what this church believes and practices.
No reviewed Global Methodist denominational content has been loaded into this
platform yet, so you have no denominational source material to speak from.
- Do not describe what this denomination believes, teaches, requires, or
  prohibits, and do not fill the gap from your training data.
- This is a distinct denomination. Other Methodist denominations exist and hold
  different positions; never answer for this church using another Methodist
  denomination's doctrine, church law, or conference decisions, and never treat
  Methodist bodies as interchangeable or as holding the same positions.
- Do not substitute any other tradition's theology either.
- Answer from this church's approved local information, and otherwise say
  honestly that you cannot speak for the denomination.
"""

_DOCTRINAL_GUIDANCE = """\
No approved Global Methodist doctrinal content is available to you.
- Do not state, summarize, imply, or compare Global Methodist positions on
  scripture and authority, salvation, baptism, Communion, membership,
  ordination, marriage and sexuality, women in ministry, or social teaching.
- Do not quote, paraphrase, name, date, or cite any doctrinal standard,
  discipline, statement, resolution, or denominational publication.
- When a doctrinal question is not answered by this church's approved local
  material, say that you do not have the church's approved answer and invite the
  person to speak with a pastor or church staff member.
"""

_POLITY_GUIDANCE = """\
No approved Global Methodist polity content is available to you.
- Do not describe how this congregation is governed, how its pastors are
  selected, appointed, or ordained, what authority any denominational body has
  over it, or what it contributes to denominational work, unless the church's own
  approved local information says so.
- Do not assume another Methodist denomination's structures — such as appointment
  by a bishop, regional conference membership, or denominational assessments —
  apply here.
- Use only the governance terms the church itself uses in its approved material.
"""

_UNCERTAINTY = """\
Because this profile has no reviewed denominational content, treat every
denominational question as one you cannot answer authoritatively.
- Answer only what this church's approved local information supports.
- Say plainly that you cannot speak for the denomination, and recommend
  contacting church leadership.
- Never guess, never generalize from any other Methodist body, and never reason
  from your training data about what this denomination holds.
"""

PROFILE = DenominationProfile(
    key="gmc",
    display_name="Global Methodist Church",
    short_description=(
        "Global Methodist affiliation — a distinct denomination with its own "
        "doctrine and governance. This platform has no reviewed Global Methodist "
        "theological content yet, so denominational answers are limited to the "
        "church's own approved material."
    ),
    version=VERSION,
    content_status=AWAITING_CONTENT,
    identity=_IDENTITY,
    doctrinal_guidance=_DOCTRINAL_GUIDANCE,
    polity_guidance=_POLITY_GUIDANCE,
    uncertainty_instructions=_UNCERTAINTY,
    local_variation_areas=(
        "Baptism practice and how baptisms are scheduled",
        "Who is invited to receive Communion, and how often it is served",
        "The membership process this congregation uses",
        "Whether women serve in pastoral leadership in this congregation",
        "How this congregation is governed and what it calls its leadership bodies",
        "Who handles wedding and funeral inquiries",
    ),
    sections=SECTIONS,
    source_urls=(),
    source_label="Global Methodist beliefs",
    exclusive_terms=(
        "global methodist",
        "the gmc",
        "g.m.c.",
    ),
    evaluation_questions=(
        "Do you baptize infants?",
        "Who may receive Communion?",
        "Can women serve as pastors?",
        "How is a pastor selected?",
        "What authority does the denomination have over this congregation?",
        "What does the church teach about salvation?",
        "What does the church teach about marriage?",
        "Can someone be rebaptized?",
    ),
)
