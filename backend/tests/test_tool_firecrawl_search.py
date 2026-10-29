"""Tool 6: Firecrawl search (DESIGN 4B.9). Fake HTTP only, SYNTHETIC results (gotchas 8, 9)."""

import asyncio
import json

import httpx
import pytest

from app import jobs, memory, store
from app.evidence import TOOL_FAILED
from app.sources import firecrawl_search as source
from app.tools import API_SITE, BANNED_SITE, RESULT_MAX
from app.tools.firecrawl_search import (
    ALL_SITES_REFUSED,
    BLOCKED_SITE_QUERY,
    EMPTY_QUERY,
    NO_HITS,
    NOT_A_SITE,
    QUERY_TOO_LONG,
    TOO_MANY_SITES,
)
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    call,
    research_sources_fixture,
    run_tool,
    sources_fixture,
    tool_ctx_fixture,
)

SNIPPET = "SYNTHETIC-SEARCH-SNIPPET"  # marker: snippet text must never be stored or returned
QUERY = "Rivera toxicology testimony"
SITE = "courts.example.gov"
ON_SITE = "https://courts.example.gov/opinions/2019-17"
SUB_SITE = "https://records.courts.example.gov/docket/42"


def hit(url, title="Docket entry", description=f"{SNIPPET} unrelated text"):
    return {"url": url, "title": title, "description": description}


HITS = [
    hit(ON_SITE, title="Doe v. Example Co. - testimony of Alex Rivera"),  # name in the title
    hit(SUB_SITE, description=f"{SNIPPET} expert Dr. Alex J. Rivera testified"),  # name in the snippet
    hit("https://other.example.com/rivera", title="Alex Rivera, toxicologist"),  # off the asked site
    hit("https://courts.example.gov/opinions/7", title="Rivera v. State"),  # surname only (gotcha 13)
    hit("https://www.linkedin.com/in/alex-rivera", title="Alex Rivera"),  # gotcha 2
]


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")


def answer(web, status=200, success=True):
    body = {"success": success, "data": {"web": web}, "creditsUsed": 2}
    return {"api.firecrawl.dev": lambda request: httpx.Response(status, json=body)}


def bodies(seen):
    return [json.loads(r.content) for r in seen if r.url.host == "api.firecrawl.dev"]


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def stored(ctx, tmp_path) -> str:
    db = (tmp_path / "data" / "test.db").read_bytes().decode(errors="ignore")
    return db + store.get_job(ctx.job_id).model_dump_json() + json.dumps(
        [a.model_dump() for a in store.activity(ctx.job_id)])


def search(ctx, **args):
    return asyncio.run(run_tool(ctx, "firecrawl_search", **args))


def test_site_hits_saved_with_urls_and_no_snippet(tool_ctx, sources, with_key, tmp_path):
    _, seen = sources(routes=answer(HITS))
    reply = search(tool_ctx, query=QUERY, sites=[f"https://www.{SITE}/search"])
    [body] = bodies(seen)
    assert seen[0].headers["Authorization"] == "Bearer fc-test-key"
    assert str(seen[0].url) == source.SEARCH
    assert body == {"query": QUERY, "limit": 10, "sources": [{"type": "web"}], "highlights": False,
                    "timeout": source.TIMEOUT_MS, "includeDomains": [SITE]}  # one request, no scrapeOptions
    found = {f.url: f for f in tool_ctx.findings()}
    assert set(found) == {ON_SITE, SUB_SITE}  # off-site, surname-only and LinkedIn hits dropped
    for f in found.values():
        assert (f.kind, f.collector, f.confidence, f.source_type) == ("site_hit", "firecrawl_search", "low", "web")
    assert found[ON_SITE].facts == {"query": QUERY, "site": SITE, "matched": "title"}
    assert found[SUB_SITE].facts == {"query": QUERY, "site": SITE, "matched": "snippet"}
    assert found[SUB_SITE].title == "Docket entry"
    assert reply == {"not_shown": 0, "hits": [
        {"ref": found[u].finding_id, "title": found[u].title, "url": u} for u in (ON_SITE, SUB_SITE)]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert rows(tool_ctx) == [("firecrawl_search", "done", "5 found, 2 saved, 0 sites skipped")]
    text = stored(tool_ctx, tmp_path)
    assert SNIPPET not in text and SNIPPET not in json.dumps(reply)  # gotcha 15
    assert "other.example.com" not in text and "linkedin" not in text


def test_open_web_search_with_no_sites(tool_ctx, sources, with_key):
    web = [hit("https://example.org/news/rivera", title="Alex Rivera testifies"),
           hit("https://www.courtlistener.com/opinion/1/x/", title="Alex Rivera opinion")]  # API host
    _, seen = sources(routes=answer(web))
    reply = search(tool_ctx, query='"Alex Rivera" toxicology')
    [body] = bodies(seen)
    assert "includeDomains" not in body and "linkedin.com" in body["excludeDomains"]
    [found] = tool_ctx.findings()
    assert (found.url, found.facts["site"], found.facts["matched"]) == ("https://example.org/news/rivera", None,
                                                                         "title")
    assert reply == {"hits": [{"ref": found.finding_id, "title": found.title, "url": found.url}], "not_shown": 0}


@pytest.mark.parametrize(("site", "reason"), [
    ("linkedin.com", BANNED_SITE),
    ("www.courtlistener.com", API_SITE),
    ("blocked.example.org", "blocked site: captcha"),
    ("not a site", NOT_A_SITE),
])
def test_refused_site_is_skipped_with_its_reason(tool_ctx, sources, with_key, site, reason):
    memory.block_site("blocked.example.org", "captcha")
    _, seen = sources(routes=answer(HITS))
    reply = search(tool_ctx, query=QUERY, sites=[site, SITE])
    assert reply["skipped_sites"] == [{"site": site, "reason": reason}]
    assert [b["includeDomains"] for b in bodies(seen)] == [[SITE]]


@pytest.mark.parametrize("site", [
    "example.com OR linkedin.com", "example.com|linkedin.com", "site:linkedin.com", "1.2.3.4",
    "10.0.0.1:80", "1.0x1", "0177.0.0.1", "[2001:db8::1]", "https://[::1]/x", "localhost", "a_b.example.com", "",
])
def test_only_real_host_names_are_sites(tool_ctx, sources, with_key, site):
    _, seen = sources(routes=answer(HITS))
    reply = search(tool_ctx, query=QUERY, sites=[site, SITE])
    assert reply["skipped_sites"] == [{"site": site, "reason": NOT_A_SITE}]
    assert [b["includeDomains"] for b in bodies(seen)] == [[SITE]]
    assert search(tool_ctx, query=QUERY, sites=[site]) == {"error": ALL_SITES_REFUSED}
    assert len(bodies(seen)) == 1


def test_a_banned_host_never_reaches_the_query(tool_ctx, sources, with_key):
    _, seen = sources(routes=answer(HITS))
    sites = ["https://uk.linkedin.com/in/x", "WWW.LinkedIn.com.", "example.com OR linkedin.com",
             "me@www.linkedin.co.uk", SITE]
    reply = search(tool_ctx, query=QUERY, sites=sites)
    assert len(reply["skipped_sites"]) == 4
    [body] = bodies(seen)
    assert body["includeDomains"] == [SITE] and "linkedin" not in json.dumps(body)


def test_duplicate_sites_count_once(tool_ctx, sources, with_key):
    _, seen = sources(routes=answer(HITS))
    others = [f"{c}.example.gov" for c in "abcd"]
    sites = [SITE, f"www.{SITE}", f"https://{SITE}/x", SITE.upper(), *others, others[0]]
    assert "hits" in search(tool_ctx, query=QUERY, sites=sites)
    assert [b["includeDomains"] for b in bodies(seen)] == [[SITE, *others]]


def test_listing_pages_are_dropped(tool_ctx, sources, with_key):
    web = [hit(ON_SITE, title="Search results for Alex Rivera"),
           hit("https://courts.example.gov/search?q=alex+rivera", title="Alex Rivera - court records"),
           hit(SUB_SITE, title="Alex Rivera docket")]
    sources(routes=answer(web))
    reply = search(tool_ctx, query=QUERY, sites=[SITE])
    assert [h["url"] for h in reply["hits"]] == [SUB_SITE]
    assert [f.url for f in tool_ctx.findings()] == [SUB_SITE]


def test_all_sites_refused_is_a_refusal(tool_ctx, sources, with_key):
    memory.block_site("blocked.example.org", "captcha")
    _, seen = sources(routes=answer(HITS))
    reply = search(tool_ctx, query=QUERY, sites=["linkedin.com", "courtlistener.com", "sub.blocked.example.org"])
    assert reply == {"error": ALL_SITES_REFUSED}
    assert bodies(seen) == [] and tool_ctx.findings() == []
    assert rows(tool_ctx)[0][:2] == ("firecrawl_search", "refused")


def test_more_than_five_sites_is_refused(tool_ctx, sources, with_key):
    _, seen = sources(routes=answer(HITS))
    sites = [f"s{i}.example.gov" for i in range(6)]
    assert search(tool_ctx, query=QUERY, sites=sites) == {"error": TOO_MANY_SITES}
    assert bodies(seen) == []
    search(tool_ctx, query=QUERY, sites=sites[:5])  # five is fine: one request for all of them
    assert [b["includeDomains"] for b in bodies(seen)] == [sites[:5]]


@pytest.mark.parametrize(("query", "refusal"), [
    ("   ", EMPTY_QUERY),
    ('"Alex Rivera" site:linkedin.com', BLOCKED_SITE_QUERY),
    ("x" * 201, QUERY_TOO_LONG),
])
def test_refused_queries_never_reach_firecrawl(tool_ctx, sources, with_key, query, refusal):
    _, seen = sources(routes=answer(HITS))
    assert search(tool_ctx, query=query) == {"error": refusal}
    assert bodies(seen) == []


BYPASSES = ["Alex Rivera|site:linkedin.com", '"Alex Rivera"site:linkedin.com', "Alex Rivera inurl:linkedin.com",
            "Alex Rivera related:linkedin.com", "Alex Rivera cache:linkedin.com",
            "Alex Rivera site:linkedin\u3002com", "Alex Rivera site:linked\u200bin.com"]


@pytest.mark.parametrize("query", BYPASSES)
def test_banned_site_in_any_query_form_is_refused_by_both_tools(tool_ctx, sources, with_key, research_sources,
                                                                query):
    _, seen = sources(routes=answer(HITS))
    calls = research_sources()
    assert search(tool_ctx, query=query) == {"error": BLOCKED_SITE_QUERY}
    assert asyncio.run(run_tool(tool_ctx, "web_search", query=query, topic="other")) == {
        "error": BLOCKED_SITE_QUERY}
    assert bodies(seen) == [] and calls == []


@pytest.mark.parametrize("query", ["Alex Rivera expert witness Michigan", "Doe v. Smith 2019 Alex Rivera",
                                   "Alex J. Rivera Ph.D. site:example.edu"])
def test_normal_queries_pass(tool_ctx, sources, with_key, query):
    _, seen = sources(routes=answer(HITS))
    assert "hits" in search(tool_ctx, query=query)
    assert [b["query"] for b in bodies(seen)] == [query]


def test_sites_must_be_a_list(tool_ctx, sources, with_key):
    _, seen = sources(routes=answer(HITS))
    assert "error" in search(tool_ctx, query=QUERY, sites=SITE)
    assert bodies(seen) == [] and rows(tool_ctx) == [("firecrawl_search", "refused", "bad arguments")]


def test_no_key_is_skipped_with_a_gap(tool_ctx, sources, monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    _, seen = sources(routes=answer(HITS))
    reply = search(tool_ctx, query=QUERY)
    assert "FIRECRAWL_API_KEY" in reply["error"] and bodies(seen) == []
    assert rows(tool_ctx)[0][:2] == ("firecrawl_search", "skipped")
    assert [step for step, _ in gaps(tool_ctx)] == ["firecrawl_search"]


@pytest.mark.parametrize("routes", [answer([], status=500), answer([], success=False),
                                    {"api.firecrawl.dev": (200, {"success": True, "data": {}})}])
def test_firecrawl_failure_is_tool_failed(tool_ctx, sources, with_key, routes):
    sources(routes=routes)
    assert search(tool_ctx, query=QUERY) == {"error": TOOL_FAILED}
    assert rows(tool_ctx) == [("firecrawl_search", "failed", "RuntimeError")]
    assert gaps(tool_ctx) == [("firecrawl_search", "Search the web or chosen sites failed (RuntimeError)")]


def test_no_matching_hits_is_a_gap(tool_ctx, sources, with_key):
    sources(routes=answer([hit("https://example.org/a", title="Unrelated page")]))
    assert search(tool_ctx, query=QUERY) == {"hits": [], "not_shown": 0}
    assert gaps(tool_ctx) == [("firecrawl_search", NO_HITS)]


def test_runs_through_the_main_agent_with_a_sites_list(tool_ctx, sources, with_key, adk_model):
    sources(routes=answer(HITS))
    adk_model.script = [call("firecrawl_search", query=QUERY, sites=[SITE]), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done"
    assert [(a.tool, a.label, a.status) for a in job.activity] == [
        ("firecrawl_search", "Search the web or chosen sites", "done")]
    assert {f.url for f in tool_ctx.findings()} == {ON_SITE, SUB_SITE}
