"""Collectors with fake sources (tests/fakes.py): findings, steps and gaps. Synthetic people only."""

import asyncio
import time

import pytest

from app import collectors, jobs, llm, store
from app.evidence import LIMIT_REACHED
from app.models import Candidate, Evidence, Finding, Gap, Identifiers
from app.sources import SourceSkipped, websearch
from tests.fakes import (  # noqa: F401 - pytest fixture
    HANG,
    PAGE_TEXT,
    RESEARCH_SOURCES,
    call,
    cl_hit,
    research_sources_fixture,
    work,
)

CANDIDATE = Candidate(
    candidate_id="cand_test", display_name="Alex J. Rivera", summary="Synthetic toxicologist.",
    field="toxicology", identifiers={"openalex": ["A0000000001"]},
    evidence=[{"source": "openalex", "url": "https://openalex.org/A0000000001", "title": "OpenAlex"}],
)


def run(collect, candidate=CANDIDATE, *args):
    steps = {}

    def step(key, status, detail=None):
        steps[key] = (status, detail)

    findings, gaps = asyncio.run(collect(candidate, *args, step))
    return findings, gaps, steps


def check_sources(findings):
    """3c: every finding has a source URL and a retrieval time."""
    assert findings and all(f.url.startswith("https://") and f.retrieved_at for f in findings)


def test_testimony(research_sources):
    calls = research_sources()
    findings, gaps, steps = run(collectors.collect_testimony)
    check_sources(findings)
    opinions = [f for f in findings if f.kind == "court_opinion"]
    assert [f.facts["date_filed"] for f in opinions] == ["2021-05-01", "2018-01-02"]  # deduped, newest first
    assert all(f.confidence == "high" and f.source_type == "courtlistener_opinion" for f in opinions)
    assert set(opinions[0].facts) == {"case_name", "court", "date_filed", "passages"}
    assert opinions[0].facts["passages"] == ["the plaintiff's expert, Alex J. Rivera, a toxicologist, testified that"]
    web = [f for f in findings if f.kind == "web_testimony"]
    assert [f.url for f in web] == ["https://example.org/news/rivera"]  # LinkedIn dropped (gotcha 2)
    assert web[0].confidence == "low" and web[0].note == "verify" and web[0].source_type == "web"
    assert web[0].facts == {"facts": ["Testified for the plaintiff in a 2020 example trial"]}  # 3h
    assert "SNIPPET" not in str([f.model_dump() for f in findings])
    assert collectors.STATE_COURTS_GAP in [g.reason for g in gaps]
    assert steps == {"courtlistener": ("done", "2 opinions"), "web_testimony": ("done", "1 pages")}
    (name, queries, topic), kw = next((a, k) for key, a, k in calls if key == "websearch.search_facts")
    assert name == "Alex J. Rivera" and topic == "expert testimony" and len(queries) == 2
    assert kw == {"context": "toxicology"}  # field (and location, when known) target this person
    assert all('"Alex J. Rivera"' in q and "toxicology" in q for q in queries)


def with_evidence(*urls):
    extra = [{"source": "courtlistener", "url": u, "title": "Opinion"} for u in urls]
    return CANDIDATE.model_copy(update={"evidence": CANDIDATE.evidence + [Evidence(**e) for e in extra]})


def test_opinion_fetch_failure_keeps_the_case_only_if_in_evidence(research_sources):
    research_sources({"courtlistener.opinion_passages": RuntimeError("HTTP 500")})
    findings, gaps, steps = run(collectors.collect_testimony, with_evidence(cl_hit(11, "")["url"]))
    opinions = [f for f in findings if f.kind == "court_opinion"]
    assert len(opinions) == 1 and opinions[0].facts["passages"] == [] and opinions[0].note
    reasons = [g.reason for g in gaps]
    assert "opinion text not fetched for 2 opinions" in reasons
    assert "1 same-name court opinions skipped: not tied to this person" in reasons  # no text, no evidence
    assert steps["courtlistener"] == ("done", "1 opinions")


def test_same_name_opinions_are_dropped(research_sources):
    """Kept: in the candidate's evidence, or the passages name the field (a stem is enough).
    Evidence-backed hits are fetched first, within the cap of MAX_OPINIONS fetches."""
    hits = [cl_hit(n, f"2020-01-{n:02d}") for n in range(1, 9)]  # 8 opinions, newest is 8
    texts = {3: "Alex J. Rivera, a paramedic, said", 4: "Alex J. Rivera, the toxicologist, said",
             5: "Alex J. Rivera, an attorney, said", 6: "Alex J. Rivera, a sleep expert, said"}
    calls = research_sources({
        "courtlistener.search_opinions": hits,
        "courtlistener.opinion_passages": lambda oid, name: [texts.get(oid, "Alex J. Rivera said")],
    })
    findings, gaps, steps = run(collectors.collect_testimony, with_evidence(hits[0]["url"] + "/"))
    fetched = [args[0] for key, args, _ in calls if key == "courtlistener.opinion_passages"]
    assert fetched == [1, 8, 7, 6, 5, 4]  # evidence first, then newest; 6 fetches at most
    opinions = [f for f in findings if f.kind == "court_opinion"]
    assert [f.facts["date_filed"] for f in opinions] == ["2020-01-01", "2020-01-04"]
    assert all(f.confidence == "high" for f in opinions)
    assert "4 same-name court opinions skipped: not tied to this person" in [g.reason for g in gaps]
    assert steps["courtlistener"] == ("done", "2 opinions")


def test_publications(research_sources):
    calls = research_sources()
    findings, gaps, steps = run(collectors.collect_publications)
    check_sources(findings)
    kinds = [f.kind for f in findings]
    assert kinds.count("author_stats") == 1 and kinds.count("work") == 3  # work 1 deduped by DOI
    retractions = {f.facts.get("doi"): f for f in findings if f.kind == "retraction"}
    assert retractions["10.5555/synthetic.2"].source_type == "crossref_work"
    assert retractions["10.5555/synthetic.2"].url == "https://doi.org/10.5555/notice.2"
    assert retractions[None].source_type == "openalex_work"  # no DOI, OpenAlex flag only
    assert [args for key, args, _ in calls if key == "crossref.retraction"] == [
        ("10.5555/synthetic.1",), ("10.5555/synthetic.2",)
    ]
    reasons = [g.reason for g in gaps]
    assert collectors.REASONS_GAP in reasons and any("1 works not checked in Crossref" in r for r in reasons)
    assert steps == {"openalex": ("done", "3 works"), "crossref": ("done", "2 retractions")}


def test_crossref_checks_newest_50_only(research_sources):
    calls = research_sources({"openalex.works": [work(n, 1950 + n) for n in range(60)]})
    _, gaps, _ = run(collectors.collect_publications)
    checked = [args[0] for key, args, _ in calls if key == "crossref.retraction"]
    assert len(checked) == 50 and checked[0] == "10.5555/synthetic.59" and "10.5555/synthetic.9" not in checked
    assert any("10 works not checked in Crossref" in g.reason for g in gaps)


def test_no_openalex_id_is_a_gap(research_sources):
    calls = research_sources()
    findings, gaps, steps = run(collectors.collect_publications, CANDIDATE.model_copy(update={"identifiers": Identifiers()}))
    assert findings == [] and calls == []
    assert steps == {"openalex": ("skipped", "no OpenAlex id"), "crossref": ("skipped", "no works to check")}
    assert {g.step for g in gaps} == {"openalex", "crossref"}


def test_credentials(research_sources):
    calls = research_sources()
    findings, gaps, steps = run(collectors.collect_credentials)
    check_sources(findings)
    assert [(args, kw) for key, args, kw in calls if key == "leie.possible_matches"] == [
        (("alex", "rivera"), {"middle_initial": "J"})
    ]
    leie = next(f for f in findings if f.kind == "leie_possible_match")
    assert leie.confidence == "low" and leie.note == collectors.LEIE_NOTE and leie.source_type == "oig_leie"
    assert leie.facts["state"] == "OH" and "url" not in leie.facts
    web = [f for f in findings if f.kind == "web_credential"]
    assert len(web) == 1 and web[0].confidence == "low" and "verify manually" in web[0].note
    assert gaps == []
    assert steps == {"oig_leie": ("done", "1 possible matches"), "web_credentials": ("done", "1 pages")}


def test_leie_matches_from_another_state_are_dropped(research_sources):
    match = {"general": "SYNTHETIC", "specialty": "", "excltype": "1128a1", "excldate": "20200101",
             "reindate": "", "url": "https://exclusions.oig.hhs.gov/"}
    research_sources({"leie.possible_matches": [match | {"state": "OH"}, match | {"state": "WA"},
                                                match | {"state": "NY"}, match | {"state": ""}]})
    findings, gaps, steps = run(collectors.collect_credentials,
                                CANDIDATE.model_copy(update={"location": "Columbus, Ohio"}))
    assert [f.facts["state"] for f in findings if f.kind == "leie_possible_match"] == ["OH", ""]
    assert [g.reason for g in gaps] == ["2 OIG exclusion list name matches skipped: listed in another state than OH"]
    assert steps["oig_leie"] == ("done", "2 possible matches")
    findings, _, _ = run(collectors.collect_credentials)  # state unknown: every match stays possible
    assert len([f for f in findings if f.kind == "leie_possible_match"]) == 4


def test_failing_sources_become_gaps(research_sources):
    """3d: skipped or failing sources never raise; reasons are short and hold no raw error text."""
    secret = "RAW-MODEL-TEXT"
    research_sources({
        "courtlistener.search_opinions": SourceSkipped("COURTLISTENER_TOKEN is not set"),
        "websearch.search_facts": llm.ModelError(secret),
        "openalex.author": RuntimeError(secret),
        "openalex.works": TimeoutError(),
        "leie.possible_matches": OSError(secret),
    })
    results = {c.__name__: run(c) for c in
               (collectors.collect_testimony, collectors.collect_publications, collectors.collect_credentials)}
    steps = {k: v for _, _, s in results.values() for k, v in s.items()}
    assert steps == {
        "courtlistener": ("skipped", "COURTLISTENER_TOKEN is not set"),
        "web_testimony": ("failed", "Web search failed (ModelError)"),
        "openalex": ("failed", "OpenAlex timed out"),
        "crossref": ("skipped", "no works to check"),
        "oig_leie": ("failed", "OIG LEIE failed (OSError)"),
        "web_credentials": ("failed", "Web search failed (ModelError)"),
    }
    assert all(findings == [] for findings, _, _ in results.values())
    gaps = [g for _, g, _ in results.values() for g in g]
    assert {g.step for g in gaps} == set(steps)
    assert secret not in str(gaps)


# Expert's own pages (3j) and the testimony cross-check (3k)


def web_finding(url, kind="web_testimony"):
    return Finding(collector="testimony", kind=kind, title="Synthetic page", url=url, source_type="web",
                   retrieved_at="2026-10-28T00:00:00Z", confidence="low", facts={"facts": []}, note="verify")


def page_case(name, court=None, year=None):
    return Finding(collector="testimony", kind="page_testimony_case", title=name,
                   url="https://example.org/cv.pdf", source_type="web", retrieved_at="2026-10-28T00:00:00Z",
                   confidence="low",
                   facts={"case_name": name, "court": court, "year": year, "side": "unknown", "role": "unknown"})


def scraped(calls):
    return [args[0] for key, args, _ in calls if key == "firecrawl.scrape"]


def ai_calls(calls):
    """(input, schema) of each faked llm.call_model call."""
    return [(args[1], kw.get("schema")) for key, args, kw in calls if key == "llm.call_model"]


def test_read_pages(research_sources):
    calls = research_sources()
    candidate = CANDIDATE.model_copy(update={"evidence": CANDIDATE.evidence + [
        Evidence(source="web", url=u, title="Web") for u in (
            "https://www.linkedin.com/in/alex-rivera", "https://example.org/news/rivera",
            "https://www.jurispro.com/profile/0000-alex-rivera")]})
    found = [web_finding(u) for u in ("https://example.org/a", "https://example.org/files/Rivera_CV.pdf",
                                      "https://riveratoxicology.example/about", "https://example.org/b")]
    found.append(web_finding("https://example.org/news/rivera/", "web_credential"))  # same page again
    findings, gaps, steps = run(collectors.collect_pages, candidate, found)
    # own site, CV, directory, then the rest in order; blocked never fetched; at most 5
    assert scraped(calls) == ["https://riveratoxicology.example/about", "https://example.org/files/Rivera_CV.pdf",
                              "https://www.jurispro.com/profile/0000-alex-rivera",
                              "https://example.org/news/rivera", "https://example.org/a"]
    assert all(schema is collectors.PAGE_SCHEMA for _, schema in ai_calls(calls))  # one AI call per page
    assert len(ai_calls(calls)) == 5
    check_sources(findings)
    cases = [f for f in findings if f.kind == "page_testimony_case"]
    assert len(cases) == 10 and all(f.confidence == "low" and f.source_type == "web" for f in cases)
    assert cases[0].note == collectors.PAGE_CASE_NOTE and cases[0].title == "Doe v. Example Co."
    assert cases[0].facts == {"case_name": "Doe v. Example Co.", "court": "N.D. Example", "year": "2019",
                              "side": "plaintiff", "role": "deposition"}
    assert cases[1].facts["court"] is None and cases[1].facts["year"] is None
    creds = [f for f in findings if f.kind == "page_credential"]
    assert len(creds) == 5 and creds[0].collector == "credentials" and creds[0].confidence == "low"
    assert creds[0].facts == {"facts": ["Holds a board certification in toxicology"]}  # copy guard dropped one
    dump = str([f.model_dump() for f in findings])
    assert PAGE_TEXT not in dump and "markdown" not in dump and "snippet" not in dump
    assert gaps == [] and steps == {"read_pages": ("done", "5 pages read, 10 cases")}


def test_read_pages_skips_pages_about_someone_else(research_sources):
    calls = research_sources({"firecrawl.scrape": lambda url: {"url": url, "title": "Other",
                                                               "markdown": "Morgan Example, botanist"}})
    findings, gaps, steps = run(collectors.collect_pages, CANDIDATE, [web_finding("https://example.org/a")])
    assert findings == [] and ai_calls(calls) == []  # no AI call either
    assert [g.reason for g in gaps] == ["1 pages skipped: not about this person"]
    assert steps["read_pages"] == ("done", "0 pages read, 0 cases")


def test_read_pages_without_key_or_pages(research_sources):
    research_sources({"firecrawl.scrape": SourceSkipped("FIRECRAWL_API_KEY is not set")})
    findings, gaps, steps = run(collectors.collect_pages, CANDIDATE, [web_finding("https://example.org/a")])
    assert findings == [] and [g.reason for g in gaps] == ["FIRECRAWL_API_KEY is not set"]
    assert steps["read_pages"] == ("skipped", "FIRECRAWL_API_KEY is not set")
    calls = research_sources()
    calls.clear()  # one call log per test: drop the first run's calls
    _, gaps, steps = run(collectors.collect_pages, CANDIDATE, [web_finding("https://www.linkedin.com/in/x")])
    assert scraped(calls) == [] and steps["read_pages"] == ("skipped", "no web pages about this person to read")


def test_cross_check_found_and_not_found(research_sources):
    calls = research_sources()  # default web search returns a news page, not a court site
    cases = [page_case("Doe v. Example Co.", "N.D. Example", "2019"), page_case("Roe v. Sample")]
    findings, gaps, steps = run(collectors.cross_check, CANDIDATE, cases, None)
    check_sources(findings)
    found, missing = findings
    assert found.kind == "case_check" and found.confidence == "high" and found.source_type == "courtlistener_opinion"
    assert found.url == "https://www.courtlistener.com/opinion/11/doe-v-example/"
    assert found.facts["status"] == "found" and found.facts["how"] == "courtlistener"
    assert found.facts["passages"] == ["the plaintiff's expert, Alex J. Rivera, testified that"]
    assert missing.facts["status"] == "not_found" and missing.confidence == "low"
    assert missing.note == collectors.NOT_FOUND_NOTE
    assert missing.url == "https://www.courtlistener.com/?q=%22Roe+v.+Sample%22"
    assert [args for key, args, _ in calls if key == "courtlistener.find_case"] == [
        ("Doe v. Example Co.", "Alex J. Rivera", "N.D. Example", 2019),
        ("Roe v. Sample", "Alex J. Rivera", None, None),
    ]
    (_, queries, _), kw = next((a, k) for key, a, k in calls if key == "websearch.search_facts")
    assert queries == ['"Roe v. Sample" "Alex J. Rivera"'] and kw == {"context": "toxicology"}
    assert scraped(calls) == [] and ai_calls(calls) == []  # no court site: nothing read
    assert gaps == [] and steps["cross_check"] == ("done", "2 cases: 1 found, 0 unclear, 1 not found")


def test_cross_check_court_site(research_sources):
    court = {"url": "https://www.examplecounty.courts.example.gov/case/1", "title": "Docket", "facts": []}
    news = {"url": "https://example.org/news", "title": "News", "facts": []}
    calls = research_sources({"websearch.search_facts": [news, court]})
    findings, _, _ = run(collectors.cross_check, CANDIDATE, [page_case("Roe v. Sample")], None)
    assert findings[0].facts["status"] == "found" and findings[0].facts["how"] == "court_site"
    assert findings[0].url == court["url"] and findings[0].confidence == "low"
    assert findings[0].source_type == "web" and findings[0].note == collectors.COURT_SITE_NOTES["found"]
    assert scraped(calls) == [court["url"]]
    assert [schema for _, schema in ai_calls(calls)] == [collectors.COURT_PAGE_SCHEMA]

    def unclear(instructions, input, schema=None, **kw):
        return llm.ModelResult(data={"shows_case": True, "shows_person": False})

    research_sources({"websearch.search_facts": [court], "llm.call_model": unclear})
    findings, _, _ = run(collectors.cross_check, CANDIDATE, [page_case("Roe v. Sample")], None)
    assert findings[0].facts["status"] == "unclear" and findings[0].note == collectors.COURT_SITE_NOTES["unclear"]
    research_sources({"websearch.search_facts": [court],
                      "firecrawl.scrape": SourceSkipped("FIRECRAWL_API_KEY is not set")})
    findings, gaps, _ = run(collectors.cross_check, CANDIDATE, [page_case("Roe v. Sample")], None)
    assert findings[0].facts["status"] == "unclear"
    assert [g.reason for g in gaps] == ["court page not read: FIRECRAWL_API_KEY is not set (1 cases)"]


def test_courtlistener_is_not_a_court_site():
    assert collectors.is_court_site("https://www.uscourts.gov/x") and collectors.is_court_site("https://a.ca.us/")
    assert not collectors.is_court_site("https://www.courtlistener.com/opinion/1/")


def test_cross_check_at_most_10_cases(research_sources):
    calls = research_sources()
    cases = [page_case(f"Case {n} v. Example") for n in range(12)] + [page_case("CASE 0 vs Example")]  # a repeat
    findings, gaps, _ = run(collectors.cross_check, CANDIDATE, cases, None)
    assert len(findings) == 10 and len([c for c in calls if c[0] == "courtlistener.find_case"]) == 10
    assert [g.reason for g in gaps] == ["2 cases not checked (at most 10 per research)"]


def test_cross_check_pasted_list(research_sources):
    calls = research_sources()
    pasted = "SYNTHETIC-PASTED-LIST Poe vs. Demo LLC (Example County Court, 2017)"
    findings, _, steps = run(collectors.cross_check, CANDIDATE, [page_case("Poe v. Demo LLC")], pasted)
    (prompt, schema), = ai_calls(calls)
    assert schema is collectors.LIST_SCHEMA and pasted in prompt  # one AI call parses the list
    assert [f.facts["listed_in"] for f in findings] == ["testimony_list"]  # the page's repeat is deduped
    assert findings[0].facts["court"] == "Example County Court" and findings[0].facts["year"] == "2017"
    assert "SYNTHETIC-PASTED-LIST" not in str([f.model_dump() for f in findings])
    assert steps["cross_check"] == ("done", "1 cases: 0 found, 0 unclear, 1 not found")


def test_cross_check_failures_are_gaps(research_sources):
    research_sources({"llm.call_model": llm.ModelError("RAW"), "courtlistener.find_case": RuntimeError("RAW")})
    findings, gaps, steps = run(collectors.cross_check, CANDIDATE, [], "a list")
    assert findings == [] and steps["cross_check"] == ("failed", "Testimony list failed (ModelError)")
    assert "RAW" not in str(gaps)
    research_sources({"courtlistener.find_case": SourceSkipped("COURTLISTENER_TOKEN is not set")})
    findings, gaps, _ = run(collectors.cross_check, CANDIDATE, [page_case("Roe v. Sample")], None)
    assert findings[0].facts["status"] == "not_found"
    assert [g.reason for g in gaps] == [
        "CourtListener case lookup skipped: COURTLISTENER_TOKEN is not set (1 cases)"]
    _, _, steps = run(collectors.cross_check, CANDIDATE, [], None)
    assert steps["cross_check"] == ("skipped", "no cases to check")


# Follow-up loop (DESIGN 4A): the ADK agent (tests/fakes.py FakeAdkModel) follows leads with tools.

NEWS = "https://example.org/news/rivera"  # read in round 1 (a web_testimony result)
CV = "https://example.org/cv/rivera.pdf"  # found by the loop's own web search
LOOP_SEARCH = [{"url": CV, "title": "Alex J. Rivera CV", "facts": ["Lists testimony in Roe v. Sample Corp."]},
               {"url": "https://www.linkedin.com/in/alex-rivera", "title": "LinkedIn", "facts": ["blocked"]}]
QUERY = '"Alex J. Rivera" deposition'


def follow(found, started=None):
    steps = {}

    def step(key, status, detail=None):
        steps[key] = (status, detail)

    findings, gaps = asyncio.run(collectors.follow_up(CANDIDATE, found, step, started or time.monotonic()))
    return findings, gaps, steps


def tool_replies(adk_model):
    """Every tool reply the agent saw, in order: (tool name, reply)."""
    return [(p.function_response.name, p.function_response.response)
            for c in adk_model.seen[-1].contents for p in c.parts or [] if p.function_response]


def test_follow_up_default_replies_done_at_once(research_sources, adk_model):
    calls = research_sources()
    findings, gaps, steps = follow([web_finding(NEWS), page_case("Doe v. Example Co.", "N.D. Example", "2019")])
    assert (findings, gaps) == ([], [])
    assert steps["follow_up"] == ("done", "0 leads followed")
    assert calls == [] and len(adk_model.seen) == 1
    request = adk_model.seen[0]
    prompt = request.contents[0].parts[0].text
    assert "F1 | web_testimony | Synthetic page | https://example.org/news/rivera" in prompt
    assert "F2 | page_testimony_case | Doe v. Example Co. | N.D. Example, 2019 |" in prompt
    assert set(request.tools_dict) == {"web_facts", "read_page", "find_case", "opinion_passages"}


def test_follow_up_calls_tools_and_keeps_their_findings(research_sources, adk_model):
    calls = research_sources({"websearch.search_facts": LOOP_SEARCH})
    adk_model.script = [call("web_facts", query=QUERY, topic="testimony"), call("read_page", url=CV),
                        call("find_case", case_name="Doe v. Example Co."), "DONE"]
    findings, gaps, steps = follow([web_finding(NEWS)])
    assert steps["follow_up"] == ("done", "3 leads followed")
    replies = tool_replies(adk_model)
    assert [name for name, _ in replies] == ["web_facts", "read_page", "find_case"]
    assert replies[0][1]["pages"] == [{"id": "F2", "url": CV, "title": "Alex J. Rivera CV",
                                       "facts": ["Lists testimony in Roe v. Sample Corp."]}]  # LinkedIn dropped
    assert [c["case_name"] for c in replies[1][1]["cases"]] == ["Doe v. Example Co.", "Roe v. Sample Corp."]
    assert replies[2][1]["status"] == "found"
    kinds = [f.kind for f in findings]
    assert kinds == ["web_testimony", "page_testimony_case", "page_testimony_case", "page_credential", "case_check"]
    check = findings[-1]
    assert check.facts["listed_in"] == "expert_page" and check.facts["court"] == "N.D. Example"
    assert PAGE_TEXT not in str([f.model_dump() for f in findings]) and "SNIPPET" not in str(replies)
    (name, queries, topic), _ = next((a, k) for key, a, k in calls if key == "websearch.search_facts")
    assert (name, queries, topic) == ("Alex J. Rivera", [QUERY], "expert testimony")
    assert gaps == []


def test_read_page_refuses_unknown_blocked_and_read_urls(research_sources, adk_model):
    calls = research_sources()
    adk_model.script = [call("read_page", url="https://made-up.example/rivera"),
                        call("read_page", url="https://www.linkedin.com/in/alex-rivera"),
                        call("read_page", url=NEWS), "DONE"]
    findings, _, steps = follow([web_finding(NEWS)])
    assert [r for _, r in tool_replies(adk_model)] == [{"error": collectors.UNKNOWN_URL},
                                                       {"error": collectors.BLOCKED_URL},
                                                       {"error": collectors.ALREADY_READ}]
    assert findings == [] and scraped(calls) == [] and steps["follow_up"] == ("done", "0 leads followed")


def test_uncited_case_and_query_without_full_name_are_refused(research_sources, adk_model):
    calls = research_sources()
    adk_model.script = [call("find_case", case_name="Made Up v. Nobody"),
                        call("web_facts", query="Rivera expert", topic="testimony"),
                        call("web_facts", query=QUERY, topic="gossip"), "DONE"]
    findings, _, _ = follow([page_case("Doe v. Example Co.")])
    assert [r for _, r in tool_replies(adk_model)] == [{"error": collectors.UNKNOWN_CASE},
                                                       {"error": collectors.NEED_FULL_NAME},
                                                       {"error": collectors.BAD_TOPIC}]
    assert findings == [] and calls == []  # an invented case never becomes a finding


def test_web_facts_refuses_blocked_sites_and_a_name_past_the_cut(research_sources, adk_model):
    """The query is cut to QUERY_MAX first, then checked: nothing is sent to web search."""
    calls = research_sources()
    long_query = "x" * websearch.QUERY_MAX + ' "Alex J. Rivera"'
    adk_model.script = [call("web_facts", query=f"{QUERY} site:linkedin.com", topic="credentials"),
                        call("web_facts", query=f"{QUERY} scholar.google.com", topic="testimony"),
                        call("web_facts", query=long_query, topic="testimony"), "DONE"]
    findings, _, _ = follow([])
    assert [r for _, r in tool_replies(adk_model)] == [{"error": collectors.BLOCKED_SITE_QUERY}] * 2 + [
        {"error": collectors.NEED_FULL_NAME}]
    assert findings == [] and calls == []


def test_find_case_needs_an_exact_page_case_name(research_sources, adk_model):
    """Only a page case's own name (normalised) counts: never a prefix of it, nor a case in a web fact."""
    calls = research_sources()
    news = web_finding(NEWS)
    news.facts["facts"] = ["Testified in Roe v. Sample Corp. in 2020"]
    adk_model.script = [call("find_case", case_name="Doe v. Smith"), call("find_case", case_name="Roe v. Sample Corp."),
                        call("find_case", case_name="doe vs. SMITH industries holdings"),
                        call("find_case", case_name="Doe v. Smith Industries Holdings"), "DONE"]
    findings, _, _ = follow([news, page_case("Doe v. Smith Industries Holdings", "N.D. Example", "2019")])
    assert [r for _, r in tool_replies(adk_model)][:2] == [{"error": collectors.UNKNOWN_CASE}] * 2
    assert tool_replies(adk_model)[3][1] == {"error": collectors.ALREADY_CHECKED}
    [check] = findings
    assert check.kind == "case_check" and check.facts["case_name"] == "Doe v. Smith Industries Holdings"
    assert check.facts["listed_in"] == "expert_page" and check.facts["court"] == "N.D. Example"
    assert [a[0] for key, a, _ in calls if key == "courtlistener.find_case"] == ["Doe v. Smith Industries Holdings"]


@pytest.mark.parametrize("url", [
    "https://www.courtlistener.com/opinion/11/doe-v-example/",
    collectors.search_link("Roe v. Sample Corp."),
    "HTTPS://CourtListener.com./opinion/12/",
    "https://api.openalex.org/A0000000001",
    "https://exclusions.oig.hhs.gov/",
])
def test_read_page_refuses_sites_read_through_an_api_source(research_sources, adk_model, url):
    calls = research_sources()
    opinion = web_finding(url)
    opinion.source_type = "courtlistener_opinion"
    adk_model.script = [call("read_page", url=url), "DONE"]
    findings, _, _ = follow([opinion.model_copy(update={"kind": "case_check", "facts": {"status": "not_found"}})])
    assert tool_replies(adk_model) == [("read_page", {"error": collectors.API_SITE})]
    assert findings == [] and scraped(calls) == []


def test_opinion_passages_fills_a_round_one_opinion(research_sources, adk_model):
    calls = research_sources({"courtlistener.search_opinions": [cl_hit(11, "2021-05-01")]})
    opinion = Finding(collector="testimony", kind="court_opinion", title="Doe", url=cl_hit(11, "")["url"],
                      source_type="courtlistener_opinion", retrieved_at="2026-10-28T00:00:00Z", confidence="high",
                      note="opinion text not fetched; no passages",
                      facts={"case_name": "Doe", "court": "N.D. Example", "date_filed": "2021-05-01", "passages": []})
    adk_model.script = [call("opinion_passages", finding_id="f1"), call("opinion_passages", finding_id="F1"), "DONE"]
    findings, _, steps = follow([opinion])
    assert findings == [] and opinion.note is None
    assert opinion.facts["passages"] == ["the plaintiff's expert, Alex J. Rivera, a toxicologist, testified that"]
    assert tool_replies(adk_model)[1][1] == {"error": LIMIT_REACHED}  # cap: 1 opinion without passages
    assert [a for key, a, _ in calls if key == "courtlistener.opinion_passages"] == [(11, "Alex J. Rivera")]
    assert steps["follow_up"] == ("done", "1 leads followed")


def test_follow_up_stops_at_the_step_cap(research_sources, adk_model, monkeypatch):
    research_sources({"websearch.search_facts": LOOP_SEARCH})
    monkeypatch.setattr(collectors, "FOLLOW_MAX_CALLS", 3)
    adk_model.script = [call("web_facts", query=QUERY, topic="testimony")] * 10  # never says DONE
    findings, gaps, steps = follow([])
    assert len(adk_model.seen) == 3
    assert [f.url for f in findings] == [CV]  # tool results are kept; the same page is stored once
    assert steps["follow_up"] == ("done", "3 leads followed; stopped at the step limit")
    assert Gap(step="follow_up", reason="follow-up stopped at its step limit") in gaps


def test_follow_up_stops_at_the_time_limit(research_sources, adk_model, monkeypatch):
    research_sources({"websearch.search_facts": LOOP_SEARCH})
    monkeypatch.setattr(collectors, "FOLLOW_MAX_SECONDS", 0.3)
    monkeypatch.setattr(collectors, "FOLLOW_MIN_SECONDS", 0.1)
    adk_model.script = [call("web_facts", query=QUERY, topic="testimony"), HANG]
    t = time.monotonic()
    findings, _, steps = follow([])
    assert time.monotonic() - t < 2
    assert [f.url for f in findings] == [CV]
    assert steps["follow_up"] == ("done", "1 leads followed; stopped at the time limit")


def test_follow_up_skipped_when_the_job_is_nearly_out_of_time(research_sources, adk_model):
    research_sources()
    findings, gaps, steps = follow([], started=time.monotonic() - collectors.JOB_SECONDS + 10)
    assert steps["follow_up"] == ("skipped", "no time left") and findings == [] and adk_model.seen == []
    assert gaps == [Gap(step="follow_up", reason="no time left to follow leads")]


@pytest.mark.parametrize("source, reason", [
    (RuntimeError("SECRET provider text"), "Web search failed (RuntimeError)"),
    (SourceSkipped("no web search key"), "no web search key"),
])
def test_tool_failure_is_a_gap_and_never_reaches_the_model_as_text(research_sources, adk_model, source, reason):
    research_sources({"websearch.search_facts": source})
    adk_model.script = [call("web_facts", query=QUERY, topic="credentials"), "DONE"]
    findings, gaps, steps = follow([])
    assert findings == [] and steps["follow_up"][0] == "done"
    assert gaps == [Gap(step="follow_up", reason=reason)]
    assert "SECRET" not in str(tool_replies(adk_model))


def test_model_failure_fails_the_step_with_fixed_text(research_sources, adk_model):
    research_sources()
    adk_model.script = [RuntimeError("SECRET provider text")]
    findings, gaps, steps = follow([])
    assert steps["follow_up"] == ("failed", "Follow-up failed (ModelError)")
    assert gaps == [Gap(step="follow_up", reason="Follow-up failed (ModelError)")] and findings == []


def test_research_job_runs_follow_up_last_and_saves_its_findings(research_sources, adk_model):
    def search(name, queries, topic, context=None):  # only the loop's query finds the CV
        return LOOP_SEARCH if queries == [QUERY] else RESEARCH_SOURCES["websearch.search_facts"]

    research_sources({"websearch.search_facts": search})
    assert jobs.RESEARCH_STEPS[-1] == ("follow_up", "Following leads")
    adk_model.script = [call("web_facts", query=QUERY, topic="credentials"), "DONE"]
    job_id = store.create_job("candidate_search", jobs.CANDIDATE_STEPS)
    [cand] = store.save_candidates(store.create_search("Alex Rivera", None, job_id), [CANDIDATE], [])
    job_id = store.create_job("research", jobs.RESEARCH_STEPS)
    dossier_id = store.create_research(cand.candidate_id, job_id)
    asyncio.run(jobs.run_research(job_id, dossier_id, cand))
    job = store.get_job(job_id)
    assert job.status == "done" and [s.key for s in job.steps][-1] == "follow_up"
    assert job.steps[-1].status == "done" and job.steps[-1].detail == "1 leads followed"
    assert "F1 | " in adk_model.seen[0].contents[0].parts[0].text
    saved = store.get_research(dossier_id).findings
    assert CV in [f.url for f in saved if f.kind == "web_credential"]  # the loop's finding is saved
