"""Source client app/sources/websearch.py: people search queries, hits and web facts.
SYNTHETIC data only (gotcha 8)."""

import asyncio

import pytest

from app import llm
from app.sources import is_blocked, websearch
from tests.fakes import NAME, WEB_NAME_RESULTS, WEB_RESULTS, FakeModel, sources_fixture  # noqa: F401 - fixture


def test_malformed_url_is_dropped_not_raised():
    assert is_blocked("http://[bad/x") is True
    hits = websearch.to_hits([{"url": "http://[bad/x", "title": "bad"}, WEB_RESULTS[0]])
    assert [h["url"] for h in hits] == [WEB_RESULTS[0]["url"]]


def test_web_titles_are_capped():
    hits = websearch.to_hits([WEB_RESULTS[0] | {"title": "T" * 5000}])
    assert len(hits[0]["title"]) == websearch.TITLE_MAX




def test_websearch_runs_expert_bare_hint_and_field_location_queries(sources):
    model, _ = sources()
    hints = {"field": "toxicology", "location": "Ohio", "affiliation": "Example State University"}
    hits = asyncio.run(websearch.search_people(NAME, hints))
    assert [(h["url"], h["query"]) for h in hits] == [
        ("https://example.edu/faculty/rivera", "expert"),  # blocked and repeated URLs dropped
        ("https://www.jurispro.com/profile/0000-alex-rivera", "bare"),
        ("https://example.org/clinic/alex-rivera", "bare"),
    ]
    assert hits[0]["source"] == "web" and hits[0]["title"] == "Toxicology expert witness"
    assert all(c["web_search"] is True and c["schema"] is None for c in model.calls)
    assert [c["input"] for c in model.calls] == [
        'Search query: "Alex Rivera" expert witness',
        'Search query: "Alex Rivera"',
        'Search query: "Alex Rivera" expert witness toxicology Ohio Example State University',
        'Search query: "Alex Rivera" toxicology Ohio',  # no "expert witness"
    ]


def test_websearch_field_location_query_needs_field_or_location():
    queries = websearch.build_queries(NAME, {"field": None, "location": None, "affiliation": "Example Lab"})
    assert [k for k, _ in queries] == ["expert", "bare", "hint"]
    queries = websearch.build_queries(NAME, {"location": 'Gilbert, "AZ"'})
    assert queries[-1] == ("field-location", '"Alex Rivera" Gilbert, AZ')


def test_listing_pages_are_dropped_before_the_per_query_cap():
    listings = [{"url": f"https://example.com/search?q=x&page={i}", "title": "Results"} for i in range(15)]
    person = {"url": "https://example.edu/faculty/rivera", "title": "Faculty page"}
    hits = websearch.to_hits(listings + [person], "bare")
    assert [h["url"] for h in hits] == [person["url"]]


def test_websearch_without_hints_sends_two_queries(sources):
    model, _ = sources()
    asyncio.run(websearch.search_people('Alex "Rivera"', None))
    assert [c["input"] for c in model.calls] == [
        'Search query: "Alex Rivera" expert witness', 'Search query: "Alex Rivera"']


def test_websearch_page_found_by_name_and_hint_query_counts_as_expert(sources):
    sources(model=FakeModel(web=[[], WEB_NAME_RESULTS, WEB_NAME_RESULTS[:1]]))
    hits = asyncio.run(websearch.search_people(NAME, {"field": "economics"}))
    assert [h["query"] for h in hits] == ["hint", "bare", "bare"]
    assert hits[0]["url"] == WEB_NAME_RESULTS[0]["url"]


def test_websearch_one_failed_query_is_reported_all_failed_raises(sources):
    sources(model=FakeModel(web=[llm.ModelError("down"), WEB_NAME_RESULTS]))
    failed = []
    hits = asyncio.run(websearch.search_people(NAME, None, failed))
    assert [h["query"] for h in hits] == ["bare", "bare", "bare"]
    assert failed == ["expert query failed: down"]
    down = llm.ModelError("down")
    sources(model=FakeModel(web=[[], down, down, down]))
    failed = []
    asyncio.run(websearch.search_people(NAME, {"field": "toxicology"}, failed))
    assert failed == ["bare query failed: down", "hint query failed: down",
                      "field-location query failed: down"]
    sources(model=FakeModel(web=[llm.ModelError("down"), llm.ModelError("down too")]))
    with pytest.raises(llm.ModelError, match="^down$"):
        asyncio.run(websearch.search_people(NAME, None))


def test_websearch_falls_back_to_citations(sources, monkeypatch):
    sources()
    result = llm.ModelResult(citations=[{"type": "url_citation", "url": "https://example.org/a", "title": "A"}])

    async def fake(*args, **kwargs):
        return result

    monkeypatch.setattr(llm, "call_model", fake)
    hits = asyncio.run(websearch.search_people(NAME, None))
    assert [h["url"] for h in hits] == ["https://example.org/a"]


def test_websearch_one_query_drops_blocked_and_caps_the_query(sources):
    model, _ = sources(model=FakeModel(web=[WEB_RESULTS]))
    hits = asyncio.run(websearch.search_query(NAME, '"Alex Rivera" ' + "x" * 500))
    assert [h["url"] for h in hits] == ["https://example.edu/faculty/rivera"]  # blocked and repeat dropped
    assert hits[0]["query"] == "follow-up"
    assert model.calls[0]["web_search"] is True
    assert len(model.calls[0]["input"]) == len("Search query: ") + websearch.QUERY_MAX


@pytest.mark.parametrize("query, blocked", [
    ('"Alex Rivera" site:linkedin.com', True),
    ('"Alex Rivera" site:www.LinkedIn.com/in', True),
    ("Alex Rivera (site:scholar.google.com)", True),
    ("Alex Rivera dauberttracker.com", True),
    ("Alex J. Rivera Ph.D. ... U.S.", False),  # dots that are not sites
    ("Alex Rivera site:example.edu", False),
    # glued to punctuation or behind other operators (QA bypasses)
    ("Alex Rivera|site:linkedin.com", True),
    ('"Alex Rivera"site:linkedin.com', True),
    ("Alex Rivera inurl:linkedin.com", True),
    ("Alex Rivera related:linkedin.com", True),
    ("Alex Rivera cache:linkedin.com", True),
    ("Alex Rivera intitle:expert site:uk.linkedin.com/in", True),
    ("Alex Rivera lnkd.in/abc", True),
    ("Alex Rivera \uff4c\uff49\uff4e\uff4b\uff45\uff44\uff49\uff4e\uff0e\uff43\uff4f\uff4d", True),  # full-width
    ("Alex Rivera linkedin\u3002com", True),  # ideographic full stop
    ("Alex Rivera linkedin\uff0ecom", True),  # full-width full stop
    ("Alex Rivera linkedin\uff61com", True),  # half-width ideographic full stop
    ("Alex Rivera linked\u200bin.com", True),  # zero-width space
    ("Alex Rivera linked\u200cin.com", True),  # zero-width non-joiner
    ("Alex Rivera linked\u200din.com", True),  # zero-width joiner
    ("Alex Rivera linked\u2060in.com", True),  # word joiner
    ("Alex Rivera linked\ufeffin.com", True),  # BOM
    ("Alex Rivera linked\u00adin.com", True),  # soft hyphen
    ("Alex Rivera site:linkedin.\u200bcom", True),
    ("Alex Rivera expert witness Michigan", False),
    ("Smith v. Jones 2019", False),
    ("Alex Rivera notlinkedin.com", False),
])
def test_websearch_query_naming_a_banned_site(query, blocked):
    assert websearch.names_blocked_site(query) is blocked


# --- Web search facts ---------------------------------------------------------------------------

SNIPPET = "SYNTHETIC-WEB-SNIPPET Alex J. Rivera holds an active medical licence in Ohio since the year 2004 per board"
FACT_RESULTS = [
    {"url": "https://board.example.gov/lookup/rivera", "title": "Alex J. Rivera - licence record", "snippet": SNIPPET},
    {"url": "https://www.linkedin.com/in/x", "title": "Alex Rivera | LinkedIn", "snippet": "blocked"},
    {"url": "https://news.example.com/other", "title": "Local news", "snippet": "A different Alex Sample story"},
]
FACT_RESULTS_2 = [
    {"url": "https://board.example.gov/lookup/rivera", "title": "duplicate", "snippet": SNIPPET},
    {"url": "https://example.org/certified/alex-rivera", "title": "Alex Rivera, board certified",
     "snippet": "SYNTHETIC-WEB-SNIPPET certification page"},
]
FACTS = {"results": [
    {"ref": "W1", "about_person": True, "facts": [
        "Holds an Ohio medical licence.", "x" * 300, SNIPPET[22:80], "Licensed since 2004.", "One too many."]},
    {"ref": "W2", "about_person": False, "facts": ["Not this person."]},
    {"ref": "W3", "about_person": True, "facts": ["Board certified in toxicology."]},
    {"ref": "W9", "about_person": True, "facts": ["Made up result."]},
]}


def test_search_facts_filters_and_never_returns_snippets(sources):
    fake = FakeModel(web=[FACT_RESULTS, FACT_RESULTS_2], json=[FACTS])
    sources(model=fake)
    out = asyncio.run(websearch.search_facts("Alex Rivera", ["q1", "q2"], "licences, board certification, discipline"))
    assert out == [
        {"url": "https://board.example.gov/lookup/rivera", "title": "Alex J. Rivera - licence record",
         "facts": ["Holds an Ohio medical licence.", "x" * websearch.FACT_MAX, "Licensed since 2004."]},
        {"url": "https://example.org/certified/alex-rivera", "title": "Alex Rivera, board certified",
         "facts": ["Board certified in toxicology."]},
    ]
    assert "SYNTHETIC-WEB-SNIPPET" not in repr(out)
    web_calls = [c for c in fake.calls if c["web_search"]]
    assert len(web_calls) == 2 and all(c["schema"] is None for c in web_calls)
    json_call = next(c for c in fake.calls if not c["web_search"])
    assert json_call["schema"] is websearch.FACT_SCHEMA and "linkedin" not in json_call["input"]


def test_search_facts_without_name_in_result_drops_it(sources):
    fake = FakeModel(web=[[FACT_RESULTS[2]]], json=[{"results": [
        {"ref": "W1", "about_person": True, "facts": ["Some fact."]}]}])
    sources(model=fake)
    assert asyncio.run(websearch.search_facts("Alex Rivera", ["q"], "expert testimony")) == []


def test_search_facts_errors(sources):
    sources(model=FakeModel(web=[llm.ModelError("down"), llm.ModelError("down too")]))
    with pytest.raises(llm.ModelError, match="^down$"):
        asyncio.run(websearch.search_facts("Alex Rivera", ["q1", "q2"], "expert testimony"))
    sources(model=FakeModel(web=[FACT_RESULTS], json=[llm.ModelError("json failed")]))
    with pytest.raises(llm.ModelError, match="json failed"):
        asyncio.run(websearch.search_facts("Alex Rivera", ["q"], "expert testimony"))
    fake = FakeModel(web=[], json=[])
    sources(model=fake)
    assert asyncio.run(websearch.search_facts("Alex Rivera", ["q"], "expert testimony")) == []
    assert all(c["web_search"] for c in fake.calls)  # no results: no fact call


def test_search_facts_with_context_uses_notes_and_url_name(sources):
    """Meta results often have no snippet: facts come from the search notes. A page naming the person
    in full only in its URL is kept ("Dr. Rivera" in the facts is fine); a same-name other is not."""
    quote = "has testified in more than forty synthetic exposure cases nationwide"
    notes = f'SYNTHETIC-NOTES https://experts.example.org/members/77-alex-j-rivera: Dr. Rivera "{quote}".'
    results = [
        {"url": "https://experts.example.org/members/77-alex-j-rivera", "title": "Expert profile", "snippet": ""},
        {"url": "https://law.example.org/team/rivera", "title": "Alex Rivera, attorney", "snippet": ""},
    ]
    facts = {"results": [
        {"ref": "W1", "about_person": True, "facts": ["Dr. Rivera testifies in toxicology cases.", f"He {quote}."]},
        {"ref": "W2", "about_person": False, "facts": ["A lawyer."]},
    ]}
    calls = []

    async def model(instructions, input, schema=None, web_search=False, output_type=None):
        calls.append((input, web_search))
        return llm.ModelResult(text=notes, results=results) if web_search else llm.ModelResult(data=facts)

    sources(model=model)
    out = asyncio.run(websearch.search_facts("Alex J. Rivera", ["q"], "expert testimony",
                                             context="toxicology, Columbus, OH"))
    assert out == [{"url": results[0]["url"], "title": "Expert profile",
                    "facts": ["Dr. Rivera testifies in toxicology cases."]}]  # the quoted copy is dropped
    (search_input, _), (fact_input, _) = calls
    assert "Person: Alex J. Rivera (toxicology, Columbus, OH)" in search_input
    assert "Person: Alex J. Rivera (toxicology, Columbus, OH)" in fact_input and notes in fact_input
    assert "SYNTHETIC-NOTES" not in repr(out)
