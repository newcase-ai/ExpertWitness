"""M3 source additions: opinion passages, OpenAlex author/works, web facts. SYNTHETIC data only (gotcha 8)."""

import asyncio

import httpx
import pytest

from app import llm, store
from app.sources import SourceSkipped, courtlistener, openalex, websearch
from tests.fakes import FakeModel, sources_fixture  # noqa: F401 - fixture

FILLER = "The court considered the record at length. " * 20
OPINION_TEXT = (
    FILLER + "Plaintiff offered the testimony of Dr. Alex J. Rivera, a toxicologist. " + FILLER
    + "Dr. Rivera also said many things. " + FILLER
    + "The motion to exclude Rivera, Alex is denied. " + FILLER
)


def opinion_route(body):
    return {"www.courtlistener.com": lambda request: httpx.Response(200, json=body)}


# --- CourtListener opinion passages -------------------------------------------------------------


def test_opinion_passages_need_token(sources, monkeypatch):
    sources()
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.opinion_passages(1, "Alex Rivera"))


def test_opinion_passages_never_use_surname_alone(sources):
    _, seen = sources(routes=opinion_route({"id": 1, "plain_text": OPINION_TEXT}))
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.opinion_passages(1, "Dr. Rivera"))
    assert seen == []


def test_opinion_passages_centre_on_full_name(sources):
    _, seen = sources(routes=opinion_route({"id": 7, "plain_text": OPINION_TEXT, "html_with_citations": ""}))
    found = asyncio.run(courtlistener.opinion_passages(7, "Alex Rivera"))
    assert len(found) == 2  # "Dr. Rivera" alone is not a mention (gotcha 13)
    assert "Alex J. Rivera" in found[0] and "Rivera, Alex" in found[1]
    collapsed = " ".join(OPINION_TEXT.split())
    assert all(p in collapsed and len(p) <= courtlistener.PASSAGE_CHARS for p in found)
    request = seen[0]
    assert request.url.path == "/api/rest/v4/opinions/7/"
    assert request.url.params["fields"] == "id,plain_text,html_with_citations"
    assert request.headers["Authorization"] == "Token test-token"
    assert store.calls_since("courtlistener", 60) == 1
    asyncio.run(courtlistener.opinion_passages(7, "Alex Rivera"))  # cached: no request, no count
    assert len(seen) == 1 and store.calls_since("courtlistener", 60) == 1


def test_opinion_passages_from_html_with_accents_and_cap(sources):
    html = "".join(f"<p>{FILLER}Expert <a href='#'>Álex Rivera</a> &amp; co.</p>" for _ in range(5))
    sources(routes=opinion_route({"id": 8, "plain_text": "", "html_with_citations": html}))
    found = asyncio.run(courtlistener.opinion_passages(8, "Alex Rivera"))
    assert len(found) == 3
    assert all("Álex Rivera & co." in p and "<" not in p for p in found)


def test_opinion_without_name_or_text_gives_no_passages(sources):
    sources(routes=opinion_route({"id": 9, "plain_text": FILLER + "Dr. Rivera testified."}))
    assert asyncio.run(courtlistener.opinion_passages(9, "Alex Rivera")) == []


def test_opinion_passages_middle_initial_optional(sources):
    """PLAN 3l: "Morgan Q. Example" is named "Dr. Morgan Example" or "Morgan Quinn Example"."""
    text = (FILLER + "The plaintiff called Dr. Morgan Example, a neurologist. " + FILLER
            + "Morgan Quinn Example then testified. " + FILLER + "Dr. Example said more. " + FILLER
            + "Morgan Z. Example is someone else. " + FILLER)
    sources(routes=opinion_route({"id": 11, "plain_text": text}))
    found = asyncio.run(courtlistener.opinion_passages(11, "Morgan Q. Example"))
    assert len(found) == 2
    assert "Dr. Morgan Example" in found[0] and "Morgan Quinn Example" in found[1]
    assert not any("Morgan Z. Example" in p for p in found)


def test_search_hit_reads_opinion_id_not_case_id(sources):
    result = {"absolute_url": "/opinion/500/doe-v-example/", "caseName": "Doe v. Example",
              "opinions": [{"id": 701, "snippet": "plain"}, {"id": 702, "snippet": "the <mark>Alex Rivera</mark>"}]}
    assert courtlistener.to_hit(result)["opinion_id"] == 702
    result["opinions"] = [{"id": 701, "snippet": "plain"}]
    assert courtlistener.to_hit(result)["opinion_id"] == 701


# --- CourtListener RECAP dockets (court_records, court_case) -------------------------------------


def docket_result(did, court, date, snippets, party=(), attorney=()):
    return {"docket_absolute_url": f"/docket/{did}/doe-v-example-co/", "caseName": "Doe v. Example Co.",
            "court": court, "dateFiled": date, "docketNumber": f"1:{did}-cv", "suitNature": "791 ERISA",
            "party": list(party), "attorney": list(attorney),
            "recap_documents": [{"absolute_url": f"/docket/{did}/{n}/doe-v-example-co/", "snippet": s,
                                 "short_description": f"Exhibit {n}", "entry_date_filed": date}
                                for n, s in enumerate(snippets, 1)]}


def test_search_dockets_full_name_only_and_fields(sources):
    body = {"results": [docket_result(5, "District Court, D. Example", "2021-02-08",
                                      ["no match here", "SYNTHETIC-CL reviewed by Dr. <mark>Alex J. Rivera</mark>, "
                                       "M.D. &amp; toxicologist " + "y" * 500], party=["Alex Rivera", "Example Co."])]}
    _, seen = sources(routes=opinion_route(body))
    [hit] = asyncio.run(courtlistener.search_dockets("Dr. Alex J. Rivera"))
    params = seen[0].url.params
    assert params["type"] == "r" and params["highlight"] == "on"
    assert params["q"] == '"Alex J. Rivera" OR "Alex Rivera"'  # no expert-word rule, never the surname alone
    assert hit["url"] == "https://www.courtlistener.com/docket/5/doe-v-example-co/"
    assert (hit["case_name"], hit["court"], hit["docket_number"], hit["date_filed"], hit["nature_of_suit"]) == (
        "Doe v. Example Co.", "District Court, D. Example", "1:5-cv", "2021-02-08", "791 ERISA")
    assert hit["named_as"] == ["party"]
    assert hit["documents"][1] == {"description": "Exhibit 2", "date_filed": "2021-02-08",
                                   "url": "https://www.courtlistener.com/docket/5/2/doe-v-example-co/"}
    [passage] = hit["passages"]  # only the snippet that highlights a match
    assert passage.startswith("SYNTHETIC-CL reviewed by Dr. Alex J. Rivera, M.D. & toxicologist")
    assert len(passage) == courtlistener.PASSAGE_CHARS and "<" not in passage
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.search_dockets("Dr. Rivera"))


def test_find_case_query_and_best_match(sources):
    body = {"results": [
        docket_result(1, "District Court, N.D. Example", "2015-03-01", ["no match here"]),
        docket_result(2, "Example Court of Appeals", "2019-06-06",
                      ["SYNTHETIC-CL the plaintiff's expert <mark>Morgan Example</mark>, M.D."]),
    ]}
    _, seen = sources(routes=opinion_route(body))
    found = asyncio.run(courtlistener.find_case('Doe v. "Example" Co.', "Dr. Morgan Q. Example",
                                                court="example court of appeals", year=2019))
    q = seen[0].url.params["q"]
    assert q.startswith('caseName:("Doe" AND "Example") AND (')
    people = q.split(") AND (", 1)[1]
    assert '"Morgan Q. Example"' in people and '"Morgan Example"' in people and '"Example"' not in people
    assert seen[0].url.params["type"] == "r" and seen[0].url.params["highlight"] == "on"
    assert found["url"] == "https://www.courtlistener.com/docket/2/doe-v-example-co/"
    assert (found["case_name"], found["court"], found["date_filed"], found["docket_number"]) == (
        "Doe v. Example Co.", "Example Court of Appeals", "2019-06-06", "1:2-cv")
    assert found["passages"] == ["SYNTHETIC-CL the plaintiff's expert Morgan Example, M.D."]
    assert len(seen) == 1 and store.calls_since("courtlistener", 60) == 1  # no second call


def test_find_case_none_skip_and_first_by_default(sources, monkeypatch):
    sources(routes=opinion_route({"results": []}))
    assert asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Morgan Example")) is None
    body = {"results": [docket_result(3, "A", "2001-01-01", []), docket_result(4, "B", "2002-01-01", [])]}
    sources(routes=opinion_route(body))
    found = asyncio.run(courtlistener.find_case("Roe v. Example Co.", "Morgan Example", year=1990))
    assert found["url"].endswith("/docket/3/doe-v-example-co/") and found["passages"] == []
    _, seen = sources()
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Dr. Example"))
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Morgan Example"))
    assert seen == []


def test_find_case_long_cv_name_uses_party_key_words(sources):
    _, seen = sources(routes=opinion_route({"results": []}))
    asyncio.run(courtlistener.find_case(
        "Jordan Avery vs. Patrol Officer Sam Lindqvist and the Town of Brookfield", "Morgan Example"))
    q = seen[0].url.params["q"]
    assert q.startswith('caseName:("Avery" AND "Lindqvist") AND (') and '"Morgan Example"' in q
    assert "Officer" not in q and "Brookfield" not in q and len(seen) == 1


def test_case_name_query_entities_and_fallback():
    q = courtlistener.case_name_query
    assert q("Quinn Harlow, MD vs. Northwind Lifestyles, Inc., ZZQ Management, Ltd.") == \
        'caseName:("Harlow" AND "Northwind Lifestyles")'
    assert q("Ada Lyle, Ben Orr, and Cy Pratt, et al. v. City of Brookfield") == 'caseName:("Lyle" AND "Brookfield")'
    assert q('Dee (Dr. "Ann") Roe versus Acme* Corp.') == 'caseName:("Roe" AND "Acme")'
    assert q('In re "Estate" of (Lyle)') == 'caseName:"In re Estate of Lyle"'  # no "v.": phrase as before


def test_find_case_strips_highlight_tags(sources):
    result = docket_result(5, "<mark>Example</mark> Court", "2019-06-06", ["<mark>Morgan Example</mark> &amp; co"])
    result["caseName"] = "<mark>Doe</mark> v. <mark>Example</mark> Co. &amp; Sons"
    result["docketNumber"] = "<em>1:5-cv</em>"
    sources(routes=opinion_route({"results": [result]}))
    found = asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Morgan Example"))
    assert (found["case_name"], found["court"], found["docket_number"], found["passages"]) == (
        "Doe v. Example Co. & Sons", "Example Court", "1:5-cv", ["Morgan Example & co"])
    opinion = {"absolute_url": "/opinion/5/doe-v-example-co/", "caseName": result["caseName"],  # candidate search
               "court": result["court"], "dateFiled": "2019-06-06",
               "opinions": [{"id": 1005, "snippet": "<mark>Morgan Example</mark> &amp; co"}]}
    hit = courtlistener.to_hit(opinion)
    assert hit["title"] == "Doe v. Example Co. & Sons" and hit["case"][1] == "Example Court"
    assert "<" not in hit["note"] + hit["snippet"]


def test_opinion_http_error_raises(sources):
    sources(routes={"www.courtlistener.com": (404, {"detail": "Not found."})})
    with pytest.raises(RuntimeError, match="404"):
        asyncio.run(courtlistener.opinion_passages(10, "Alex Rivera"))


# --- OpenAlex author and works ------------------------------------------------------------------

AUTHOR = {
    "id": "https://openalex.org/A0000000001", "display_name": "Alex J. Rivera",
    "orcid": "https://orcid.org/0000-0000-0000-0001", "works_count": 92, "cited_by_count": 1200,
    "summary_stats": {"h_index": 18}, "last_known_institutions": [{"display_name": "Example State University"}],
}
WORKS = {"results": [
    {"id": "https://openalex.org/W1", "title": "Synthetic toxicology study", "publication_year": 2021,
     "primary_location": {"source": {"display_name": "Journal of Examples"}},
     "doi": "https://doi.org/10.5555/SYNTH.1", "is_retracted": True, "cited_by_count": 4},
    {"id": "https://openalex.org/W2", "display_name": "Untitled synthetic work", "publication_year": None,
     "primary_location": None, "doi": None, "is_retracted": None},
]}


def test_openalex_author_stats(sources, monkeypatch):
    monkeypatch.setattr(openalex, "_budget", None)
    _, seen = sources(routes={"api.openalex.org": (200, AUTHOR)})
    assert asyncio.run(openalex.author("https://openalex.org/A0000000001")) == {
        "id": "A0000000001", "url": "https://openalex.org/A0000000001", "display_name": "Alex J. Rivera",
        "works_count": 92, "cited_by_count": 1200, "h_index": 18, "orcid": "0000-0000-0000-0001",
        "last_institution": "Example State University"}
    assert seen[0].url.path == "/authors/A0000000001"


def test_openalex_works_first_page_newest_first(sources, monkeypatch):
    monkeypatch.setattr(openalex, "_budget", None)
    _, seen = sources(routes={"api.openalex.org": (200, WORKS)})
    works = asyncio.run(openalex.works("A0000000001", limit=50))
    assert seen[0].url.params["filter"] == "author.id:A0000000001"
    assert seen[0].url.params["sort"] == "publication_year:desc"
    assert seen[0].url.params["per_page"] == "50" and "cursor" not in seen[0].url.params
    assert works[0] == {"id": "W1", "title": "Synthetic toxicology study", "year": 2021,
                        "venue": "Journal of Examples", "doi": "10.5555/synth.1",
                        "url": "https://doi.org/10.5555/synth.1", "is_retracted": True, "cited_by_count": 4}
    assert works[1]["doi"] is None and works[1]["url"] == "https://openalex.org/W2"
    assert works[1]["title"] == "Untitled synthetic work" and works[1]["is_retracted"] is False


def test_openalex_bad_id_and_budget(sources, monkeypatch):
    _, seen = sources()
    with pytest.raises(ValueError):
        asyncio.run(openalex.works("A1,author.id:A2"))
    monkeypatch.setattr(openalex, "_budget", (openalex._today(), 0.0))
    with pytest.raises(SourceSkipped):
        asyncio.run(openalex.author("A0000000001"))
    assert seen == []


# --- Web search facts ---------------------------------------------------------------------------

SNIPPET = "SYNTHETIC-WEB-SNIPPET Alex J. Rivera holds an active medical licence in Ohio since the year 2004 per board"
FACT_RESULTS = [
    {"url": "https://board.example.gov/lookup/rivera", "title": "Alex J. Rivera - licence record", "snippet": SNIPPET},
    {"url": "https://www.linkedin.com/in/alex-rivera", "title": "Alex Rivera | LinkedIn", "snippet": "blocked"},
    {"url": "https://news.example.com/other", "title": "Local news", "snippet": "A different Alex Morgan story"},
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
