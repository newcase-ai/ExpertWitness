"""Tool 10: retraction check (DESIGN 4B.9) with fake Crossref and OpenAlex functions. Synthetic data only."""

import asyncio
import json

from app import jobs, store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.models import Identifiers
from app.tools import RESULT_MAX
from app.tools.crossref import NO_WORKS, REASONS_GAP
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    call,
    research_sources_fixture,
    run_tool,
    tool_ctx_fixture,
    work,
)

UNCHECKED = ("{} works not checked in Crossref (no DOI or beyond the newest 50); only OpenAlex's flag was read")


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def with_works(ctx, research_sources, overrides=None):
    """Save works through tool 9, as the main agent would."""
    ctx.expert = EXPERT.model_copy(update={"identifiers": Identifiers(openalex=["A0000000001"])})
    calls = research_sources(overrides)
    assert "error" not in asyncio.run(run_tool(ctx, "publications"))
    return calls


def test_retractions_from_crossref_and_openalex_flag(tool_ctx, research_sources):
    """Default fakes: work 2 is retracted in Crossref; work 3 has no DOI, only OpenAlex's flag."""
    calls = with_works(tool_ctx, research_sources)
    reply = asyncio.run(run_tool(tool_ctx, "retraction_check"))
    crossref_found, openalex_found = tool_ctx.findings("retraction")
    assert (crossref_found.url, crossref_found.source_type, crossref_found.confidence) == (
        "https://doi.org/10.5555/notice.2", "crossref_work", "high")
    assert crossref_found.facts == {"doi": "10.5555/synthetic.2", "date": "2022-03-01", "source": "retraction-watch",
                                    "notice_url": "https://doi.org/10.5555/notice.2",
                                    "openalex_is_retracted": False}
    assert (openalex_found.url, openalex_found.source_type, openalex_found.facts) == (
        "https://openalex.org/W3", "openalex_work", {"doi": None, "flagged_by": "openalex"})
    assert reply == {"checked_in_crossref": 2, "works": 3, "retractions": [
        {"ref": crossref_found.finding_id, "title": "Synthetic toxicology study 2", "doi": "10.5555/synthetic.2",
         "found_by": "crossref"},
        {"ref": openalex_found.finding_id, "title": "Synthetic toxicology study 3", "doi": None,
         "found_by": "openalex"}]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert [a for k, a, _ in calls if k == "crossref.retraction"] == [("10.5555/synthetic.1",),
                                                                      ("10.5555/synthetic.2",)]
    assert gaps(tool_ctx) == [("retraction_check", REASONS_GAP), ("retraction_check", UNCHECKED.format(1))]
    assert rows(tool_ctx)[1] == ("retraction_check", "done", "2 DOIs checked in Crossref, 2 retractions")


def test_openalex_flag_only(tool_ctx, research_sources):
    with_works(tool_ctx, research_sources, {"openalex.works": [work(7, 2019, retracted=True)],
                                            "crossref.retraction": None})
    reply = asyncio.run(run_tool(tool_ctx, "retraction_check"))
    [found] = tool_ctx.findings("retraction")
    assert (found.url, found.source_type, found.note) == (
        "https://doi.org/10.5555/synthetic.7", "openalex_work",
        "flagged as retracted by OpenAlex; no Crossref notice found")
    assert [r["found_by"] for r in reply["retractions"]] == ["openalex"] and reply["checked_in_crossref"] == 1


def test_newest_fifty_dois_are_checked(tool_ctx, research_sources):
    calls = with_works(tool_ctx, research_sources, {"openalex.works": [work(n, 1950 + n) for n in range(60)],
                                                    "crossref.retraction": None})
    reply = asyncio.run(run_tool(tool_ctx, "retraction_check"))
    checked = [a[0] for k, a, _ in calls if k == "crossref.retraction"]
    assert checked == [f"10.5555/synthetic.{n}" for n in range(59, 9, -1)]
    assert reply == {"retractions": [], "checked_in_crossref": 50, "works": 60}
    assert ("retraction_check", UNCHECKED.format(10)) in gaps(tool_ctx)


def test_before_publications_is_refused(tool_ctx, research_sources):
    calls = research_sources()
    assert asyncio.run(run_tool(tool_ctx, "retraction_check")) == {"error": NO_WORKS}
    assert rows(tool_ctx) == [("retraction_check", "refused", NO_WORKS)] and calls == []


def test_one_doi_failing_is_a_gap(tool_ctx, research_sources):
    def flaky(doi):
        if doi.endswith(".1"):
            raise RuntimeError("Crossref HTTP 500")

    with_works(tool_ctx, research_sources, {"crossref.retraction": flaky})
    reply = asyncio.run(run_tool(tool_ctx, "retraction_check"))
    assert reply["checked_in_crossref"] == 1 and rows(tool_ctx)[1][1] == "done"
    assert ("retraction_check", "Crossref check failed for 1 DOIs") in gaps(tool_ctx)


def test_crossref_failing_is_tool_failed(tool_ctx, research_sources):
    with_works(tool_ctx, research_sources, {"crossref.retraction": RuntimeError("Crossref HTTP 503")})
    assert asyncio.run(run_tool(tool_ctx, "retraction_check")) == {"error": TOOL_FAILED}
    assert rows(tool_ctx)[1] == ("retraction_check", "failed", "RuntimeError")
    assert ("retraction_check", "Retraction check failed (RuntimeError)") in gaps(tool_ctx)
    assert [f.source_type for f in tool_ctx.findings("retraction")] == ["openalex_work"]  # the flag is kept


def test_cap(tool_ctx, research_sources):
    with_works(tool_ctx, research_sources)
    replies = [asyncio.run(run_tool(tool_ctx, "retraction_check")) for _ in range(3)]
    assert replies[0] == replies[1] and replies[2] == {"error": LIMIT_REACHED}
    assert len(tool_ctx.findings("retraction")) == 2  # same kind + URL: saved once
    assert rows(tool_ctx)[3] == ("retraction_check", "refused", "cap used up")


def test_publications_and_retraction_check_run_through_the_main_agent(tool_ctx, research_sources, adk_model):
    research_sources()
    expert = EXPERT.model_copy(update={"identifiers": Identifiers(openalex=["A0000000001"])})
    adk_model.script = [call("publications"), call("retraction_check"), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, expert))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done"
    assert [(a.tool, a.label, a.status) for a in job.activity] == [
        ("publications", "Publications", "done"), ("retraction_check", "Retraction check", "done")]
    assert {f.kind for f in tool_ctx.findings()} == {"author_stats", "work", "retraction"}
    tools = adk_model.seen[0].tools_dict
    assert {"publications", "retraction_check"} <= set(tools)
