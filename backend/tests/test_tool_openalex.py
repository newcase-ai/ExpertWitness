"""Tool 9: publications (DESIGN 4B.9) with fake OpenAlex functions. Synthetic people only."""

import asyncio
import json

from app import store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.models import Identifiers
from app.sources import SourceSkipped
from app.tools import RESULT_MAX
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    research_sources_fixture,
    run_tool,
    tool_ctx_fixture,
    work,
)

ORCID = "0000-4114-4269-4203"  # real format (valid check character) that also passes the card checksum


def author(aid):
    return {"id": aid, "url": f"https://openalex.org/{aid}", "display_name": "Alex J. Rivera", "works_count": 3,
            "cited_by_count": 40, "h_index": 2, "orcid": ORCID, "last_institution": "Example State University"}


def with_ids(ctx, *ids):
    ctx.expert = EXPERT.model_copy(update={"identifiers": Identifiers(openalex=list(ids))})


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def test_publications_saves_author_records_and_works(tool_ctx, research_sources):
    with_ids(tool_ctx, "A0000000001")
    calls = research_sources({"openalex.author": author})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    [stats] = tool_ctx.findings("author_stats")
    assert (stats.url, stats.source_type, stats.confidence, stats.collector) == (
        "https://openalex.org/A0000000001", "openalex_author", "high", "publications")
    assert stats.facts["orcid"] == ORCID and stats.facts["h_index"] == 2
    works = tool_ctx.findings("work")
    assert [(w.url, w.source_type, w.facts["year"]) for w in works] == [  # work 1 listed twice: saved once
        ("https://doi.org/10.5555/synthetic.1", "openalex_work", 2021),
        ("https://doi.org/10.5555/synthetic.2", "openalex_work", 2020),
        ("https://openalex.org/W3", "openalex_work", 2015)]
    assert works[2].facts == {"id": "W3", "year": 2015, "venue": "Example Journal", "doi": None,
                              "cited_by_count": 3, "is_retracted": True}
    assert reply == {
        "authors": [{"ref": stats.finding_id, "id": "A0000000001", "works_count": 3, "cited_by_count": 40,
                     "h_index": 2, "orcid": ORCID}],
        "works": 3,
        "newest_works": [{"ref": w.finding_id, "title": w.title, "year": w.facts["year"], "doi": w.facts["doi"]}
                         for w in works]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert [(k, a, kw) for k, a, kw in calls] == [("openalex.author", ("A0000000001",), {}),
                                                  ("openalex.works", ("A0000000001",), {"limit": 100})]
    assert rows(tool_ctx) == [("publications", "done", "1 OpenAlex ids, 1 author records, 3 works")]
    assert gaps(tool_ctx) == []


def test_first_three_ids_are_merged(tool_ctx, research_sources):
    """Gotcha 12: one person split over several author ids; works shared between ids are saved once."""
    with_ids(tool_ctx, "A1", "A2", "A3", "A4")
    by_id = {"A1": [work(1, 2021), work(2, 2020)], "A2": [work(2, 2020), work(4, 2022)], "A3": []}
    calls = research_sources({"openalex.author": author, "openalex.works": lambda aid, limit: by_id[aid]})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    assert [a for k, (a,), _ in calls if k == "openalex.author"] == ["A1", "A2", "A3"]
    assert len(tool_ctx.findings("author_stats")) == 3
    assert [w["year"] for w in reply["newest_works"]] == [2022, 2021, 2020] and reply["works"] == 3


def test_newest_ten_in_the_reply(tool_ctx, research_sources):
    with_ids(tool_ctx, "A1")
    research_sources({"openalex.works": [work(n, 1990 + n) for n in range(30)]})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    assert reply["works"] == 30 and len(tool_ctx.findings("work")) == 30
    assert [w["year"] for w in reply["newest_works"]] == list(range(2019, 2009, -1))
    assert len(json.dumps(reply)) <= RESULT_MAX


def test_no_openalex_id_is_skipped(tool_ctx, research_sources):
    calls = research_sources()
    assert asyncio.run(run_tool(tool_ctx, "publications")) == {"error": "source not available: no OpenAlex id"}
    assert rows(tool_ctx) == [("publications", "skipped", "no OpenAlex id")]
    assert gaps(tool_ctx) == [("publications", "no OpenAlex id")] and calls == []


def test_one_id_failing_is_a_gap(tool_ctx, research_sources):
    with_ids(tool_ctx, "A1", "A2")

    def flaky(aid, limit):
        if aid == "A2":
            raise RuntimeError("OpenAlex HTTP 500")
        return [work(1, 2021)]

    research_sources({"openalex.works": flaky})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    assert reply["works"] == 1 and len(reply["authors"]) == 2
    assert rows(tool_ctx)[0][1] == "done"
    assert gaps(tool_ctx) == [("publications", "OpenAlex failed (RuntimeError)")]


def test_source_failing_is_tool_failed(tool_ctx, research_sources):
    with_ids(tool_ctx, "A1", "A2")
    research_sources({"openalex.author": RuntimeError("down"), "openalex.works": RuntimeError("down")})
    assert asyncio.run(run_tool(tool_ctx, "publications")) == {"error": TOOL_FAILED}
    assert rows(tool_ctx) == [("publications", "failed", "RuntimeError")]
    assert gaps(tool_ctx) == [("publications", "Publications failed (RuntimeError)")]
    assert tool_ctx.findings() == []


def test_budget_used_up_is_skipped(tool_ctx, research_sources):
    with_ids(tool_ctx, "A1")
    budget = SourceSkipped("OpenAlex daily budget nearly used up ($0.0010 left)")
    research_sources({"openalex.author": budget, "openalex.works": budget})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    assert reply == {"error": f"source not available: {budget}"}
    assert rows(tool_ctx)[0][1] == "skipped" and gaps(tool_ctx) == [("publications", str(budget))]


def test_cap(tool_ctx, research_sources):
    with_ids(tool_ctx, "A1")
    research_sources()
    assert "error" not in asyncio.run(run_tool(tool_ctx, "publications"))
    assert asyncio.run(run_tool(tool_ctx, "publications")) == {"error": LIMIT_REACHED}
    assert rows(tool_ctx)[1] == ("publications", "refused", "cap used up")
