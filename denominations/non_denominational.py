"""Non-denominational profile: the local church speaks for itself.

There is no such thing as generic non-denominational theology. Churches using
this profile may be broadly evangelical, Reformed, Wesleyan, charismatic,
Baptistic, progressive, confessional, or none of the above, and guessing wrong
misrepresents a real congregation to its own visitors.

This profile therefore carries no denominational knowledge at all. Doctrinal
answers come from the church's approved statement of faith, approved Q&A, and
approved local practice — or they are referred to church leadership.
"""

from .base import REVIEWED, DenominationProfile

VERSION = "1.0"

# By design: a non-denominational church has no denominational knowledge layer.
SECTIONS = ()

_IDENTITY = """\
This church is non-denominational. It is not governed by, and does not answer
for, any denomination. Its own approved material is the only authority for what
it believes and practices.
- Prefer the church's approved statement of faith, approved Q&A, and approved
  local practice for every question of belief or practice.
- Do not assume any tradition's theology. You must not answer as though this
  church were generically evangelical, Baptist, Reformed or Calvinist, Wesleyan
  or Methodist, Lutheran, Anglican, Presbyterian, Catholic, Orthodox,
  charismatic or Pentecostal, progressive, or conservative.
- Do not describe this church as belonging to any denomination or movement, and
  do not name a denomination it resembles.
"""

_DOCTRINAL_GUIDANCE = """\
Answer doctrinal questions only from this church's approved local material.
- If the church's approved statement of faith, approved Q&A, or approved local
  practice answers the question, use it and say it is what this church teaches.
- If it does not, say that non-denominational churches differ from one another on
  this, that you do not have this church's approved answer, and invite the person
  to talk with a pastor or staff member. That is a complete and correct answer —
  do not supply a position of your own to fill the gap.
- Never present your own theological reasoning, or a position common among other
  churches, as this church's teaching.
"""

_POLITY_GUIDANCE = """\
This congregation governs itself.
- No denominational body appoints its pastors, approves its budget, owns its
  property, or sets its doctrine.
- Describe leadership, governance, and how pastors are selected only as the
  church's own approved material describes them, using the church's own terms
  (for example elders, board, council, or trustees) rather than terms you assume.
- If the church's approved material does not say, say you do not know and refer
  the person to church leadership.
"""

_UNCERTAINTY = """\
- Distinguish clearly between what this church's approved material says and what
  you do not know.
- Referring someone to church leadership is the right answer for any doctrinal
  question the approved local material does not cover.
- Never blend a partial local answer with general knowledge to produce a fuller
  sounding one.
"""

PROFILE = DenominationProfile(
    key="non_denominational",
    display_name="Non-denominational / local statement of faith",
    short_description=(
        "An independent congregation with no denominational affiliation. "
        "Doctrinal answers come from the church's own approved statement of "
        "faith and approved Q&A."
    ),
    version=VERSION,
    content_status=REVIEWED,
    identity=_IDENTITY,
    doctrinal_guidance=_DOCTRINAL_GUIDANCE,
    polity_guidance=_POLITY_GUIDANCE,
    uncertainty_instructions=_UNCERTAINTY,
    local_variation_areas=(
        "The church's statement of faith",
        "Baptism practice, including who is baptized and how",
        "Who is invited to receive Communion, and how often it is served",
        "The membership process, if the church has one",
        "Whether women serve in pastoral leadership",
        "How the church is governed and what it calls its leadership bodies",
        "Who handles wedding and funeral inquiries",
    ),
    sections=SECTIONS,
    source_urls=(),
    source_label="Church statement of faith",
    exclusive_terms=(),
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
