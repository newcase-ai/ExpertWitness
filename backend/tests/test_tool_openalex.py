"""Tool 9: publications (DESIGN 4B.9) with fake OpenAlex functions. Synthetic people only."""

import asyncio
import json

from app import store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.models import Identifiers
from app.sources import SourceSkipped
from app.tools import RESULT_MAX
from app.tools.openalex import NO_MATCH
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    add_test_file,
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


TITLES = ["Liver enzyme changes after exposure to the synthetic solvent XR-7",
          "Dose timing and recovery in a synthetic Example County lead model",
          "A synthetic review of residue limits for the fictional pesticide ZQ-4"]


def long_work(n, title):
    return {**work(n, 2020 + n), "title": title}


def hit(aid, orcid=None):
    return {"source": "openalex", "openalex_id": aid, "orcid": orcid, "name": "Alex J. Rivera"}


def lookup_sources(research_sources, works_by_id):
    return research_sources({"openalex.search_authors": [hit(a) for a in works_by_id],
                             "openalex.works": lambda aid, limit: works_by_id[aid]})


def test_no_id_looks_up_the_author_whose_titles_are_in_the_cv(tool_ctx, research_sources):
    """A2 shares two long titles with the saved CV (across line breaks and case); A1 is a namesake."""
    add_test_file(tool_ctx, f"Selected publications\n{TITLES[0].upper()}.\n{TITLES[1].replace(' and ', ' and\n')}")
    calls = lookup_sources(research_sources, {
        "A1": [long_work(9, "Unrelated synthetic soil chemistry results from the field")],
        "A2": [long_work(1, TITLES[0]), long_work(2, TITLES[1]), long_work(3, "Editorial")]})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    assert [a["id"] for a in reply["authors"]] == ["A0000000001"]  # the fake author() reply
    assert reply["authors"][0]["matched_by"] == "2 work titles found in collected sources"
    assert [(k, a) for k, a, _ in calls] == [("openalex.search_authors", (EXPERT.display_name,)),
                                             ("openalex.works", ("A1",)), ("openalex.works", ("A2",)),
                                             ("openalex.author", ("A2",))]  # A2's works are not read twice
    assert tool_ctx.findings("author_stats")[0].note == "2 work titles found in collected sources"
    assert reply["works"] == 3


def test_one_matching_title_is_not_enough(tool_ctx, research_sources):
    add_test_file(tool_ctx, TITLES[0])
    lookup_sources(research_sources, {"A1": [long_work(1, TITLES[0]), long_work(2, TITLES[2])]})
    assert asyncio.run(run_tool(tool_ctx, "publications")) == {"error": f"source not available: {NO_MATCH}"}
    assert gaps(tool_ctx) == [("publications", NO_MATCH)] and tool_ctx.findings("work") == []


def test_orcid_in_a_finding_is_a_match(tool_ctx, research_sources):
    tool_ctx.add_finding("web_fact", "Faculty page", "https://example.edu/rivera", facts={"orcid": ORCID})
    research_sources({"openalex.search_authors": [hit("A7", ORCID)], "openalex.works": []})
    reply = asyncio.run(run_tool(tool_ctx, "publications"))
    assert reply["authors"][0]["matched_by"] == "ORCID found in collected sources"


def test_no_author_found_is_skipped(tool_ctx, research_sources):
    calls = research_sources({"openalex.search_authors": []})
    assert asyncio.run(run_tool(tool_ctx, "publications")) == {"error": f"source not available: {NO_MATCH}"}
    assert [k for k, _, _ in calls] == ["openalex.search_authors"]


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
