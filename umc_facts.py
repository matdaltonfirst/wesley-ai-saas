"""Platform-maintained United Methodist denominational knowledge.

The AI model's training data predates the May 2024 General Conference, so its
built-in UMC knowledge is stale and it will otherwise answer doctrine questions
confidently from the old Book of Discipline. These sections — written in our
own words, no copyrighted text — are injected as citable retrieval sources for
every church on the platform. Update this file when General Conference acts.

Maintainers: keep sections short, factual, and denominationally accurate.
"""

_UMC_URL = "https://www.umc.org/en/what-we-believe"

SECTIONS = [
    {
        "key": "current-discipline",
        "title": "The current Book of Discipline (2020/2024)",
        "content": (
            "The current Book of Discipline of The United Methodist Church is the "
            "2020/2024 edition, adopted when the postponed 2020 General Conference "
            "met in Charlotte, North Carolina in April and May of 2024. It replaced "
            "the 2016 edition and includes revised Social Principles. Any statements "
            "from earlier editions — including the former restrictive language on "
            "human sexuality — are no longer current church law."
        ),
    },
    {
        "key": "marriage-sexuality",
        "title": "Marriage and human sexuality",
        "content": (
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
    },
    {
        "key": "baptism",
        "title": "Baptism",
        "content": (
            "United Methodists baptize people of all ages, including infants, "
            "believing God's grace is at work in a person before they can respond — "
            "what Wesleyans call prevenient grace. Baptism may be by sprinkling, "
            "pouring, or immersion, and it is received once: rather than rebaptism, "
            "the church offers reaffirmation of the baptismal covenant. Children "
            "baptized as infants later affirm the faith for themselves at "
            "confirmation."
        ),
    },
    {
        "key": "communion",
        "title": "Holy Communion (the open table)",
        "content": (
            "The United Methodist Church practices an open table: all who love "
            "Christ, earnestly repent of their sin, and seek to live in peace with "
            "one another are welcome to receive Communion — including children and "
            "guests who are not members of the congregation or the denomination. "
            "United Methodists typically use unfermented grape juice, a practice "
            "rooted in the church's historic witness on alcohol, and understand "
            "Christ to be truly present in the sacrament without defining the "
            "mystery precisely."
        ),
    },
    {
        "key": "membership",
        "title": "Church membership",
        "content": (
            "People join a United Methodist congregation by profession of faith, by "
            "transfer from another congregation, or through confirmation. Members "
            "take vows to faithfully participate in the church's ministries by "
            "their prayers, their presence, their gifts, their service, and their "
            "witness. Baptism is a prerequisite; those not yet baptized are "
            "baptized when they join."
        ),
    },
    {
        "key": "clergy-ordination",
        "title": "Clergy and ordination",
        "content": (
            "United Methodist clergy include elders (ordained to word, sacrament, "
            "order, and service), deacons (ordained to word, service, compassion, "
            "and justice), and licensed local pastors. Women have been ordained "
            "with full clergy rights since 1956 and serve at every level, including "
            "as bishops. Pastors are appointed to congregations by bishops in an "
            "itinerant system rather than hired directly. Following the 2024 "
            "General Conference, sexual orientation is not a bar to ordination."
        ),
    },
    {
        "key": "wesleyan-theology",
        "title": "Wesleyan understanding of grace",
        "content": (
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
    },
    {
        "key": "structure",
        "title": "How the church is organized",
        "content": (
            "The United Methodist Church is connectional: congregations are joined "
            "in districts and annual conferences, led by bishops, with General "
            "Conference — meeting every four years — as the only body that speaks "
            "for the whole denomination. Local churches support shared global "
            "ministries through apportioned giving. The 2024 General Conference "
            "also approved a regionalization plan allowing different world regions "
            "to adapt some church rules to their own contexts."
        ),
    },
    {
        "key": "social-principles",
        "title": "The Social Principles",
        "content": (
            "The Social Principles, substantially revised in 2024, express the "
            "church's teaching on contemporary life in four areas: the Community "
            "of All Creation (care for the natural world), the Economic Community, "
            "the Social Community, and the Political Community. They call United "
            "Methodists to environmental stewardship, human dignity, and justice. "
            "They are teaching documents meant to guide conscience and witness "
            "rather than binding church law."
        ),
    },
    {
        "key": "recent-history",
        "title": "Recent denominational history",
        "content": (
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
    },
]


def score_denomination_chunks(question: str, top_n: int = 3) -> list[tuple[int, dict]]:
    """Score UMC sections for a question.

    Uses a gentler threshold than find_relevant_chunks: a doctrine question
    often shares exactly one decisive keyword with its section ("homosexuality",
    "baptize"), and missing it means the model falls back to stale training
    data — the failure this layer exists to prevent.
    """
    from documents import extract_keywords, score_chunk

    keywords = extract_keywords(question)
    if not keywords:
        return []
    scored = [(score_chunk(c, keywords), c) for c in load_denomination_chunks()]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(score, c) for score, c in scored[:top_n] if score > 0]


def load_denomination_chunks() -> list[dict]:
    """UMC facts as citable retrieval chunks (same shape as document chunks)."""
    return [
        {
            "content": f"{section['title']}\n{section['content']}",
            "source": f"United Methodist beliefs: {section['title']}",
            "location": _UMC_URL,
            "type": "denomination",
        }
        for section in SECTIONS
    ]
