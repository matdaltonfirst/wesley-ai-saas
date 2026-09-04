"""United Methodist Church denominational profile.

The knowledge sections below were previously in ``umc_facts.py`` and are carried
over unchanged: this is the platform's first complete, reviewed profile, and
existing United Methodist churches must keep receiving substantially the same
answers after the multi-denominational refactor.

The AI model's training data predates the May 2024 General Conference, so its
built-in UMC knowledge is stale and it will otherwise answer doctrine questions
confidently from the old Book of Discipline. Update this file when General
Conference acts, and bump ``VERSION``.

Maintainers: keep sections short, factual, and denominationally accurate. Write
in our own words — never copyrighted text.
"""

from .base import REVIEWED, DenominationProfile, KnowledgeSection

VERSION = "2024.1"

_UMC_URL = "https://www.umc.org/en/what-we-believe"

SECTIONS = (
    KnowledgeSection(
        key="current-discipline",
        title="The current Book of Discipline (2020/2024)",
        content=(
            "The current Book of Discipline of The United Methodist Church is the "
            "2020/2024 edition, adopted when the postponed 2020 General Conference "
            "met in Charlotte, North Carolina in April and May of 2024. It replaced "
            "the 2016 edition and includes revised Social Principles. Any statements "
            "from earlier editions — including the former restrictive language on "
            "human sexuality — are no longer current church law."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="marriage-sexuality",
        title="Marriage and human sexuality",
        content=(
            "As of the 2024 General Conference, The United Methodist Church removed "
            "its former statement that the practice of homosexuality is incompatible "
            "with Christian teaching, removed the prohibition on ordaining gay "
            "clergy, and removed bans and penalties related to same-sex weddings. "
            "The revised Social Principles describe marriage as a sacred, lifelong "
            "covenant between two people of faith. Pastors and congregations have "
            "discretion: no pastor is required to perform any particular wedding and "
            "no congregation is required to host one, but they are no longer "
            "forbidden from doing so. The church affirms that all people are of "
            "sacred worth and are welcome in the life of the church."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="baptism",
        title="Baptism",
        content=(
            "United Methodists baptize people of all ages, including infants, "
            "believing God's grace is at work in a person before they can respond — "
            "what Wesleyans call prevenient grace. Baptism may be by sprinkling, "
            "pouring, or immersion, and it is received once: rather than rebaptism, "
            "the church offers reaffirmation of the baptismal covenant. Children "
            "baptized as infants later affirm the faith for themselves at "
            "confirmation."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="communion",
        title="Holy Communion (the open table)",
        content=(
            "The United Methodist Church practices an open table: all who love "
            "Christ, earnestly repent of their sin, and seek to live in peace with "
            "one another are welcome to receive Communion — including children and "
            "guests who are not members of the congregation or the denomination. "
            "United Methodists typically use unfermented grape juice, a practice "
            "rooted in the church's historic witness on alcohol, and understand "
            "Christ to be truly present in the sacrament without defining the "
            "mystery precisely."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="membership",
        title="Church membership",
        content=(
            "People join a United Methodist congregation by profession of faith, by "
            "transfer from another congregation, or through confirmation. Members "
            "take vows to faithfully participate in the church's ministries by "
            "their prayers, their presence, their gifts, their service, and their "
            "witness. Baptism is a prerequisite; those not yet baptized are "
            "baptized when they join."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="clergy-ordination",
        title="Clergy and ordination",
        content=(
            "United Methodist clergy include elders (ordained to word, sacrament, "
            "order, and service), deacons (ordained to word, service, compassion, "
            "and justice), and licensed local pastors. Women have been ordained "
            "with full clergy rights since 1956 and serve at every level, including "
            "as bishops. Pastors are appointed to congregations by bishops in an "
            "itinerant system rather than hired directly. Following the 2024 "
            "General Conference, sexual orientation is not a bar to ordination."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="wesleyan-theology",
        title="Wesleyan understanding of grace",
        content=(
            "United Methodists follow John Wesley's emphasis on grace in three "
            "movements: prevenient grace, which goes before us and draws every "
            "person toward God; justifying grace, through which we are forgiven and "
            "made right with God by faith; and sanctifying grace, which grows us in "
            "holiness and love throughout life. Methodists are Arminian rather than "
            "Calvinist: salvation is offered to all people, not a predestined few, "
            "and faith is lived out through both works of piety and works of mercy. "
            "Theological reflection draws on scripture — primary — along with "
            "tradition, reason, and experience."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="structure",
        title="How the church is organized",
        content=(
            "The United Methodist Church is connectional: congregations are joined "
            "in districts and annual conferences, led by bishops, with General "
            "Conference — meeting every four years — as the only body that speaks "
            "for the whole denomination. Local churches support shared global "
            "ministries through apportioned giving. The 2024 General Conference "
            "also approved a regionalization plan allowing different world regions "
            "to adapt some church rules to their own contexts."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="social-principles",
        title="The Social Principles",
        content=(
            "The Social Principles, substantially revised in 2024, express the "
            "church's teaching on contemporary life in four areas: the Community "
            "of All Creation (care for the natural world), the Economic Community, "
            "the Social Community, and the Political Community. They call United "
            "Methodists to environmental stewardship, human dignity, and justice. "
            "They are teaching documents meant to guide conscience and witness "
            "rather than binding church law."
        ),
        url=_UMC_URL,
    ),
    KnowledgeSection(
        key="recent-history",
        title="Recent denominational history",
        content=(
            "Between 2019 and 2023, in a season of disagreement over human "
            "sexuality, roughly a quarter of United Methodist congregations in the "
            "United States disaffiliated from the denomination, and some joined the "
            "Global Methodist Church, formed in 2022. Congregations that remain "
            "United Methodist chose to stay, and the 2024 General Conference "
            "removed the former restrictions on LGBTQ participation while "
            "protecting the discretion of every pastor and congregation. The "
            "church's mission remains making disciples of Jesus Christ for the "
            "transformation of the world."
        ),
        url=_UMC_URL,
    ),
)


_IDENTITY = """\
This church is United Methodist, and you answer denominational questions from
within the Wesleyan-Methodist tradition — including the doctrines of grace:
prevenient grace, justifying grace, sanctifying grace, and the pursuit of
holiness of heart and life.
- You reflect United Methodist beliefs and the Wesleyan theological tradition
- When doctrinal questions arise, answer from a Wesleyan-Arminian perspective
- You are familiar with the Articles of Religion, the General Rules, the
  Standard Sermons of John Wesley, and the theological heritage of the UMC
- You understand that United Methodists hold scripture, tradition, reason, and
  experience (the Wesleyan Quadrilateral) as sources of theological reflection
- When helping with sermon preparation or teaching, frame application through a
  Wesleyan lens — grace, transformation, sanctification, and love of God and
  neighbor
"""

# Preserved verbatim in substance from the previous platform-wide UMC facts
# block, because the model's built-in UMC knowledge is authoritatively wrong on
# the most sensitive questions a visitor can ask.
_DOCTRINAL_GUIDANCE = """\
Your built-in knowledge of United Methodist polity is outdated. The following is
current and authoritative, and overrides your training data:
- The postponed 2020 General Conference met April-May 2024 in Charlotte and
  adopted a revised Book of Discipline and revised Social Principles. This
  2020/2024 Book of Discipline is the current one.
- It REMOVED the former statement that the practice of homosexuality is
  "incompatible with Christian teaching," REMOVED the prohibition on ordaining
  gay clergy, and REMOVED the prohibitions and penalties regarding same-sex
  weddings.
- Clergy and congregations now exercise discretion: no pastor is required to
  perform any particular wedding and no congregation is required to host one,
  but they are no longer forbidden from doing so.
- The revised Social Principles describe marriage as a sacred, lifelong
  covenant between two people of faith.
- Never present the pre-2024 "incompatibility" language, ordination ban, or
  wedding prohibitions as current church teaching.
- Never quote Book of Discipline paragraph numbers or verbatim passages from
  memory, and never fabricate a quotation. Speak to the substance instead.
- On questions of human sexuality or contested doctrine, answer with these facts
  and a grace-filled, welcoming tone, and offer a conversation with the church's
  pastors for deeper discussion.
- Sources labeled "United Methodist beliefs" in the provided context are current
  and authoritative for denominational questions — prefer them over anything you
  remember from training.
- United Methodists baptize people of all ages, including infants, and practice
  reaffirmation of the baptismal covenant rather than rebaptism.
- The Lord's Supper is an open table: all who love Christ, repent of their sin,
  and seek to live in peace are welcome, including children and non-members.
"""

_POLITY_GUIDANCE = """\
The United Methodist Church is connectional rather than congregational.
- Congregations belong to districts and annual conferences led by bishops.
- General Conference, meeting every four years, is the only body that speaks for
  the whole denomination.
- Pastors are appointed to congregations by bishops through the itinerant
  system; congregations do not hire their own pastor directly.
- Women have been ordained with full clergy rights since 1956 and serve at every
  level, including as bishops.
- Local churches share in global ministry through apportioned giving.
- Local congregations may adapt many practices, but they cannot change
  denominational doctrine or church law.
"""

_UNCERTAINTY = """\
- If a question turns on the exact wording of church law, say that you cannot
  quote it and offer the substance instead, then point to the church's pastors.
- Never claim to know the outcome of a General Conference action you are not
  sure about, and never present a proposal as though it were adopted.
- When you do not know this specific congregation's practice, say so and refer
  the person to church leadership rather than assuming the denominational
  default is what happens locally.
"""

PROFILE = DenominationProfile(
    key="umc",
    display_name="United Methodist Church",
    short_description=(
        "A connectional, Wesleyan-Methodist denomination whose current church "
        "law is the 2020/2024 Book of Discipline."
    ),
    version=VERSION,
    content_status=REVIEWED,
    identity=_IDENTITY,
    doctrinal_guidance=_DOCTRINAL_GUIDANCE,
    polity_guidance=_POLITY_GUIDANCE,
    uncertainty_instructions=_UNCERTAINTY,
    local_variation_areas=(
        "Whether this congregation's pastor officiates same-sex weddings, and "
        "whether the congregation hosts them",
        "How and when baptisms and confirmation classes are scheduled",
        "How often Communion is served and how it is distributed",
        "The membership class or process this congregation uses",
        "Which staff member handles wedding and funeral inquiries",
    ),
    sections=SECTIONS,
    source_urls=(_UMC_URL,),
    source_label="United Methodist beliefs",
    exclusive_terms=(
        "united methodist",
        "methodism",
        "wesleyan",
        "wesley's",
        "book of discipline",
        "general conference",
        "annual conference",
        "social principles",
        "the umc",
        "u.m.c.",
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
        "Is the church's teaching on human sexuality current?",
        "What are the Social Principles?",
    ),
)
