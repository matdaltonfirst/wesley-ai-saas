"""Tests for the per-group citation floor in build_cited_context.

Citation slots used to be filled greedily in group order, so a group late in
the list could be shut out entirely. The case that mattered: on a doctrine
question, hits from documents and the website consumed every slot and the
reviewed denominational section never reached the model — silently, with no
error and no log line, in the one feature the profile layer exists to serve.
"""

from unittest.mock import patch

from documents import build_cited_context


def _web(title, url, content="..."):
    return {"content": content, "source": title, "location": url}


def _doc(name, page, content="..."):
    return {"content": content, "source": name, "location": f"Page {page}"}


def _denom(title, content="Baptism is administered to people of all ages."):
    return {
        "content": content,
        "source": f"United Methodist beliefs: {title}",
        "location": "https://www.umc.org/en/what-we-believe",
        "type": "denomination",
        "denomination": "umc",
    }


def _scored(chunks, score=10):
    return [(score, c) for c in chunks]


class TestDenominationalStarvation:
    """The regression this floor exists to prevent."""

    def test_denominational_section_survives_a_crowded_field(self):
        """Four higher-scoring web/doc hits must not evict the theology chunk."""
        groups = [
            _scored([_doc("Membership Guide.pdf", 1)], score=12),
            _scored([
                _web("Baptism", "https://church.org/baptism"),
                _web("Our Beliefs", "https://church.org/beliefs"),
                _web("New Here", "https://church.org/new"),
            ], score=18),
            [],                                        # calendar: no hits
            [],                                        # sermons: no hits
            _scored([_denom("Baptism")], score=6),     # lowest score, last group
        ]
        context, citations = build_cited_context(groups)

        titles = [c["title"] for c in citations]
        assert "United Methodist beliefs: Baptism" in titles
        # The chunk must reach the model, not merely appear in the sidebar.
        assert "Baptism is administered to people of all ages." in context

    def test_denominational_chunk_is_numbered_consistently(self):
        """Its [n] marker must match its position in the citation list, or the
        model cites one source and the visitor is shown another."""
        groups = [
            _scored([_web(f"Page {i}", f"https://church.org/{i}") for i in range(4)]),
            _scored([_denom("Communion", "All are welcome at the table.")]),
        ]
        context, citations = build_cited_context(groups)

        index = [c["title"] for c in citations].index(
            "United Methodist beliefs: Communion"
        )
        assert f"[Source {index + 1}:" in context

    def test_empty_groups_claim_no_slots(self):
        """A group with no hits must not reserve a slot it cannot fill."""
        groups = [
            _scored([_web(f"Page {i}", f"https://church.org/{i}") for i in range(6)]),
            [], [], [],
        ]
        _, citations = build_cited_context(groups)
        assert len(citations) == 4


class TestFloorDoesNotInflateResults:
    def test_single_group_still_respects_the_limit(self):
        groups = [_scored([_web(f"Page {i}", f"https://church.org/{i}") for i in range(10)])]
        _, citations = build_cited_context(groups)
        assert len(citations) == 4

    def test_cap_grows_only_as_far_as_the_floors_require(self):
        """Five contributing groups need five slots — and get exactly five."""
        groups = [
            _scored([_web(f"A{i}", f"https://church.org/a{i}") for i in range(3)]),
            _scored([_web(f"B{i}", f"https://church.org/b{i}") for i in range(3)]),
            _scored([_web(f"C{i}", f"https://church.org/c{i}") for i in range(3)]),
            _scored([_web(f"D{i}", f"https://church.org/d{i}") for i in range(3)]),
            _scored([_web(f"E{i}", f"https://church.org/e{i}") for i in range(3)]),
        ]
        _, citations = build_cited_context(groups)
        assert len(citations) == 5
        # One from each group, in group-priority order.
        assert [c["title"] for c in citations] == ["A0", "B0", "C0", "D0", "E0"]

    def test_citations_are_numbered_in_group_order(self):
        """The floor decides *which* sources get in; numbering still follows the
        group order, so citation [1] is the highest-priority source as before."""
        groups = [
            _scored([_web("A0", "https://church.org/a0"), _web("A1", "https://church.org/a1")]),
            _scored([_web("B0", "https://church.org/b0"), _web("B1", "https://church.org/b1")]),
        ]
        _, citations = build_cited_context(groups)
        assert [c["title"] for c in citations] == ["A0", "A1", "B0", "B1"]

    def test_leftover_slots_favour_earlier_groups(self):
        """Three groups take one slot each; the spare goes to the first group,
        not to whichever group happens to come last."""
        groups = [
            _scored([_web("A0", "https://church.org/a0"), _web("A1", "https://church.org/a1")]),
            _scored([_web("B0", "https://church.org/b0"), _web("B1", "https://church.org/b1")]),
            _scored([_web("C0", "https://church.org/c0"), _web("C1", "https://church.org/c1")]),
        ]
        _, citations = build_cited_context(groups)
        assert [c["title"] for c in citations] == ["A0", "A1", "B0", "C0"]


class TestExistingBehaviourPreserved:
    def test_document_pages_still_collapse_to_one_citation(self):
        groups = [_scored([_doc("Handbook.pdf", 2), _doc("Handbook.pdf", 5)])]
        _, citations = build_cited_context(groups)
        assert len(citations) == 1
        assert citations[0]["location"] == "Pages 2, 5"

    def test_zero_scored_chunks_are_excluded(self):
        groups = [_scored([_web("Relevant", "https://church.org/a")], score=5)
                  + _scored([_web("Irrelevant", "https://church.org/b")], score=0)]
        _, citations = build_cited_context(groups)
        assert [c["title"] for c in citations] == ["Relevant"]

    def test_typed_chunk_with_a_url_still_links(self):
        """A denominational section carries an explicit type and a URL — its
        citation must still resolve to a link rather than a bare location."""
        _, citations = build_cited_context([_scored([_denom("Grace")])])
        assert citations[0]["url"] == "https://www.umc.org/en/what-we-believe"
        assert citations[0]["location"] == "Website"
        assert citations[0]["type"] == "denomination"


class TestEndToEndThroughTheWidget:
    def test_doctrine_question_cites_the_denominational_section(self, client, church):
        """The full widget path, with a website that would previously win every slot."""
        web_pages = [
            _web("Baptism", "https://church.org/baptism", "Baptism services are scheduled monthly."),
            _web("Our Beliefs", "https://church.org/beliefs", "We believe in baptism and grace."),
            _web("New Here", "https://church.org/new", "Baptism classes meet in room 4."),
            _web("Contact", "https://church.org/contact", "Ask us about baptism."),
        ]
        with patch("routes.widget.load_chatbot_documents", return_value=[]), \
             patch("routes.widget.load_curated_content", return_value=[]), \
             patch("routes.widget.load_church_web_content", return_value=web_pages), \
             patch("routes.widget.call_gemini") as gemini:
            gemini.return_value = "We baptize people of all ages. [5]"
            res = client.post("/api/widget/chat", json={
                "church_id": church.id,
                "question": "Do you baptize infants?",
            })

        assert res.status_code == 200
        model_context = gemini.call_args.args[1]
        assert "United Methodist beliefs" in model_context
