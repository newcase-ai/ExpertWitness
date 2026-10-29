"""Tool 1: court records (DESIGN 4B.9) with fake CourtListener functions. Synthetic people only."""

import asyncio
import json

from app import jobs, store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.models import Evidence
from app.tools import RESULT_MAX
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    call,
    research_sources_fixture,
    run_tool,
    sources_fixture,
    tool_ctx_fixture,
)

SNIPPET = "SYNTHETIC-CL-SNIPPET"  # marker: search snippets must never reach the database
FULL = "the plaintiff's expert, Alex J. Rivera, a toxicologist, testified that"  # full name + field word
SURNAME = "the plaintiff's expert, Dr. Rivera, a toxicologist, testified that"
NO_FIELD = "the court heard Alex Rivera, who testified that"


def hit(oid, date, court="District Court, N.D. Example"):
    return {"source": "courtlistener", "url": f"https://www.courtlistener.com/opinion/{oid}/doe-v-example/",
            "title": f"Doe v. Example Co. {oid}", "note": f"{court}, filed {date}", "query": "expert",
            "case": (f"Doe v. Example Co. {oid}", court, date), "opinion_id": oid,
            "snippet": f"{SNIPPET} expert Alex Rivera"}


def passages_by_id(texts: dict):
    return lambda opinion_id, name: [texts[opinion_id]] if texts.get(opinion_id) else []


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def stored(ctx, tmp_path) -> str:
    db = (tmp_path / "data" / "test.db").read_bytes().decode(errors="ignore")
    return db + store.get_job(ctx.job_id).model_dump_json() + json.dumps([a.model_dump() for a in
                                                                         store.activity(ctx.job_id)])


def test_court_records_saves_scored_opinions(tool_ctx, research_sources, tmp_path):
    research_sources({"courtlistener.search_opinions": [hit(12, "2018-01-02"), hit(11, "2021-05-01"),
                                                        hit(11, "2021-05-01")],
                      "courtlistener.opinion_passages": [FULL]})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    found = tool_ctx.findings()
    assert [f.url for f in found] == [hit(11, "")["url"], hit(12, "")["url"]]  # newest first, duplicate once
    first = found[0]
    assert (first.kind, first.collector, first.source_type, first.confidence) == (
        "court_opinion", "court_records", "courtlistener_opinion", "low")
    assert first.facts == {"case_name": "Doe v. Example Co. 11", "court": "District Court, N.D. Example",
                           "date_filed": "2021-05-01", "passages": [FULL],
                           "match": {"score": 40, "level": "medium",
                                     "reasons": ["full name in the opinion text", "field word near the name"]}}
    assert reply == {"skipped_same_name": 0, "opinions": [
        {"ref": f.finding_id, "case_name": f.facts["case_name"], "court": f.facts["court"],
         "date_filed": f.facts["date_filed"], "score": 40, "level": "medium", "passage": FULL} for f in found]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert rows(tool_ctx) == [("court_records", "done", "3 found, 2 kept, 0 same-name skipped")]
    assert SNIPPET not in stored(tool_ctx, tmp_path)  # gotcha 15


def test_evidence_url_and_home_state_score_high(tool_ctx, research_sources):
    url = hit(5, "")["url"]
    tool_ctx.expert = EXPERT.model_copy(update={"evidence": [
        Evidence(source="courtlistener", url=url + "  ", title="Doe v. Example Co. 5")]})
    research_sources({"courtlistener.search_opinions": [hit(5, "2015-03-03", court="Court of Appeals of Ohio")],
                      "courtlistener.opinion_passages": [FULL]})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    [found] = tool_ctx.findings()
    assert (found.url, found.confidence) == (url, "high")
    assert found.facts["match"] == {"score": 100, "level": "high", "reasons": [
        "opinion link is in the candidate's evidence", "full name in the opinion text",
        "field word near the name", "court in the expert's state"]}
    assert [(o["score"], o["level"]) for o in reply["opinions"]] == [(100, "high")]


def test_surname_only_is_never_kept(tool_ctx, research_sources):
    """Gotcha 13: an opinion giving only "Dr. Rivera" (or no text at all) is never kept, even with
    the field word and a court in the expert's state."""
    research_sources({
        "courtlistener.search_opinions": [hit(1, "2020-01-01", court="Supreme Court of Ohio"),
                                          hit(3, "2018-01-01")],
        "courtlistener.opinion_passages": passages_by_id({1: SURNAME, 3: None})})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    assert tool_ctx.findings() == []
    assert reply == {"opinions": [], "skipped_same_name": 2}
    assert gaps(tool_ctx) == [("court_records", "2 same-name court opinions skipped: not tied to this person")]


def test_full_name_only_is_kept_low_with_a_verify_note(tool_ctx, research_sources):
    """Full name, nothing else (25) and full name + home state (35): kept as level "low"."""
    research_sources({
        "courtlistener.search_opinions": [hit(1, "2020-01-01"), hit(2, "2019-01-01", court="Supreme Court of Ohio")],
        "courtlistener.opinion_passages": [NO_FIELD]})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    found = tool_ctx.findings()
    assert [(f.facts["match"]["score"], f.facts["match"]["level"], f.confidence, f.note) for f in found] == [
        (25, "low", "low", "full name only; verify it is the same person"),
        (35, "low", "low", "full name only; verify it is the same person")]
    assert [(o["score"], o["level"]) for o in reply["opinions"]] == [(25, "low"), (35, "low")]
    assert reply["skipped_same_name"] == 0 and gaps(tool_ctx) == []


def test_court_in_state_needs_the_whole_state_name():
    from app.tools.courtlistener import court_in_state

    assert court_in_state("District Court, N.D. Ohio", "OH")
    assert not court_in_state("Supreme Court of Appeals of West Virginia", "VA")
    assert court_in_state("Supreme Court of Virginia", "VA")
    assert not court_in_state("Supreme Court of Arkansas", "KS")
    assert not court_in_state("Court of Appeals for the Sixth Circuit", "OH")
    assert not court_in_state("Supreme Court of Ohio", None)


def test_opinion_text_not_fetched_is_a_gap(tool_ctx, research_sources):
    research_sources({"courtlistener.search_opinions": [hit(1, "2020-01-01")],
                      "courtlistener.opinion_passages": RuntimeError("down")})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    assert reply["skipped_same_name"] == 1
    assert ("court_records", "opinion text not fetched for 1 opinions") in gaps(tool_ctx)


def test_no_opinions_is_a_gap(tool_ctx, research_sources):
    research_sources({"courtlistener.search_opinions": []})
    assert asyncio.run(run_tool(tool_ctx, "court_records")) == {"opinions": [], "skipped_same_name": 0}
    assert gaps(tool_ctx) == [("court_records", "no court opinions name this person as an expert")]


def test_source_failing_is_tool_failed(tool_ctx, research_sources):
    research_sources({"courtlistener.search_opinions": RuntimeError("CourtListener HTTP 500")})
    assert asyncio.run(run_tool(tool_ctx, "court_records")) == {"error": TOOL_FAILED}
    assert rows(tool_ctx) == [("court_records", "failed", "RuntimeError")]
    assert gaps(tool_ctx) == [("court_records", "Court records failed (RuntimeError)")]


def test_no_token_is_skipped(tool_ctx, sources, monkeypatch):
    """Gotcha 11: the real source refuses before any request (fake HTTP routes installed anyway)."""
    _, seen = sources()
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    for tool_name, args in (("court_records", {}), ("court_case", {"case_name": "Doe v. Example Co."})):
        reply = asyncio.run(run_tool(tool_ctx, tool_name, **args))
        assert reply == {"error": "source not available: COURTLISTENER_TOKEN is not set"}
    assert [r[1] for r in rows(tool_ctx)] == ["skipped", "skipped"] and seen == []
    assert gaps(tool_ctx) == [("court_records", "COURTLISTENER_TOKEN is not set"),
                              ("court_case", "COURTLISTENER_TOKEN is not set")]


def test_court_case_found(tool_ctx, research_sources, tmp_path):
    calls = research_sources({"courtlistener.find_case": lambda case_name, name, court, year: {
        "url": "https://www.courtlistener.com/opinion/11/doe-v-example/", "case_name": "Doe v. Example Co.",
        "court": "District Court, N.D. Example", "date_filed": "2019-04-02", "docket_number": "1:19-cv-0001",
        "passages": [f"{SNIPPET} the plaintiff's expert, Alex J. Rivera, testified that"]}})
    reply = asyncio.run(run_tool(tool_ctx, "court_case", case_name="Doe v Example", court="N.D. Example",
                                 year="2019"))
    assert calls == [("courtlistener.find_case", ("Doe v Example", "Alex Rivera", "N.D. Example", 2019), {})]
    [found] = tool_ctx.findings()
    assert (found.kind, found.url, found.confidence, found.title) == (
        "case_check", "https://www.courtlistener.com/opinion/11/doe-v-example/", "high", "Doe v. Example Co.")
    assert found.facts["status"] == "found" and found.facts["court_record"]["docket_number"] == "1:19-cv-0001"
    assert reply["found"] is True and reply["ref"] == found.finding_id and SNIPPET in reply["passages"][0]
    assert rows(tool_ctx) == [("court_case", "done", "found")]
    assert SNIPPET not in stored(tool_ctx, tmp_path)  # snippet passages: reply only (gotcha 15)


def test_court_case_not_found_saves_nothing(tool_ctx, research_sources):
    research_sources()
    assert asyncio.run(run_tool(tool_ctx, "court_case", case_name="Invented v. Nobody")) == {"found": False}
    assert tool_ctx.findings() == [] and gaps(tool_ctx) == []
    assert rows(tool_ctx) == [("court_case", "done", "not found")]


def test_caps(tool_ctx, research_sources):
    research_sources()
    assert "error" not in asyncio.run(run_tool(tool_ctx, "court_records"))
    assert asyncio.run(run_tool(tool_ctx, "court_records")) == {"error": LIMIT_REACHED}
    replies = [asyncio.run(run_tool(tool_ctx, "court_case", case_name="Doe v. Example Co.")) for _ in range(7)]
    assert all(r["found"] for r in replies[:6]) and replies[6] == {"error": LIMIT_REACHED}
    assert len(tool_ctx.findings("case_check")) == 1  # same kind + URL: one finding
    assert [(r[0], r[1], r[2]) for r in rows(tool_ctx) if r[1] == "refused"] == [
        ("court_records", "refused", "cap used up"), ("court_case", "refused", "cap used up")]


def test_court_records_runs_through_the_main_agent(tool_ctx, research_sources, adk_model):
    research_sources()
    adk_model.script = [call("court_records"), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done"
    assert [(a.tool, a.label, a.status) for a in job.activity] == [("court_records", "Court records", "done")]
    assert {f.kind for f in tool_ctx.findings()} == {"court_opinion"}
    assert "court_case" in adk_model.seen[0].tools_dict
