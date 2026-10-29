"""Tool 12: web search (DESIGN 4B.9) with a fake websearch.search_facts. Synthetic people only."""

import asyncio
import json

import pytest

from app import jobs, store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.tools import RESULT_MAX
from app.tools.web_search import BLOCKED_SITE_QUERY, NEED_FULL_NAME, QUERY_TOO_LONG
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    call,
    research_sources_fixture,
    run_tool,
    tool_ctx_fixture,
)

SNIPPET = "SYNTHETIC-WEB-SNIPPET"  # marker: page text must never reach the database
QUERY = '"Alex Rivera" expert witness toxicology'


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def stored(ctx, tmp_path) -> str:
    db = (tmp_path / "data" / "test.db").read_bytes().decode(errors="ignore")
    return db + store.get_job(ctx.job_id).model_dump_json() + json.dumps([a.model_dump() for a in
                                                                         store.activity(ctx.job_id)])


def test_saves_url_title_and_facts_only(tool_ctx, research_sources, tmp_path):
    """Default fake: one page (with a snippet marker) and one LinkedIn page (gotcha 2)."""
    calls = research_sources()
    reply = asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic="testimony"))
    assert calls == [("websearch.search_facts", ("Alex Rivera", [QUERY], "expert testimony"),
                      {"context": "toxicology, Ohio, US"})]
    [found] = tool_ctx.findings()
    assert (found.kind, found.collector, found.source_type, found.confidence, found.note) == (
        "web_testimony", "web_search", "web", "low", "verify")
    assert (found.url, found.title) == ("https://example.org/news/rivera", "Expert testifies in example trial")
    assert found.facts == {"facts": ["Testified for the plaintiff in a 2020 example trial"]}
    assert reply == {"not_shown": 0, "results": [
        {"ref": found.finding_id, "title": found.title, "url": found.url, "facts": found.facts["facts"]}]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert rows(tool_ctx) == [("web_search", "done", "2 found, 1 saved")]
    text = stored(tool_ctx, tmp_path)
    assert SNIPPET not in text and SNIPPET not in json.dumps(reply)  # gotcha 15
    assert "linkedin" not in text.replace(QUERY, "")


@pytest.mark.parametrize(("topic", "search_topic", "kind"), [
    ("testimony", "expert testimony", "web_testimony"),
    ("credentials", "licences, board certification, discipline", "web_credential"),
    ("other", "professional background", "web_fact"),
])
def test_topic_picks_the_kind(tool_ctx, research_sources, topic, search_topic, kind):
    calls = research_sources()
    asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic=topic))
    assert calls[0][1][2] == search_topic
    assert [f.kind for f in tool_ctx.findings()] == [kind]
    assert tool_ctx.findings()[0].note.startswith("verify")


@pytest.mark.parametrize(("query", "refusal"), [
    ("Rivera expert witness toxicology", NEED_FULL_NAME),
    ('"Alex Rivera" site:linkedin.com', BLOCKED_SITE_QUERY),
    ('"Alex Rivera" scholar.google.com', BLOCKED_SITE_QUERY),
    ('"Alex Rivera" ' + "x" * 200, QUERY_TOO_LONG),
])
def test_refused_queries_never_reach_the_source(tool_ctx, research_sources, query, refusal):
    calls = research_sources()
    assert asyncio.run(run_tool(tool_ctx, "web_search", query=query, topic="testimony")) == {"error": refusal}
    assert calls == [] and tool_ctx.findings() == []
    assert [r[1] for r in rows(tool_ctx)] == ["refused"]


def test_bad_topic_is_bad_arguments(tool_ctx, research_sources):
    calls = research_sources()
    assert "error" in asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic="gossip"))
    assert calls == [] and rows(tool_ctx) == [("web_search", "refused", "bad arguments")]


def test_many_results_keep_the_reply_short(tool_ctx, research_sources):
    page = {"title": "T" * 200, "facts": ["F" * 200] * 3}
    research_sources({"websearch.search_facts": [page | {"url": f"https://example.org/p/{i}"} for i in range(12)]})
    reply = asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic="other"))
    assert len(json.dumps(reply)) <= RESULT_MAX and reply["not_shown"] > 0
    assert len(reply["results"]) + reply["not_shown"] == len(tool_ctx.findings()) == 12


def test_no_results_is_a_gap(tool_ctx, research_sources):
    research_sources({"websearch.search_facts": []})
    assert asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic="testimony")) == {
        "results": [], "not_shown": 0}
    assert gaps(tool_ctx) == [("web_search", "no web results")]


def test_source_failing_is_tool_failed(tool_ctx, research_sources):
    research_sources({"websearch.search_facts": RuntimeError("search down")})
    assert asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic="testimony")) == {"error": TOOL_FAILED}
    assert rows(tool_ctx) == [("web_search", "failed", "RuntimeError")]
    assert gaps(tool_ctx) == [("web_search", "Web search failed (RuntimeError)")]


def test_cap_is_six(tool_ctx, research_sources):
    research_sources()
    replies = [asyncio.run(run_tool(tool_ctx, "web_search", query=QUERY, topic="testimony")) for _ in range(7)]
    assert all("results" in r for r in replies[:6]) and replies[6] == {"error": LIMIT_REACHED}
    assert len(tool_ctx.findings()) == 1  # same kind + URL: one finding
    assert rows(tool_ctx)[-1] == ("web_search", "refused", "cap used up")


def test_both_tools_run_through_the_main_agent(tool_ctx, research_sources, adk_model):
    research_sources()
    adk_model.script = [call("exclusion_check"), call("web_search", query=QUERY, topic="testimony"), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done"
    assert [(a.tool, a.label, a.status) for a in job.activity] == [
        ("exclusion_check", "OIG exclusion list", "done"), ("web_search", "Web search", "done")]
    assert {f.kind for f in tool_ctx.findings()} == {"leie_possible_match", "web_testimony"}
