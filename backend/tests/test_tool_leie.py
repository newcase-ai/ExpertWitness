"""Tool 11: exclusion check (DESIGN 4B.9) with a fake LEIE source. Synthetic people only."""

import asyncio
import json

import httpx

from app import store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.tools import RESULT_MAX
from app.tools.leie import LEIE_NOTE
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    research_sources_fixture,
    run_tool,
    tool_ctx_fixture,
)

SEARCH = "https://exclusions.oig.hhs.gov/"


def match(state, general="SYNTHETIC GENERAL"):
    return {"general": general, "specialty": "SYNTHETIC SPECIALTY", "excltype": "1128a1", "excldate": "20200101",
            "reindate": "", "state": state, "url": SEARCH}


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def test_possible_matches_saved_and_other_states_dropped(tool_ctx, research_sources):
    calls = research_sources({"leie.possible_matches": [match("OH"), match("TX"), match("oh ", "OTHER"),
                                                         match("")]})
    reply = asyncio.run(run_tool(tool_ctx, "exclusion_check"))
    assert calls == [("leie.possible_matches", ("alex", "rivera"), {"middle_initial": None})]
    found = tool_ctx.findings()
    assert [f.url for f in found] == [f"{SEARCH}#possible-match-{i}" for i in (1, 2, 3)]  # each its own finding
    first = found[0]
    assert (first.kind, first.collector, first.source_type, first.confidence, first.note) == (
        "leie_possible_match", "exclusion_check", "oig_leie", "low", LEIE_NOTE)
    assert first.title == "OIG exclusion list: possible match (SYNTHETIC GENERAL)"
    assert first.facts == {k: v for k, v in match("OH").items() if k != "url"}
    assert reply == {"possible_matches": 3, "refs": [f.finding_id for f in found]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert gaps(tool_ctx) == [("exclusion_check",
                               "1 OIG exclusion list name matches skipped: listed in another state than OH")]
    assert rows(tool_ctx) == [("exclusion_check", "done", "3 possible matches")]


def test_unknown_state_keeps_every_match_and_middle_initial_is_sent(tool_ctx, research_sources):
    tool_ctx.expert = EXPERT.model_copy(update={"display_name": "Dr. Alex J. Rivera", "location": None})
    calls = research_sources({"leie.possible_matches": [match("OH"), match("TX")]})
    assert asyncio.run(run_tool(tool_ctx, "exclusion_check"))["possible_matches"] == 2
    assert calls[0][2] == {"middle_initial": "J"}
    assert gaps(tool_ctx) == []


def test_no_matches(tool_ctx, research_sources):
    research_sources({"leie.possible_matches": []})
    assert asyncio.run(run_tool(tool_ctx, "exclusion_check")) == {"possible_matches": 0, "refs": []}
    assert tool_ctx.findings() == [] and rows(tool_ctx) == [("exclusion_check", "done", "0 possible matches")]


def test_source_failing_is_tool_failed(tool_ctx, research_sources):
    """PLAN known problem: the live CSV download fails with ConnectError."""
    research_sources({"leie.possible_matches": httpx.ConnectError("synthetic connect failure")})
    assert asyncio.run(run_tool(tool_ctx, "exclusion_check")) == {"error": TOOL_FAILED}
    assert rows(tool_ctx) == [("exclusion_check", "failed", "ConnectError")]
    assert gaps(tool_ctx) == [("exclusion_check", "OIG exclusion list failed (ConnectError)")]


def test_one_word_name_is_skipped(tool_ctx, research_sources):
    tool_ctx.expert = EXPERT.model_copy(update={"display_name": "Dr. Rivera"})
    calls = research_sources()
    reply = asyncio.run(run_tool(tool_ctx, "exclusion_check"))
    assert reply == {"error": "source not available: need a given and a family name for the LEIE check"}
    assert calls == [] and [r[1] for r in rows(tool_ctx)] == ["skipped"]
    assert gaps(tool_ctx) == [("exclusion_check", "need a given and a family name for the LEIE check")]


def test_cap_is_one(tool_ctx, research_sources):
    research_sources()
    assert "error" not in asyncio.run(run_tool(tool_ctx, "exclusion_check"))
    assert asyncio.run(run_tool(tool_ctx, "exclusion_check")) == {"error": LIMIT_REACHED}
    assert rows(tool_ctx)[-1] == ("exclusion_check", "refused", "cap used up")
