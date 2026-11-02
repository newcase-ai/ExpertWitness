"""Tool 1: court records (DESIGN 4B.9): federal dockets (RECAP) with fake CourtListener functions and a
fake AI same-person check. Synthetic people only."""

import asyncio
import json

from app import jobs, llm, store
from app.evidence import LIMIT_REACHED, TOOL_FAILED
from app.models import Evidence
from app.profile import PER_KIND, known_so_far
from app.sources import SourceFailed
from app.sources import courtlistener as courtlistener_source
from app.tools import RESULT_MAX
from app.tools.courtlistener import AI_LOW_NOTE, LOW_NOTE, NONE_GAP, court_in_state
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    call,
    cl_docket,
    research_sources_fixture,
    run_tool,
    sources_fixture,
    tool_ctx_fixture,
)

SNIPPET = "SYNTHETIC-CL-SNIPPET"  # marker: search passages must never reach the database
FULL = f"{SNIPPET} the claim was reviewed by Dr. Alex J. Rivera, a toxicologist"  # full name + field word
SURNAME = f"{SNIPPET} the claim was reviewed by Dr. Rivera, a toxicologist"
NO_FIELD = f"{SNIPPET} a letter from Alex Rivera was filed"
AI_DOWN = llm.ModelError("model call failed (RuntimeError)")
AI_DOWN_GAP = ("court_records", "AI same-person check failed (model call failed (RuntimeError)); code score used")


def ai(*verdicts):
    """A fake call_model for the same-person check: (score, role, reason) for C1, C2, ...; the
    prompts it got are in `.prompts`."""

    def reply(instructions, input, schema=None, web_search=False, output_type=None):
        reply.prompts.append(input)
        return llm.ModelResult(data={"cases": [{"id": f"C{i}", "same_person": s, "role": r, "reason": why}
                                               for i, (s, r, why) in enumerate(verdicts, 1)]})

    reply.prompts = []
    return reply


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def stored(ctx, tmp_path) -> str:
    db = (tmp_path / "data" / "test.db").read_bytes().decode(errors="ignore")
    return db + store.get_job(ctx.job_id).model_dump_json() + json.dumps([a.model_dump() for a in
                                                                         store.activity(ctx.job_id)])


def test_ai_keeps_the_same_person_and_skips_a_namesake(tool_ctx, research_sources, tmp_path):
    """The AI compares each case with what we know so far; a namesake (score < 25) is not kept."""
    tool_ctx.add_finding("web_testimony", "SYNTHETIC expert profile", "https://example.org/rivera",
                         facts={"facts": ["SYNTHETIC: reviews disability claims"]})
    check = ai((85, "consultant or reviewer", "SYNTHETIC same field and kind of case"),
               (10, "party", "SYNTHETIC a police officer, not a toxicologist"))
    research_sources({"courtlistener.search_dockets": [cl_docket(12, "2018-01-02", passage=FULL),
                                                       cl_docket(11, "2021-05-01", passage=FULL),
                                                       cl_docket(11, "2021-05-01", passage=FULL)],
                      "llm.call_model": check})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    [prompt] = check.prompts  # one AI call for all cases
    assert "Field: toxicology" in prompt and "Location: Ohio, US" in prompt
    assert "- web_testimony: SYNTHETIC expert profile; SYNTHETIC: reviews disability claims" in prompt
    assert "C1: Doe v. Example Co. 11 | District Court, N.D. Example | filed 2021-05-01" in prompt
    assert "- docket 1:11-cv-0001; nature of suit: 791 ERISA" in prompt
    assert f"- Exhibit 2, Appeal Decision Letter: {FULL}" in prompt and "C2: Doe v. Example Co. 12" in prompt
    [found] = tool_ctx.findings("court_docket")
    url = "https://www.courtlistener.com/docket/11/doe-v-example/"
    assert (found.url, found.collector, found.source_type, found.confidence, found.note) == (
        url, "court_records", "courtlistener_docket", "high", None)
    assert found.facts == {
        "case_name": "Doe v. Example Co. 11", "court": "District Court, N.D. Example",
        "docket_number": "1:11-cv-0001", "date_filed": "2021-05-01", "nature_of_suit": "791 ERISA", "named_as": [],
        "documents": [{"description": "Exhibit 2, Appeal Decision Letter", "date_filed": "2021-05-01",
                       "url": "https://www.courtlistener.com/docket/11/12/2/doe-v-example/"}],
        "match": {"score": 85, "level": "high", "role": "consultant or reviewer",
                  "reasons": ["SYNTHETIC same field and kind of case"], "checked_by": "ai",
                  "context": "no profile yet, 1 findings"}}
    assert reply == {"skipped_same_name": 1, "files": [], "cases": [
        {"ref": found.finding_id, "case_name": "Doe v. Example Co. 11", "court": "District Court, N.D. Example",
         "docket_number": "1:11-cv-0001", "date_filed": "2021-05-01", "score": 85, "level": "high",
         "role": "consultant or reviewer", "why": "SYNTHETIC same field and kind of case", "passage": FULL}]}
    assert len(json.dumps(reply)) <= RESULT_MAX
    assert rows(tool_ctx) == [("court_records", "done", ("3 cases found, 1 kept, 1 same-name skipped"
                                                         " · checked against no profile yet, 1 findings"))]
    assert gaps(tool_ctx) == [("court_records", "1 same-name court cases skipped: not tied to this person")]
    assert SNIPPET not in stored(tool_ctx, tmp_path)  # gotcha 15: passages go to the AI and the reply only


def test_ai_unsure_is_kept_low_with_a_verify_note(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": [cl_docket(1, "2020-01-01", passage=NO_FIELD)],
                      "llm.call_model": ai((30, "unclear", "SYNTHETIC nothing ties it to the expert"))})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    [found] = tool_ctx.findings()
    assert (found.confidence, found.note, found.facts["match"]["level"]) == ("low", AI_LOW_NOTE, "low")
    assert (reply["cases"][0]["role"], reply["skipped_same_name"]) == ("unclear", 0)


def test_named_as_a_party_goes_to_the_ai(tool_ctx, research_sources):
    """No filing passage, but the docket lists the expert as a party: the AI decides (a lawsuit
    against the expert matters)."""
    check = ai((60, "party", "SYNTHETIC same name and state; he is the defendant"))
    research_sources({"courtlistener.search_dockets": [cl_docket(3, "2022-01-01", passage=None, named_as=["party"])],
                      "llm.call_model": check})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    assert "- Alex Rivera is named as a party on the docket" in check.prompts[0]
    assert [(c["level"], c["role"]) for c in reply["cases"]] == [("medium", "party")]
    assert tool_ctx.findings()[0].facts["named_as"] == ["party"]


def test_known_so_far_caps_each_kind(tool_ctx):
    for n in range(PER_KIND + 3):
        tool_ctx.add_finding("site_hit", f"SYNTHETIC hit {n}", f"https://example.org/{n}")
    text = known_so_far(tool_ctx)
    assert text.count("- site_hit:") == PER_KIND and "Affiliations: Example State University" in text
    assert "- Faculty page" in text  # the candidate's evidence from the search


def test_ai_down_falls_back_to_the_code_score(tool_ctx, research_sources):
    url = cl_docket(5, "")["url"]
    tool_ctx.expert = EXPERT.model_copy(update={"evidence": [
        Evidence(source="courtlistener", url=url + "  ", title="Doe v. Example Co. 5")]})
    research_sources({"courtlistener.search_dockets": [cl_docket(5, "2015-03-03", court="District Court, N.D. Ohio",
                                                                 passage=FULL)],
                      "llm.call_model": AI_DOWN})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    [found] = tool_ctx.findings()
    assert (found.url, found.confidence) == (url, "high")
    assert found.facts["match"] == {"score": 100, "level": "high", "checked_by": "code", "reasons": [
        "case link is in the candidate's evidence", "full name in the filings",
        "field word near the name", "court in the expert's state"]}
    assert [(c["score"], c["level"]) for c in reply["cases"]] == [(100, "high")]
    assert gaps(tool_ctx) == [AI_DOWN_GAP]  # the model failed: the code score is used


def test_surname_only_is_never_kept(tool_ctx, research_sources):
    """Gotcha 13: a case giving only "Dr. Rivera" (or no passage at all) is never kept nor sent to the
    AI, even with the field word and a court in the expert's state (the default AI call would fail)."""
    research_sources({"courtlistener.search_dockets": [
        cl_docket(1, "2020-01-01", court="District Court, S.D. Ohio", passage=SURNAME),
        cl_docket(3, "2018-01-01", passage=None)]})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    assert tool_ctx.findings() == []
    assert reply == {"cases": [], "skipped_same_name": 2, "files": []}
    assert gaps(tool_ctx) == [("court_records", "2 same-name court cases skipped: not tied to this person")]


def test_ai_down_full_name_only_is_kept_low_with_a_verify_note(tool_ctx, research_sources):
    """AI down, code score: full name, nothing else (25) and full name + home state (35): kept "low"."""
    research_sources({"courtlistener.search_dockets": [
        cl_docket(1, "2020-01-01", passage=NO_FIELD),
        cl_docket(2, "2019-01-01", court="District Court, S.D. Ohio", passage=NO_FIELD)],
        "llm.call_model": AI_DOWN})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    found = tool_ctx.findings()
    assert [(f.facts["match"]["score"], f.facts["match"]["level"], f.confidence, f.note) for f in found] == [
        (25, "low", "low", LOW_NOTE), (35, "low", "low", LOW_NOTE)]
    assert [(c["score"], c["level"]) for c in reply["cases"]] == [(25, "low"), (35, "low")]
    assert reply["skipped_same_name"] == 0 and gaps(tool_ctx) == [AI_DOWN_GAP]


def test_court_in_state_needs_the_whole_state_name():
    assert court_in_state("District Court, N.D. Ohio", "OH")
    assert not court_in_state("Supreme Court of Appeals of West Virginia", "VA")
    assert court_in_state("Supreme Court of Virginia", "VA")
    assert not court_in_state("Supreme Court of Arkansas", "KS")
    assert not court_in_state("Court of Appeals for the Sixth Circuit", "OH")
    assert not court_in_state("Supreme Court of Ohio", None)


def test_no_cases_is_a_gap(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": []})
    assert asyncio.run(run_tool(tool_ctx, "court_records")) == {"cases": [], "skipped_same_name": 0, "files": []}
    assert gaps(tool_ctx) == [("court_records", NONE_GAP)]


def test_source_failing_is_tool_failed(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": RuntimeError("CourtListener HTTP 500")})
    assert asyncio.run(run_tool(tool_ctx, "court_records")) == {"error": TOOL_FAILED}
    assert rows(tool_ctx) == [("court_records", "failed", "RuntimeError")]
    assert gaps(tool_ctx) == [("court_records", "Court records failed (RuntimeError)")]


def test_a_source_failure_tells_the_agent_and_the_log_what_happened(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": SourceFailed(courtlistener_source.TIMED_OUT)})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    assert reply == {"error": f"source call failed: {courtlistener_source.TIMED_OUT}"}
    assert rows(tool_ctx) == [("court_records", "failed", courtlistener_source.TIMED_OUT)]
    assert gaps(tool_ctx) == [("court_records", f"Court records failed: {courtlistener_source.TIMED_OUT}")]


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


def found_case(case_name, name, court, year):
    return cl_docket(11, "2019-04-02", passage=FULL) | {"case_name": "Doe v. Example Co."}


def test_court_case_found(tool_ctx, research_sources, tmp_path):
    check = ai((90, "expert witness", "SYNTHETIC same specialty"))
    calls = research_sources({"courtlistener.find_case": found_case, "llm.call_model": check})
    reply = asyncio.run(run_tool(tool_ctx, "court_case", case_name="Doe v Example", court="N.D. Example",
                                 year="2019"))
    assert calls[0] == ("courtlistener.find_case", ("Doe v Example", "Alex Rivera", "N.D. Example", 2019), {})
    assert "C1: Doe v. Example Co. | District Court, N.D. Example | filed 2019-04-02" in check.prompts[0]
    [found] = tool_ctx.findings()
    assert (found.kind, found.url, found.confidence, found.title, found.note, found.source_type) == (
        "case_check", "https://www.courtlistener.com/docket/11/doe-v-example/", "high", "Doe v. Example Co.",
        "found in federal court records (CourtListener RECAP)", "courtlistener_docket")
    assert found.facts["status"] == "found" and found.facts["court_record"]["docket_number"] == "1:11-cv-0001"
    assert found.facts["match"]["role"] == "expert witness" and found.facts["match"]["checked_by"] == "ai"
    assert found.facts["documents"][0]["description"] == "Exhibit 2, Appeal Decision Letter"
    assert reply["found"] is True and reply["ref"] == found.finding_id and reply["passages"] == [FULL]
    assert reply["same_person"] == {"score": 90, "level": "high", "role": "expert witness",
                                    "why": "SYNTHETIC same specialty"}
    assert rows(tool_ctx) == [("court_case", "done", "found · checked against no profile yet, 0 findings")]
    assert SNIPPET not in stored(tool_ctx, tmp_path)  # search passages: AI and reply only (gotcha 15)


def test_court_case_about_a_namesake_is_not_saved(tool_ctx, research_sources):
    research_sources({"courtlistener.find_case": found_case,
                      "llm.call_model": ai((5, "party", "SYNTHETIC the defendant, a police officer"))})
    reply = asyncio.run(run_tool(tool_ctx, "court_case", case_name="Doe v Example"))
    assert tool_ctx.findings() == [] and reply["found"] is True and reply["ref"] is None
    assert reply["same_person"] == {"score": 5, "level": "low", "role": "party",
                                    "why": "SYNTHETIC the defendant, a police officer"}
    assert rows(tool_ctx) == [("court_case", "done", "found, another person · checked against no profile yet, 0 findings")]


def test_court_case_does_not_check_a_saved_case_again(tool_ctx, research_sources):
    """No circular re-check: a case court_records saved is answered from its finding, by name without
    a search, or by docket URL after the search, never with a second AI check."""
    check = ai((85, "consultant or reviewer", "SYNTHETIC same field"), (80, "consultant or reviewer", "SYNTHETIC"))
    calls = research_sources({"courtlistener.find_case": found_case, "llm.call_model": check})
    asyncio.run(run_tool(tool_ctx, "court_records"))
    [saved] = [f for f in tool_ctx.findings("court_docket") if f.facts["case_name"] == "Doe v. Example Co. 11"]
    by_name = asyncio.run(run_tool(tool_ctx, "court_case", case_name="doe v example co 11"))
    assert by_name == {"found": True, "ref": saved.finding_id, "already_found": True,
                       "case_name": "Doe v. Example Co. 11", "court": "District Court, N.D. Example",
                       "date_filed": "2021-05-01", "docket_number": "1:11-cv-0001",
                       "same_person": {"score": 85, "level": "high", "role": "consultant or reviewer",
                                       "why": "SYNTHETIC same field"}}
    by_url = asyncio.run(run_tool(tool_ctx, "court_case", case_name="Doe v. Example"))  # another name, same docket
    assert by_url["already_found"] and by_url["ref"] == saved.finding_id
    assert [c[0] for c in calls].count("courtlistener.find_case") == 1 and len(check.prompts) == 1
    assert tool_ctx.findings("case_check") == []
    assert rows(tool_ctx)[-1] == ("court_case", "done", f"already found ({saved.finding_id}); not checked again")


def test_court_case_not_found_saves_nothing(tool_ctx, research_sources):
    research_sources()
    assert asyncio.run(run_tool(tool_ctx, "court_case", case_name="Invented v. Nobody")) == {"found": False}
    assert tool_ctx.findings() == [] and gaps(tool_ctx) == []
    assert rows(tool_ctx) == [("court_case", "done", "not found")]


def test_caps(tool_ctx, research_sources):
    research_sources({"llm.call_model": AI_DOWN})
    assert "error" not in asyncio.run(run_tool(tool_ctx, "court_records"))
    assert asyncio.run(run_tool(tool_ctx, "court_records")) == {"error": LIMIT_REACHED}
    replies = [asyncio.run(run_tool(tool_ctx, "court_case", case_name="Doe v. Example Co.")) for _ in range(7)]
    assert all(r["found"] for r in replies[:6]) and replies[6] == {"error": LIMIT_REACHED}
    assert all(r["already_found"] for r in replies[:6])  # court_records saved docket 11: never checked again
    assert tool_ctx.findings("case_check") == []
    assert [(r[0], r[1], r[2]) for r in rows(tool_ctx) if r[1] == "refused"] == [
        ("court_records", "refused", "cap used up"), ("court_case", "refused", "cap used up")]


def test_court_records_runs_through_the_main_agent(tool_ctx, research_sources, adk_model):
    research_sources({"llm.call_model": ai((80, "expert witness", "SYNTHETIC"), (80, "expert witness", "SYNTHETIC"))})
    adk_model.script = [call("court_records"), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done"
    assert [(a.tool, a.label, a.status) for a in job.activity] == [("court_records", "Court records", "done")]
    assert {f.kind for f in tool_ctx.findings()} == {"court_docket"}
    assert "court_case" in adk_model.seen[0].tools_dict
