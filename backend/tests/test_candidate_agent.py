import asyncio
import copy

import httpx
import pytest

from app import candidate_agent, llm
from app.models import Candidate
from app.sources import courtlistener

real_call_model = llm.call_model  # the fixture swaps in a FakeModel
from app.evidence import LIMIT_REACHED, Run
from tests.fakes import (  # noqa: F401 - fixture
    COURTLISTENER_SEARCH,
    GROUPING,
    HANG,
    NAME,
    WEB_NAME_RESULTS,
    WEB_RESULTS,
    FakeModel,
    author,
    call,
    opinion,
    sources_fixture,
)


class Steps:
    def __init__(self):
        self.calls = []

    def __call__(self, step, status, detail=None):
        self.calls.append((step, status, detail))

    def last(self, step):
        return [c for c in self.calls if c[0] == step][-1]


def run(name=NAME, hints=None):
    steps = Steps()
    candidates, failed = asyncio.run(candidate_agent.find_candidates(name, hints, steps))
    return candidates, failed, steps


def test_find_candidates_groups_merges_and_cites(sources):
    model, _ = sources()
    candidates, failed, steps = run(hints={"field": "toxicology"})
    assert failed == []
    # ghost dropped (no evidence); the chiropractor (no expert signal) is listed last, flagged
    assert [c.display_name for c in candidates] == ["Alex J. Rivera", "Alex Rivera", "Alex Rivera"]
    assert [c.expert_signals for c in candidates] == [
        ["court_opinion_expert", "web_expert_witness"], ["expert_directory"], []]
    assert [c.expert_evidence for c in candidates] == [True, True, False]

    tox = candidates[0]
    assert tox.candidate_id == ""  # ids are assigned by the job code
    assert tox.identifiers.openalex == ["A0000000001", "A0000000002"]
    assert tox.identifiers.orcid == "0000-0000-0000-0001"
    assert "merged by name only" in tox.match_notes
    assert [e.url for e in tox.evidence] == [  # E99 (unknown) dropped
        "https://api.openalex.org/authors/A0000000001",
        "https://api.openalex.org/authors/A0000000002",
        "https://www.courtlistener.com/opinion/1/doe-v-example-co/",
        "https://example.edu/faculty/rivera",
    ]
    assert tox.evidence[1].note == "2 works"  # code note when the AI gave none
    assert candidates[1].identifiers.openalex == ["A0000000003"]  # OpenAlex attached to an expert

    # every fact has a source URL; third-party snippets never leave the job
    for c in candidates:
        assert c.evidence and all(e.url.startswith("https://") for e in c.evidence)
        dumped = c.model_dump_json()
        assert "SYNTHETIC-WEB-SNIPPET" not in dumped and "SYNTHETIC-CL-SNIPPET" not in dumped

    group_call = model.calls[-1]
    assert group_call["schema"] is candidate_agent.GROUP_SCHEMA and group_call["web_search"] is False
    assert "E5 [web]" in group_call["input"] and "linkedin" not in group_call["input"]
    assert "E7 [web]" in group_call["input"] and "E8 [web]" not in group_call["input"]  # repeat URL
    assert "found by: name search" in group_call["input"]
    assert "expert witness" in group_call["instructions"].lower()
    assert [c["input"] for c in model.calls if c["web_search"]] == [
        'Search query: "Alex Rivera" expert witness',
        'Search query: "Alex Rivera"',
        'Search query: "Alex Rivera" expert witness toxicology',
        'Search query: "Alex Rivera" toxicology',
    ]
    assert "Morgan" not in group_call["input"]  # filtered by name before the AI sees it
    assert steps.last("group_candidates") == ("group_candidates", "done", "3 candidates")
    for key in ("openalex", "courtlistener", "web_search"):
        assert steps.last(key)[1] == "done"


def test_openalex_ids_with_different_orcids_are_kept_apart(sources):
    grouping = copy.deepcopy(GROUPING)
    grouping["people"][0]["evidence"].append({"ref": "E3", "note": None})
    grouping["people"] = grouping["people"][:1]
    sources(model=FakeModel(web=[WEB_RESULTS], json=[grouping]))
    candidates, _, _ = run()
    # the record kept apart is left out, never a candidate of its own
    assert [c.identifiers.openalex for c in candidates] == [["A0000000001", "A0000000002"]]
    assert "1 OpenAlex record(s) left out" in candidates[0].match_notes
    assert "A0000000003" not in candidates[0].model_dump_json()


def test_merge_checks_every_pair_of_openalex_ids(sources):
    authors = {"results": [author("A0000000011", "Alex Rivera"),
                           author("A0000000012", "Alex Rivera", "0000-0000-0000-0012"),
                           author("A0000000013", "Alex J. Rivera", "0000-0000-0000-0013")]}
    person = GROUPING["people"][1] | {"evidence": [{"ref": f"E{i}", "note": None} for i in (1, 2, 3, 4)]}
    sources({"api.openalex.org": (200, authors)},
            model=FakeModel(web=[[]], json=[{"people": [person]}]))
    candidates, _, _ = run()
    assert [c.identifiers.openalex for c in candidates] == [["A0000000011", "A0000000012"]]
    assert candidates[0].identifiers.orcid == "0000-0000-0000-0012"
    assert "merged by name only" in candidates[0].match_notes
    assert "left out" in candidates[0].match_notes


def test_ai_text_is_capped_and_copied_snippet_text_dropped(sources):
    snippet = WEB_RESULTS[0]["snippet"]
    person = copy.deepcopy(GROUPING["people"][0])
    person |= {"summary": f"Bio: {snippet.upper()} and more.",
               "affiliations": [snippet, "Example State University"], "location": "x" * 500,
               "match_notes": "Toxicologist. " * 100}
    person["evidence"][3]["note"] = "copied: " + snippet
    sources(model=FakeModel(web=[WEB_RESULTS], json=[{"people": [person]}]))
    tox = run()[0][0]
    assert tox.summary == candidate_agent.SUMMARY_REMOVED
    assert len(tox.location) == 120 and len(tox.match_notes) <= 400
    assert tox.affiliations == ["Example State University"]
    web = next(e for e in tox.evidence if e.source == "web")
    assert web.note is None
    assert "SYNTHETIC-WEB-SNIPPET" not in tox.model_dump_json().upper()


def test_failed_and_skipped_sources_are_reported_not_raised(sources, monkeypatch):
    sources({"api.openalex.org": (500, {})})
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    candidates, failed, steps = run()
    assert {f.source: f.reason for f in failed} == {
        "openalex": "OpenAlex HTTP 500",
        "courtlistener": "COURTLISTENER_TOKEN is not set",
    }
    assert steps.last("openalex")[1] == "failed"
    assert steps.last("courtlistener")[1] == "skipped"
    assert candidates  # web hits were still grouped


def test_slow_source_times_out(sources, monkeypatch):
    sources(model=FakeModel(json=[{"people": []}], hang=True))
    monkeypatch.setattr(candidate_agent, "SOURCE_TIMEOUT", 0.05)
    _, failed, _ = run()
    assert [(f.source, f.reason) for f in failed] == [("web_search", "timed out")]


def test_grouping_failure_raises_with_all_failed_sources(sources):
    sources({"www.courtlistener.com": (500, {})},
            model=FakeModel(web=[WEB_RESULTS], json=[llm.ModelError("model returned invalid output")]))
    steps = Steps()
    with pytest.raises(candidate_agent.FindCandidatesError, match="AI grouping step failed") as err:
        asyncio.run(candidate_agent.find_candidates(NAME, None, steps))
    assert [(f.source, f.reason) for f in err.value.failed_sources] == [
        ("courtlistener", courtlistener.failure(500)),
        ("group_candidates", "model returned invalid output"),
    ]
    assert steps.last("group_candidates")[1] == "failed"


def test_no_hits_skips_the_ai(sources):
    model, _ = sources({"api.openalex.org": (200, {"results": []}),
                        "www.courtlistener.com": (200, {"results": []})},
                       model=FakeModel(web=[[]]))
    candidates, failed, steps = run()
    assert candidates == [] and failed == []
    assert len(model.calls) == 2  # only the two web searches (expert and name-only)
    assert steps.last("group_candidates")[1] == "done"


def person(refs, note=None, summary="Expert.", name=NAME):
    return {"display_name": name, "summary": summary, "field": None, "affiliations": [],
            "location": None, "evidence": [{"ref": r, "note": note} for r in refs], "match_notes": None}


def test_candidates_are_ranked_court_then_directory_then_web(sources):
    people = [person(["E5"], summary="web"), person(["E6"], summary="directory"),
              person(["E4"], summary="court"),
              person(["E5", "E6"], summary="directory and web") | {"field": "toxicology"}]
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[{"people": people}]))
    candidates, _, _ = run()
    assert [c.summary for c in candidates] == ["court", "directory and web", "directory", "web"]
    assert candidates[1].expert_signals == ["expert_directory", "web_expert_witness"]


def test_same_case_twice_counts_once_with_lowest_opinion_id(sources):
    model, _ = sources()
    candidates, _, _ = run()
    court = [e.url for e in candidates[0].evidence if e.source == "courtlistener"]
    assert court == ["https://www.courtlistener.com/opinion/1/doe-v-example-co/"]
    assert "/opinion/2/" not in model.calls[-1]["input"]  # the AI never sees the duplicate


def test_dedupe_keeps_different_cases_and_hits_without_full_case_data():
    hits = [courtlistener.to_hit(o) for o in (
        opinion(3), opinion(2) | {"dateFiled": "2020-01-01"}, opinion(1),
        opinion(5) | {"dateFiled": ""}, opinion(4) | {"dateFiled": ""})]
    kept = [h["opinion_id"] for h in candidate_agent.dedupe_cases(hits)]
    assert kept == [2, 1, 5, 4]  # 3 and 1 are one case; a different date or no date is kept


@pytest.mark.parametrize("display, kept", [
    ("Alex Rivera", True), ("ALEX RIVERA", True), ("Dr. Alex J. Rivera, PhD", True),
    ("Alex Quinn Rivera", True), ("Alex Riveras", False), ("Rivera", False), ("Dr. Rivera", False),
    ("Alex Rivera-Stone", False), ("Alexa Rivera", False), ("A" * 500, False),
])
def test_display_name_must_match_the_searched_full_name(sources, display, kept):
    sources(model=FakeModel(web=[WEB_RESULTS], json=[{"people": [person(["E4"], name=display)]}]))
    candidates, _, _ = run()
    assert len(candidates) == int(kept)


def test_people_without_expert_signals_come_last_and_openalex_alone_is_dropped(sources):
    people = [person(["E1", "E2"], summary="openalex only"), person(["E7"], summary="clinic"),
              person(["E3", "E7"], summary="clinic and openalex"), person(["E6"], summary="directory")]
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[{"people": people}]))
    candidates, failed, _ = run()
    assert [c.summary for c in candidates] == ["directory", "clinic", "clinic and openalex"]
    assert [c.expert_evidence for c in candidates] == [True, False, False]
    assert candidates[2].identifiers.openalex == ["A0000000003"]  # OpenAlex supports a real page
    assert failed == []


def test_experts_take_the_capped_slots_first(sources):
    people = [person(["E7"], summary=f"clinic {i}") for i in range(12)] + [person(["E6"], summary="directory")]
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[{"people": people}]))
    candidates, _, _ = run()
    assert len(candidates) == candidate_agent.MAX_CANDIDATES
    assert candidates[0].summary == "directory" and not candidates[1].expert_evidence


def test_hint_matches_rank_above_tiers(sources):
    def located(refs, summary, location, field=None):
        return person(refs, summary=summary) | {"location": location, "field": field}

    people = [located(["E4"], "court elsewhere", "Exampleville, TX"),
              located(["E5"], "web in Ohio", "Columbus, Ohio", "toxicology"),
              located(["E4"], "court in Ohio", "Akron, OHIO", "Clinical Toxicology"),
              located(["E7"], "clinic in Ohio", "Ohio", "toxicology")]
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[{"people": people}]))
    candidates, _, _ = run(hints={"field": "toxicology", "location": "Ohio", "affiliation": None})
    # hint matches first (court > web > no signal inside), then the court expert with no hint match
    assert [c.summary for c in candidates] == [
        "court in Ohio", "web in Ohio", "clinic in Ohio", "court elsewhere"]
    assert candidates[2].expert_note == "No expert-witness evidence found"
    assert candidates[0].expert_note is None


def test_without_field_or_location_hint_tiers_decide(sources):
    people = [person(["E7"], summary="clinic") | {"affiliations": ["Example Lab"]},
              person(["E4"], summary="court")]
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[{"people": people}]))
    candidates, _, _ = run(hints={"field": None, "location": None, "affiliation": "Example Lab"})
    assert [c.summary for c in candidates] == ["court", "clinic"]


@pytest.mark.parametrize("hint, location, field, matches", [
    ({"affiliation": "University of Michigan"}, None, "University of Texas", 0),  # affiliation never ranks
    ({"field": "Addiction Medicine"}, None, "Internal Medicine", 0),  # generic words only
    ({"field": "Addiction Medicine"}, None, "Addiction Psychiatry", 1),
    ({"field": "Orthopedic Surgery"}, None, "Plastic Surgery", 0),  # "surgery" is generic
    ({"field": "Toxicología"}, None, "toxicologia", 1),  # accent-insensitive
    ({"location": "Ohio"}, "Akron, OH", None, 1),  # state name = state code
    ({"location": "San Jose, California"}, "Santa Clara, CA", None, 1),  # same state, other city
    ({"location": "New York"}, "New Orleans, LA", None, 0),  # "new" is not a match
    ({"location": "Saint Louis, MO"}, "Saint Paul, MN", None, 0),
    ({"location": "Fort Worth, TX"}, "Fort Myers, FL", None, 0),
    ({"location": "Washington, DC"}, "Seattle, WA", None, 0),  # DC is its own place
    ({"location": "Washington, DC"}, "Washington, DC", None, 1),
    ({"location": "Maryland"}, "Seattle, WA", None, 0),  # the ", MD" in the title is a credential
    ({"location": "Bogota"}, "Bogotá", None, 0),  # cities are never compared
    ({"location": "Ohio", "field": "toxicology"}, "Akron, OH", "Toxicology", 2),
])
def test_hint_words(hint, location, field, matches):
    cand = Candidate(display_name="Alex Rivera", summary="x", location=location, field=field,
                     evidence=[{"source": "web", "url": "https://example.com", "title": "Alex Rivera, MD"}])
    assert candidate_agent.hint_matches(cand, hint) == matches


def test_accented_names_match_unaccented_search(sources):
    sources(model=FakeModel(web=[WEB_RESULTS], json=[{"people": [person(["E1"], name="José M. García")]}]))
    candidates, _, _ = run(name="Jose Garcia")
    assert [c.display_name for c in candidates] == ["José M. García"]


DIR_A = "https://www.seakexperts.com/members/0000-alex-rivera"
DIR_B = "https://www.imenet.com/members/0000-alex-rivera"


NO_OTHER_SOURCES = {"api.openalex.org": (200, {"results": []}), "www.courtlistener.com": (200, {"results": []})}
CLINIC = "https://clinic.example/rivera"


def guarded(locations, field="chiropractic", person_location=None, urls=(DIR_A, DIR_B), titles=None):
    """A person resting on web listings, each with its own AI-given location, and the fake pages."""
    merged = person([f"E{i}" for i in range(1, len(urls) + 1)]) | {"field": field, "location": person_location}
    for ref, loc in zip(merged["evidence"], locations):
        ref["location"] = loc
    titles = titles or ["Alex Rivera, DC - Chiropractor", "Alex Rivera, PA", "Alex Rivera, MD", "Alex Rivera"]
    return merged, [{"url": u, "title": t, "snippet": "S"} for u, t in zip(urls, titles)]


@pytest.mark.parametrize("loc_a, loc_b, person_location", [
    ("St. Louis, MO", "Saint Louis, Missouri", None),
    ("Dallas, TX", "Fort Worth, Texas", "Dallas-Fort Worth, TX"),
    ("San Jose, CA", "Santa Clara, California", "San Jose, CA"),
    ("Austin, TX", None, "Austin, TX"),  # a missing city is no conflict
    (None, None, None),
    ("Tampa, FL", "Tampa, FL", "Bethesda, MD"),  # ", DC"/", PA" in the titles are never states
])
def test_listings_in_one_state_or_without_one_stay_together(sources, loc_a, loc_b, person_location):
    merged, pages = guarded([loc_a, loc_b], person_location=person_location)
    sources(NO_OTHER_SOURCES, model=FakeModel(web=[[], pages], json=[{"people": [merged]}]))
    candidates, _, _ = run()
    assert [[e.url for e in c.evidence] for c in candidates] == [[DIR_A, DIR_B]]
    assert candidates[0].match_notes == "merged on field only - verify"  # two sites, shared field only
    assert candidates[0].location == person_location


def test_listings_in_different_states_are_split_by_state(sources):
    dir_c = "https://www.jurispro.com/profile/0000-alex-rivera"
    merged, pages = guarded(["Columbus, Ohio", "Houston, TX", "Dallas, Texas", None],
                            person_location="Columbus, OH", urls=(DIR_A, DIR_B, dir_c, CLINIC))
    sources(NO_OTHER_SOURCES, model=FakeModel(web=[[], pages], json=[{"people": [merged]}]))
    candidates, _, _ = run()
    by_urls = {tuple(e.url for e in c.evidence): c.location for c in candidates}
    # one group per state; the listing with no state stays with the rest (here: nothing else)
    assert by_urls == {(DIR_A,): "Columbus, Ohio", (DIR_B, dir_c): "Houston, TX", (CLINIC,): None}
    assert all(c.match_notes == "merged on field only - verify; Kept apart: the listings name different US states"
               for c in candidates)


def test_split_keeps_court_and_stateless_listings_together(sources):
    merged, pages = guarded([None, "Dallas, TX", "Miami, FL", None], urls=(DIR_A, DIR_B, CLINIC))
    merged["evidence"] = [{"ref": "E1", "note": None, "location": None}] + [  # E1 = court hit
        {"ref": f"E{i}", "note": None, "location": loc} for i, loc in ((2, "Dallas, TX"), (3, "Miami, FL"), (4, None))]
    sources({"api.openalex.org": (200, {"results": []})},
            model=FakeModel(web=[[], pages], json=[{"people": [merged]}]))
    candidates, _, _ = run()
    groups = [[e.url.split("/")[2] for e in c.evidence] for c in candidates]
    assert groups == [["www.courtlistener.com", "clinic.example"], ["www.seakexperts.com"], ["www.imenet.com"]]


@pytest.mark.parametrize("urls, field, groups, note", [
    # two plain listings, no field, different sites: never merged on name alone
    ((DIR_A, DIR_B), None, 2, "Kept apart: the listings share no field or website"),
    # a directory hosts many people: the same directory site alone is not enough
    (("https://www.seakexperts.com/members/x", "https://www.seakexperts.com/members/y"), None, 2,
     "Kept apart: the listings share no field or website"),
    # generic words are not a field
    ((DIR_A, DIR_B), "Medical Services", 2, "Kept apart: the listings share no field or website"),
    # the same practice website: a concrete shared detail, no note
    (("https://riverachiro.example/about", "https://www.riverachiro.example/team"), None, 1, None),
])
def test_listing_merge_needs_a_shared_field_or_website(sources, urls, field, groups, note):
    merged, pages = guarded(["Dallas, TX", "Dallas, TX"], field=field, urls=urls)
    sources(NO_OTHER_SOURCES, model=FakeModel(web=[[], pages], json=[{"people": [merged]}]))
    candidates, _, _ = run()
    assert len(candidates) == groups
    assert all(c.match_notes == note for c in candidates)


@pytest.mark.parametrize("url, signals", [
    ("https://www.riveraexpertwitness.example/about", ["web_expert_witness"]),
    ("https://alex-rivera-expert-witness.example/", ["web_expert_witness"]),
    ("https://riveraconsulting.example/", []),
])
def test_expert_witness_domains_earn_the_web_signal(url, signals):
    assert candidate_agent.expert_signals([{"source": "web", "url": url, "title": "Alex Rivera"}]) == signals


def test_grouping_instructions_allow_merging_directory_profiles_on_shared_detail():
    text = candidate_agent.GROUP_INSTRUCTIONS
    assert "IMENet" in text and "same specialty in the same state" in text
    assert "never on the name alone" in text and "never alone" in text  # hints never merge alone


@pytest.mark.parametrize("query, url, title, note, signals", [
    ("bare", "https://example.com/rivera", "Alex Rivera, Expert Witness in Economics", None, ["web_expert_witness"]),
    ("bare", "https://example.com/expert-witness/rivera", "Alex Rivera", None, ["web_expert_witness"]),
    ("bare", "https://example.com/expert_witness/rivera", "Alex Rivera", None, ["web_expert_witness"]),
    ("bare", "https://example.com/rivera", "Alex Rivera | Economics Experts", None, ["web_expert_witness"]),
    ("bare", "https://example.com/rivera", "Alex Rivera: an inexpert review", None, []),
    ("bare", "https://example.com/rivera", "Alex Rivera expertly explains", None, []),
    # the AI note never earns a signal (QA A1)
    ("bare", "https://clinic.example/rivera", "Rivera Chiropractic", "serves as expert witness", []),
    # a page returned by the expert query alone is not enough (QA A2)
    ("expert", "https://news.example/zoning", "Town zoning board meeting", "zoning applicant", []),
    ("expert", "https://example.com/rivera", "Alex Rivera: areas of expertise", None, []),
])
def test_web_signal_comes_from_title_or_url_only(sources, query, url, title, note, signals):
    page = [{"url": url, "title": title, "snippet": "SYNTHETIC"}]
    web = [page, []] if query == "expert" else [[], page]
    sources({"api.openalex.org": (200, {"results": []}), "www.courtlistener.com": (200, {"results": []})},
            model=FakeModel(web=web, json=[{"people": [person(["E1"], note)]}]))
    candidates, _, _ = run()
    assert [c.expert_signals for c in candidates] == [signals]  # listed either way, flagged if none


def test_failed_expert_query_is_reported(sources):
    sources(model=FakeModel(web=[llm.ModelError("model call ended with error"), WEB_NAME_RESULTS],
                            json=[GROUPING]))
    candidates, failed, steps = run()
    assert [(f.source, f.reason) for f in failed] == [
        ("web_search_query", "expert query failed: model call ended with error")]
    assert steps.last("web_search")[1] == "done" and candidates


def test_cap_applies_after_ranking(sources):
    people = [person(["E5"], summary=f"web {i}") for i in range(12)] + [person(["E4"], summary="court")]
    sources(model=FakeModel(web=[WEB_RESULTS], json=[{"people": people}]))
    candidates, _, _ = run()
    assert len(candidates) == candidate_agent.MAX_CANDIDATES
    assert candidates[0].summary == "court"


@pytest.mark.parametrize("stream", ["data: {not json\n\n", 'data: {"type": "response.completed"}\n\n'])
def test_bad_model_stream_still_reports_failed_sources(sources, monkeypatch, stream):
    async def web_fake_grouping_real(instructions, input, schema=None, web_search=False, output_type=None):
        if web_search:
            return llm.ModelResult(results=WEB_RESULTS)
        return await real_call_model(instructions, input, schema=schema, output_type=output_type)

    sources({"www.courtlistener.com": (500, {})})
    monkeypatch.setattr(llm, "call_model", web_fake_grouping_real)
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(lambda request: httpx.Response(200, text=stream)))
    steps = Steps()
    with pytest.raises(candidate_agent.FindCandidatesError, match="AI grouping step failed") as err:
        asyncio.run(candidate_agent.find_candidates(NAME, None, steps))
    assert [f.source for f in err.value.failed_sources] == ["courtlistener", "group_candidates"]
    assert steps.last("group_candidates")[1] == "failed"


def test_long_web_titles_are_capped_in_candidates(sources):
    long_title = [WEB_RESULTS[0] | {"title": "T" * 5000}]
    sources(model=FakeModel(web=[long_title], json=[GROUPING]))
    candidates, _, _ = run()
    titles = [e.title for c in candidates for e in c.evidence]
    assert max(len(t) for t in titles) <= candidate_agent.LIMITS["title"]


# Follow-up loop (DESIGN 4A): an ADK agent calls sources as tools after round 1; guards stay in code.

LOOP_URL = "https://example.com/experts/alex-rivera"
LOOP_RESULTS = [
    {"type": "text_result", "url": LOOP_URL, "title": "Alex Rivera, toxicology expert witness",
     "snippet": "SYNTHETIC-WEB-SNIPPET follow-up page about a toxicology expert in Ohio"},
    {"type": "text_result", "url": "https://www.linkedin.com/in/x", "title": "LinkedIn",
     "snippet": "SYNTHETIC-WEB-SNIPPET blocked"},
    {"type": "text_result", "url": "https://example.edu/faculty/rivera", "title": "Faculty page (seen)",
     "snippet": "SYNTHETIC-WEB-SNIPPET repeat"},
]


def loop_model(people, web_extra=(LOOP_RESULTS,)):
    """Round 1 without hints sends two web queries (expert, bare); the loop's searches come next."""
    return FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS, *web_extra], json=[{"people": people}])


def tool_reply(adk_model, turn):
    """What the tool answered, as the model saw it on its next turn."""
    return adk_model.seen[turn].contents[-1].parts[0].function_response.response


def test_loop_calls_tools_and_new_hits_are_grouped_with_all_guards(sources, adk_model):
    copied_summary = "Note: " + LOOP_RESULTS[0]["snippet"]
    people = [person(["E8"], summary=copied_summary), person(["E4"], summary="court"), person(["E9"])]
    model, _ = sources(model=loop_model(people))
    adk_model.script = [call("web_search", query='"Alex Rivera" toxicology Ohio'), "DONE"]
    candidates, failed, steps = run()
    assert failed == []
    loop_prompt = adk_model.seen[0].contents[0].parts[0].text
    assert "E7 [web]" in loop_prompt and "Alex Rivera" in loop_prompt
    declared = {f.name for f in adk_model.seen[0].config.tools[0].function_declarations}
    assert declared == {"web_search", "court_opinions", "openalex_authors"}
    reply = tool_reply(adk_model, 1)
    assert [line.split(" | ")[0] for line in reply["results"]] == [
        "E8 [web] Alex Rivera, toxicology expert witness"]  # blocked and repeated URLs dropped
    assert [c["input"] for c in model.calls if c["web_search"]][-1] == 'Search query: "Alex Rivera" toxicology Ohio'
    group_call = model.calls[-1]
    assert "E8 [web]" in group_call["input"] and "found by: follow-up search" in group_call["input"]
    assert "linkedin" not in group_call["input"]
    # ranking: court first; E9 (never returned by a tool) dropped; copied summary removed
    assert [c.summary for c in candidates] == ["court", candidate_agent.SUMMARY_REMOVED]
    assert candidates[1].evidence[0].url == LOOP_URL
    assert candidates[1].expert_signals == ["web_expert_witness"]
    assert all("SYNTHETIC" not in c.model_dump_json() for c in candidates)
    assert steps.last("group_candidates") == (
        "group_candidates", "done", "2 candidates; follow-up: 1 source calls, 1 new results")


def test_loop_stops_at_the_step_cap_and_keeps_tool_results(sources, adk_model):
    model, _ = sources(model=loop_model([person(["E8"])]))
    adk_model.script = [call("web_search", query=f'"Alex Rivera" lead {i}') for i in range(10)]
    candidates, _, steps = run()
    assert len(adk_model.seen) == candidate_agent.LOOP_STEPS
    assert len([c for c in model.calls if c["web_search"]]) == 2 + candidate_agent.LOOP_CAPS["web_search"]
    assert tool_reply(adk_model, 3) == {"error": LIMIT_REACHED}  # tool cap reached
    assert [c.evidence[0].url for c in candidates] == [LOOP_URL]  # evidence kept after the stop
    assert steps.last("group_candidates")[2] == (
        "1 candidates; follow-up: 2 source calls, 1 new results, stopped at the step limit")


def test_loop_stops_at_the_time_limit_and_grouping_still_runs(sources, adk_model, monkeypatch):
    monkeypatch.setattr(candidate_agent, "TOTAL_SECONDS", 0.3)
    monkeypatch.setattr(candidate_agent, "GROUP_RESERVE", 0.0)
    monkeypatch.setattr(candidate_agent, "MIN_LOOP", 0.1)
    model, _ = sources(model=loop_model([person(["E4"])]))
    adk_model.script = [HANG]
    candidates, _, steps = run()
    assert [c.summary for c in candidates] == ["Expert."]
    assert model.calls[-1]["schema"] is candidate_agent.GROUP_SCHEMA
    assert steps.last("group_candidates")[2].endswith("stopped at the time limit")


def test_loop_is_skipped_without_time_left(sources, adk_model, monkeypatch):
    monkeypatch.setattr(candidate_agent, "TOTAL_SECONDS", 0.0)
    sources(model=loop_model([person(["E4"])]))
    candidates, _, steps = run()
    assert adk_model.seen == [] and candidates
    assert steps.last("group_candidates")[2] == "1 candidates; follow-up skipped: no time left"


def test_tools_refuse_queries_without_the_full_name(sources, adk_model):
    model, seen = sources(model=loop_model([person(["E4"])]))
    adk_model.script = [call("web_search", query="Rivera toxicology"), call("court_opinions", full_name="Alex Smith"),
                        call("openalex_authors", full_name="Rivera"), "DONE"]
    run()
    assert tool_reply(adk_model, 1) == {"error": candidate_agent.NO_FULL_NAME}
    assert tool_reply(adk_model, 2) == tool_reply(adk_model, 3) == {"error": candidate_agent.OTHER_NAME}
    assert len([c for c in model.calls if c["web_search"]]) == 2  # round 1 only
    assert len([r for r in seen if r.url.host == "www.courtlistener.com"]) == 1


def test_web_tool_checks_the_cut_query_and_refuses_banned_sites(sources, adk_model):
    model, _ = sources(model=loop_model([person(["E4"])]))
    long_query = "toxicology " * 25 + '"Alex Rivera"'  # the name sits past the cut
    adk_model.script = [call("web_search", query=long_query),
                        call("web_search", query='"Alex Rivera" expert site:linkedin.com'), "DONE"]
    run()
    assert tool_reply(adk_model, 1) == {"error": candidate_agent.NO_FULL_NAME}
    assert tool_reply(adk_model, 2) == {"error": candidate_agent.BLOCKED_SITE}
    assert len([c for c in model.calls if c["web_search"]]) == 2  # round 1 only: nothing was sent


def test_court_tool_adds_only_new_cases(sources, adk_model):
    new_case = opinion(5) | {"caseName": "Roe v. Sample Corp."}
    bodies = [COURTLISTENER_SEARCH, {"results": [opinion(3), new_case]}]  # opinion 3: a case already stored
    sources({"www.courtlistener.com": lambda request: httpx.Response(200, json=bodies.pop(0))},
            model=loop_model([person(["E4", "E8"])]))
    adk_model.script = [call("court_opinions", full_name="Alex J. Rivera"), "DONE"]
    candidates, _, _ = run()
    assert [line.split(" | ")[0] for line in tool_reply(adk_model, 1)["results"]] == [
        "E8 [courtlistener] Roe v. Sample Corp."]
    assert [e.url for e in candidates[0].evidence] == [
        "https://www.courtlistener.com/opinion/1/doe-v-example-co/",
        "https://www.courtlistener.com/opinion/5/doe-v-example-co/"]


def test_tools_are_offered_only_for_sources_that_answered(sources, adk_model, monkeypatch):
    sources({"api.openalex.org": (500, {})}, model=loop_model([person(["E2"])]))
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    adk_model.script = [call("court_opinions", full_name="Alex Rivera"), "DONE"]
    _, failed, _ = run()
    declared = {f.name for f in adk_model.seen[0].config.tools[0].function_declarations}
    assert declared == {"web_search"}
    assert [f.source for f in failed] == ["openalex", "courtlistener"]  # round 1 only, no tool call ran


def test_failed_tool_call_is_reported_as_a_failed_source(sources, adk_model):
    replies = [httpx.Response(200, json={"results": [author("A0000000001", "Alex J. Rivera")]}),
               httpx.Response(500, json={})]
    sources({"api.openalex.org": lambda request: replies.pop(0)}, model=loop_model([person(["E2"])]))
    adk_model.script = [call("openalex_authors", full_name="Alex J. Rivera"), "DONE"]
    candidates, failed, _ = run()
    assert tool_reply(adk_model, 1) == {"error": "source call failed"}
    assert [f.source for f in failed] == ["openalex"]
    assert failed[0].reason.startswith("follow-up openalex_authors call failed (")
    assert candidates


def test_loop_model_failure_is_noted_and_grouping_still_runs(sources, adk_model):
    sources(model=loop_model([person(["E4"])]))
    adk_model.script = [RuntimeError("provider text that must not leak")]
    candidates, failed, steps = run()
    assert candidates and failed == []
    detail = steps.last("group_candidates")[2]
    assert "follow-up failed: model call failed (RuntimeError)" in detail and "provider" not in detail


def test_store_hits_drops_blocked_and_repeated_hits():
    store = Run(10, {})
    ok = {"source": "web", "url": "https://example.org/a", "title": "A", "note": None, "query": "follow-up"}
    lines = candidate_agent.store_hits(store, [ok, ok | {"title": "again"},
                                      ok | {"url": "https://www.linkedin.com/in/x"},
                                      ok | {"url": "https://scholar.google.com/x"}])
    assert lines == ["E1 [web] A | https://example.org/a | found by: follow-up search"]
    assert list(store.evidence) == ["E1"]
