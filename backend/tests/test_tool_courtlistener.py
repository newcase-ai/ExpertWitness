"""Tool 1: court records (DESIGN 4B.9): court_search, court_filings, court_file and court_verdict with
fake CourtListener functions and fake PDF text (a "PDF" here is synthetic page texts joined by form
feeds). Synthetic people only (gotcha 8)."""

import asyncio
import json
from pathlib import Path

import pytest

from app import jobs, llm, memory, pdf_text, purchases, store, trace
from app.profile import PER_KIND, finding_line, known_so_far
from app.research_agent import first_message
from app.sources import SourceFailed, ramp
from app.sources import courtlistener as courtlistener_source
from app.tools import RESULT_MAX, secret
from app.tools import courtlistener as court
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    RAMP_PAN,
    call,
    cl_docket,
    cl_filing,
    cl_page,
    ramp_http_fixture,
    ramp_keys_fixture,
    research_sources_fixture,
    run_tool,
    sources_fixture,
    tool_ctx_fixture,
)

SNIPPET = "SYNTHETIC-CL-SNIPPET"  # marker: search passages must never reach the database
OWN = "Alex J. Rivera, PhD. My opinions in this matter are as follows."
OTHER = "The court sets the schedule. Counsel shall appear."
WHY = "SYNTHETIC same specialty and kind of case"


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def stored(ctx, tmp_path) -> str:
    db = (tmp_path / "data" / "test.db").read_bytes().decode(errors="ignore")
    return db + store.get_job(ctx.job_id).model_dump_json() + json.dumps([a.model_dump() for a in
                                                                         store.activity(ctx.job_id)])


def search(ctx, **args):
    return asyncio.run(run_tool(ctx, "court_search", **args))


def get_file(ctx, document_id):
    return asyncio.run(run_tool(ctx, "court_file", document_id=document_id))


def verdict(ctx, docket_id=11, same_person=85, role="expert witness", why=WHY):
    return asyncio.run(run_tool(ctx, "court_verdict", docket_id=docket_id, same_person=same_person, role=role,
                                why=why))


def pdf(*pages: str) -> bytes:
    return b"%PDF-" + "\f".join(pages).encode()


# --- court_search ---------------------------------------------------------------------------------


def test_search_shows_every_hit_and_saves_nothing(tool_ctx, research_sources, tmp_path):
    """No code drop or score (user, 2026-11-02): a surname-only passage and a case with no passage
    both go to the agent; nothing is saved, and passages reach the reply only (gotcha 15)."""
    surname = cl_filing(1, passage=f"{SNIPPET} reviewed by Dr. Rivera")
    sealed = cl_filing(2, n=5, passage="", available=False, description="Sealed exhibit")
    calls = research_sources({"courtlistener.search_dockets": cl_page([
        cl_docket(1, "2020-01-01", documents=[surname]), cl_docket(2, "2019-01-01", named_as=["party"], documents=[sealed]),
        cl_docket(3, "2018-01-01") | {"docket_id": None}])})
    reply = search(tool_ctx)
    assert calls[0] == ("courtlistener.search_dockets", ("Alex Rivera", "", ""), {})
    case = {"court": "District Court, N.D. Example", "nature_of_suit": "791 ERISA"}
    assert reply == {"total": 3, "more": False, "cases": [
        case | {"docket_id": 1, "case_name": "Doe v. Example Co. 1", "docket_number": "1:1-cv-0001",
                "date_filed": "2020-01-01", "filings": [{"document_id": 102, "title": "Exhibit 2, Appeal Decision Letter",
                                                         "pages": 3, "available": True,
                                                         "passage": f"{SNIPPET} reviewed by Dr. Rivera"}]},
        case | {"docket_id": 2, "case_name": "Doe v. Example Co. 2", "docket_number": "1:2-cv-0001",
                "date_filed": "2019-01-01", "named_as": ["party"],
                "filings": [{"document_id": 205, "title": "Sealed exhibit", "pages": 3, "available": False}]}]}
    assert tool_ctx.findings() == [] and gaps(tool_ctx) == []
    assert set(tool_ctx.run.cases) == {1, 2} and tool_ctx.run.filings[205]["docket_id"] == 2
    assert rows(tool_ctx) == [("court_search", "done", "2 of 3 cases shown")]
    assert SNIPPET not in stored(tool_ctx, tmp_path)


def test_search_words_and_case_name(tool_ctx, research_sources):
    calls = research_sources()
    search(tool_ctx, words='"motion to exclude"', case_name="Doe v. Example Co.")
    assert calls[-1] == ("courtlistener.search_dockets", ("Alex Rivera", '"motion to exclude"', "Doe v. Example Co."), {})
    assert search(tool_ctx, words="x" * (court.WORDS_MAX + 1)) == {"error": court.TOO_LONG}
    assert search(tool_ctx, case_name="x" * (court.CASE_NAME_MAX + 1)) == {"error": court.TOO_LONG}


def test_no_cases_is_a_gap_for_the_plain_search_only(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": cl_page([])})
    empty = {"total": 0, "cases": [], "more": False}
    assert search(tool_ctx, words="Medtronic") == empty and gaps(tool_ctx) == []
    assert search(tool_ctx) == empty
    assert gaps(tool_ctx) == [("court_search", court.NONE_GAP)]


def test_search_reply_fits_and_more_shows_the_rest(tool_ctx, research_sources):
    """20 cases x 3 long filings: a reply stays under RESULT_MAX (a longer one is dropped whole,
    gotcha 59); more=true shows the rest of the page without a new CourtListener call."""
    def docs(did):
        return [cl_filing(did, n, passage="Alex Rivera " + "x" * 400, description="y" * 200) for n in (1, 2, 3)]

    calls = research_sources({"courtlistener.search_dockets": cl_page(
        [cl_docket(d, "2021-01-01", documents=docs(d)) | {"case_name": "z" * 200} for d in range(1, 21)])})
    shown = []
    for n in range(5):
        reply = search(tool_ctx, more=n > 0)
        assert len(json.dumps(reply)) <= RESULT_MAX and reply["total"] == 20
        shown += [c["docket_id"] for c in reply["cases"]]
        if not reply["more"]:
            break
    assert shown == list(range(1, 21)) and len(calls) == 1


def test_more_reads_the_next_page(tool_ctx, research_sources):
    pages = {None: cl_page([cl_docket(1, "2021-01-01")], total=2, next_url="NEXT"),
             "NEXT": cl_page([cl_docket(2, "2020-01-01")], total=2)}
    calls = research_sources({"courtlistener.search_dockets": lambda *a, next_url=None: pages[next_url]})
    assert search(tool_ctx, more=True) == {"error": court.NO_SEARCH}  # nothing to continue yet
    first = search(tool_ctx)
    assert ([c["docket_id"] for c in first["cases"]], first["more"]) == ([1], True)
    second = search(tool_ctx, more=True)
    assert (second["total"], [c["docket_id"] for c in second["cases"]], second["more"]) == (2, [2], False)
    assert calls[-1] == ("courtlistener.search_dockets", ("Alex Rivera",), {"next_url": "NEXT"})
    assert search(tool_ctx, more=True) == {"total": 2, "cases": [], "more": False}
    assert set(tool_ctx.run.cases) == {1, 2}


def test_source_failure_tells_the_agent_what_happened(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": SourceFailed(courtlistener_source.TIMED_OUT)})
    assert search(tool_ctx) == {"error": f"source call failed: {courtlistener_source.TIMED_OUT}"}
    assert rows(tool_ctx) == [("court_search", "failed", courtlistener_source.TIMED_OUT)]
    assert gaps(tool_ctx) == [("court_search", f"Court search failed: {courtlistener_source.TIMED_OUT}")]


def test_no_token_is_skipped(tool_ctx, sources, monkeypatch):
    """Gotcha 11: the real source refuses before any request (fake HTTP routes installed anyway)."""
    _, seen = sources()
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    assert search(tool_ctx) == {"error": "source not available: COURTLISTENER_TOKEN is not set"}
    assert rows(tool_ctx) == [("court_search", "skipped", "COURTLISTENER_TOKEN is not set")] and seen == []


# --- court_filings --------------------------------------------------------------------------------


def test_court_filings_lists_a_known_case(tool_ctx, research_sources):
    docs = [{k: v for k, v in cl_filing(11, n, description=f"Filing {n}").items() if k != "passage"}
            | {"attachment_number": 1 if n == 3 else None} for n in (1, 3)]
    calls = research_sources({"courtlistener.docket_documents": (docs, True)})
    assert asyncio.run(run_tool(tool_ctx, "court_filings", docket_id=11)) == {"error": court.UNKNOWN_CASE}
    search(tool_ctx)
    reply = asyncio.run(run_tool(tool_ctx, "court_filings", docket_id=11))
    assert reply == {"docket_id": 11, "more_not_listed": True, "filings": [
        {"document_id": 1101, "title": "Filing 1", "number": "1", "date_filed": "2021-05-01", "pages": 3},
        {"document_id": 1103, "title": "Filing 3", "number": "3-1", "date_filed": "2021-05-01", "pages": 3}]}
    assert ("courtlistener.docket_documents", (11,), {}) in calls
    assert gaps(tool_ctx) == [("court_filings", "court filings of Doe v. Example Co. 11 past the first 2 not listed")]
    assert tool_ctx.run.filings[1103]["docket_id"] == 11 and tool_ctx.run.filings[1102]["passage"]


# --- court_file -----------------------------------------------------------------------------------


@pytest.fixture(name="world")
def world_fixture(research_sources, monkeypatch):
    """setup(files, ...) installs fake PDF text, downloads (a missing file: storage down) and OCR,
    and a search hit for docket 11 with `docs`; returns the downloaded paths."""

    def page_texts(path):
        data = Path(path).read_bytes()
        if not data.startswith(b"%PDF-"):
            raise pdf_text.PdfUnreadable(pdf_text.UNREADABLE)
        return data[5:].decode().split("\f")

    monkeypatch.setattr(pdf_text, "page_texts", page_texts)
    monkeypatch.setattr(pdf_text, "mostly_empty", lambda pages: sum(not p.strip() for p in pages) * 2 > len(pages))
    monkeypatch.setattr(pdf_text, "split_text", lambda text: text.split("\f"))

    def setup(files, cl_text=None, ocr=None, ocr_finished=True, docs=None):
        downloads = []

        def download(filepath_local, dest):
            if filepath_local not in files:
                raise SourceFailed("SYNTHETIC storage down")
            downloads.append(filepath_local)
            Path(dest).write_bytes(files[filepath_local])
            return len(files[filepath_local])

        monkeypatch.setattr(pdf_text, "ocr_pages", lambda path, stop_at=None: (ocr or [], ocr_finished))
        research_sources({"courtlistener.search_dockets": cl_page([cl_docket(11, "2021-05-01", documents=docs)]),
                          "courtlistener.download_document": download,
                          "courtlistener.document_text": lambda doc_id: (cl_text or {}).get(doc_id, "")})
        return downloads

    return setup


def test_court_file_downloads_and_saves_the_text(tool_ctx, world, tmp_path):
    data = pdf(OTHER, OWN, "Rivera, Alex testified")
    downloads = world({"recap/synthetic.11.2.pdf": data})
    search(tool_ctx)
    reply = get_file(tool_ctx, 1102)
    file_id = reply["file_id"]
    assert reply == {"file_id": file_id, "title": "Exhibit 2, Appeal Decision Letter", "pages": 3,
                     "text_source": "text layer", "name_on_pages": [2, 3]}
    row = memory.file(file_id)
    path = Path(row["path"])
    assert path.parent == tmp_path / "data" / "files" / tool_ctx.dossier_id and path.read_bytes() == data
    assert row["source_url"] == "https://www.courtlistener.com/docket/11/2/doe-v-example/"
    assert Path(row["text_path"]).read_text() == f"[Page 1]\n{OTHER}\n[Page 2]\n{OWN}\n[Page 3]\nRivera, Alex testified"
    assert tool_ctx.findings() == []  # the verdict saves the case, not the download
    assert list((path.parent / "tmp").iterdir()) == []
    again = get_file(tool_ctx, 1102)
    assert again["file_id"] == file_id and again["already_downloaded"] and downloads == ["recap/synthetic.11.2.pdf"]
    assert rows(tool_ctx)[1:] == [("court_file", "done", "3 pages, text layer, name on 2 pages"),
                                  ("court_file", "done", "already downloaded")]


def test_court_file_scans_use_courtlistener_ocr_then_local_ocr(tool_ctx, world):
    world({"recap/synthetic.11.1.pdf": pdf("", ""), "recap/synthetic.11.2.pdf": pdf("", "")},
          cl_text={1101: f"{OWN}\f{OTHER}"}, ocr=[OTHER, OWN], docs=[cl_filing(11, 1), cl_filing(11, 2)])
    search(tool_ctx)
    first, second = get_file(tool_ctx, 1101), get_file(tool_ctx, 1102)
    assert (first["text_source"], first["name_on_pages"]) == (court.COURTLISTENER_OCR, [1])
    assert (second["text_source"], second["name_on_pages"]) == (court.LOCAL_OCR, [2])


def test_court_file_refusals_keep_no_file(tool_ctx, world, tmp_path):
    world({"recap/synthetic.11.1.pdf": b"<html>SYNTHETIC</html>", "recap/synthetic.11.3.pdf": pdf("", "")},
          docs=[cl_filing(11, 1), cl_filing(11, 2, available=False), cl_filing(11, 3), cl_filing(11, 4)])
    search(tool_ctx)
    assert get_file(tool_ctx, 999) == {"error": court.UNKNOWN_FILING}
    assert get_file(tool_ctx, 1101) == {"error": pdf_text.UNREADABLE}
    assert get_file(tool_ctx, 1102) == {"error": court.NOT_AVAILABLE}
    assert get_file(tool_ctx, 1103) == {"error": court.NO_TEXT}  # a scan, no OCR text anywhere
    assert get_file(tool_ctx, 1104) == {"error": "source call failed: SYNTHETIC storage down"}
    assert memory.files(tool_ctx.dossier_id) == []
    assert list((tmp_path / "data" / "files" / tool_ctx.dossier_id / "tmp").iterdir()) == []


def test_court_file_ocr_stopped_by_the_time_limit(tool_ctx, world):
    world({"recap/synthetic.11.2.pdf": pdf("", "")}, ocr=["SYNTHETIC first page"], ocr_finished=False)
    search(tool_ctx)
    assert get_file(tool_ctx, 1102) == {"error": court.OCR_TIME}
    assert memory.files(tool_ctx.dossier_id) == []


# --- court_verdict --------------------------------------------------------------------------------


def test_verdict_saves_the_case_with_the_files_read(tool_ctx, world):
    world({"recap/synthetic.11.2.pdf": pdf(OWN)})
    search(tool_ctx)
    file_id = get_file(tool_ctx, 1102)["file_id"]
    assert verdict(tool_ctx, why=f"SYNTHETIC: {OWN}") == {"error": court.COPIED_WHY}  # copies the filing
    reply = verdict(tool_ctx)
    [found] = tool_ctx.findings()
    assert reply == {"ref": found.finding_id, "level": "high"}
    assert (found.kind, found.title, found.url, found.collector, found.source_type, found.confidence, found.note) == (
        "court_docket", "Doe v. Example Co. 11", "https://www.courtlistener.com/docket/11/doe-v-example/",
        "court_verdict", "courtlistener_docket", "high", None)
    assert found.facts == {
        "case_name": "Doe v. Example Co. 11", "court": "District Court, N.D. Example", "docket_number": "1:11-cv-0001",
        "date_filed": "2021-05-01", "nature_of_suit": "791 ERISA", "named_as": [],
        "files": [{"file_id": file_id, "title": "Exhibit 2, Appeal Decision Letter"}],
        "match": {"score": 85, "level": "high", "role": "expert witness", "reasons": [WHY], "checked_by": "agent"}}
    assert f"his role: expert witness (high match); {WHY}" in finding_line(found)  # what the profile AI reads
    assert rows(tool_ctx)[-1] == ("court_verdict", "done", "high, expert witness")


@pytest.mark.parametrize("score,level,confidence,note", [
    (150, "high", "high", None), (60, "medium", "low", None), (30, "low", "low", court.LOW_NOTE),
    (5, "ruled out", "low", court.RULED_OUT), (-7, "ruled out", "low", court.RULED_OUT)])
def test_verdict_levels(tool_ctx, research_sources, score, level, confidence, note):
    research_sources()
    search(tool_ctx)
    assert verdict(tool_ctx, same_person=score, role="party")["level"] == level
    [found] = tool_ctx.findings()
    assert (found.confidence, found.note, found.facts["match"]["score"]) == (confidence, note, max(0, min(100, score)))


def test_verdict_refusals_save_nothing(tool_ctx, research_sources, tmp_path):
    passage = f"{SNIPPET} the claim was reviewed by Dr. Alex J. Rivera, a toxicologist"
    research_sources({"courtlistener.search_dockets": cl_page([cl_docket(11, "2021-05-01",
                                                                 documents=[cl_filing(11, passage=passage)])])})
    assert verdict(tool_ctx) == {"error": court.UNKNOWN_CASE}  # no search yet
    search(tool_ctx)
    assert verdict(tool_ctx, role="witness") == {"error": court.BAD_ROLE}
    assert verdict(tool_ctx, why="  ") == {"error": court.NO_WHY}
    assert verdict(tool_ctx, why="He is: the claim was reviewed by Dr. Alex J. Rivera, a toxicologist") == {
        "error": court.COPIED_WHY}
    assert tool_ctx.findings() == [] and SNIPPET not in stored(tool_ctx, tmp_path)


def test_one_verdict_per_case(tool_ctx, research_sources):
    research_sources()
    search(tool_ctx)
    first = verdict(tool_ctx)
    again = verdict(tool_ctx, same_person=5, role="party", why="SYNTHETIC changed my mind")
    assert again == {"ref": first["ref"], "level": "high", "already_decided": True}
    assert len(tool_ctx.findings()) == 1


# --- court_research: the court-records helper ------------------------------------------------------


def research(ctx, adk_model, script, **args):
    """court_research through the wrapper; `script` is the helper agent's turns."""
    adk_model.script = script
    return asyncio.run(run_tool(ctx, "court_research", **args))


def test_court_research_runs_the_helper_and_reports_what_it_saved(tool_ctx, world, adk_model):
    world({"recap/synthetic.11.2.pdf": pdf(OTHER, OWN)})
    reply = research(tool_ctx, adk_model, [
        call("court_search", case_name="Doe v. Example Co."), call("court_file", document_id=1102),
        call("court_verdict", docket_id=11, same_person=85, role="expert witness", why=WHY),
        "SYNTHETIC done", "SYNTHETIC report: Cases: Doe v. Example Co. 11. Leads: none."],
        focus="Doe v. Example Co.", context="SYNTHETIC CV: plaintiff's expert in Doe v. Example Co.")
    helper, reporter = adk_model.seen[0], adk_model.seen[-1]
    assert set(helper.tools_dict) == set(court.HELPER_TOOLS)
    prompt = helper.contents[0].parts[0].text
    assert "Name: Alex Rivera" in prompt and "The lead researcher asks: Doe v. Example Co." in prompt
    assert "What they know that may help: SYNTHETIC CV: plaintiff's expert" in prompt
    # the report is one more call with no tools, on the task and the helper's whole run
    work = reporter.contents[0].parts[0].text
    assert not reporter.tools_dict and len(adk_model.seen) == 5
    assert "The lead researcher asks: Doe v. Example Co." in work and "(finished, 4 model calls)" in work
    assert 'tool call court_verdict: {"docket_id": 11' in work and "tool result court_file:" in work
    assert "model: SYNTHETIC done" in work
    [found] = tool_ctx.findings()
    file_id = found.facts["files"][0]["file_id"]
    assert reply == {
        "cases": [{"ref": found.finding_id, "case_name": "Doe v. Example Co. 11", "court": "District Court, N.D. Example",
                   "docket_number": "1:11-cv-0001", "date_filed": "2021-05-01", "level": "high",
                   "role": "expert witness", "why": WHY, "file_ids": [file_id]}],
        "ruled_out": [], "unjudged": [], "report": "SYNTHETIC report: Cases: Doe v. Example Co. 11. Leads: none.",
        "files": [{"file_id": file_id, "title": "Exhibit 2, Appeal Decision Letter", "case": "Doe v. Example Co. 11",
                   "name_on_pages": [2]}]}
    assert [r[0] for r in rows(tool_ctx)] == ["court_research", "court_search", "court_file", "court_verdict"]
    assert rows(tool_ctx)[0] == ("court_research", "done", "1 cases, 0 ruled out, 1 files")


def test_court_research_lists_people_ruled_out(tool_ctx, research_sources, adk_model):
    research_sources()
    reply = research(tool_ctx, adk_model, [
        call("court_search"), call("court_verdict", docket_id=12, same_person=5, role="party", why="SYNTHETIC debtor"),
        "SYNTHETIC report"])
    assert (reply["cases"], reply["ruled_out"]) == ([], ["Doe v. Example Co. 12"])
    assert "find every federal case naming the expert" in adk_model.seen[0].contents[0].parts[0].text


def test_court_research_reports_after_the_step_limit(tool_ctx, research_sources, adk_model, monkeypatch):
    """A helper that runs out of model calls still gets its report: the report call is not counted."""
    monkeypatch.setattr(court, "HELPER_CALLS", 2)
    research_sources()
    reply = research(tool_ctx, adk_model, [
        call("court_search"), call("court_verdict", docket_id=12, same_person=5, role="party", why="SYNTHETIC debtor"),
        "SYNTHETIC report after the limit"])
    assert reply["report"] == "SYNTHETIC report after the limit" and reply["ruled_out"] == ["Doe v. Example Co. 12"]
    assert "(step_limit, 2 model calls)" in adk_model.seen[-1].contents[0].parts[0].text


def test_court_research_stopped_early_keeps_what_was_saved(tool_ctx, world, adk_model):
    world({"recap/synthetic.11.2.pdf": pdf(OWN)})
    reply = research(tool_ctx, adk_model, [call("court_search"), call("court_file", document_id=1102),
                                           RuntimeError("SYNTHETIC down")])
    assert reply["unjudged"] == ["Doe v. Example Co. 11"] and reply["files"][0]["case"] == "Doe v. Example Co. 11"
    assert reply["report"] == "the helper stopped early (model call failed (RuntimeError)); what it saved is listed"
    assert gaps(tool_ctx) == [("court_research", "court research stopped early (model call failed (RuntimeError))")]


def test_court_research_refuses_long_arguments(tool_ctx, adk_model):
    assert research(tool_ctx, adk_model, [], focus="x" * (court.FOCUS_MAX + 1))["error"].startswith("focus: at most")
    assert adk_model.seen == []


def test_one_profile_update_for_everything_the_helper_saved(tool_ctx, research_sources, adk_model, monkeypatch):
    """The helper's own tool calls do not update the profile; court_research's call does, once."""
    from app import profile
    updates = []

    async def update(dossier_id, expert, new, known):
        updates.append([f.kind for f in new])
        return store.get_profile(dossier_id)

    monkeypatch.setattr(profile, "AUTO_UPDATE", True)
    monkeypatch.setattr(profile, "update", update)
    research_sources()
    research(tool_ctx, adk_model, [
        call("court_search"), call("court_verdict", docket_id=11, same_person=85, role="expert witness", why=WHY),
        call("court_verdict", docket_id=12, same_person=5, role="party", why="SYNTHETIC debtor"), "SYNTHETIC report"])
    assert updates == [["court_docket", "court_docket"]]


def test_court_research_reply_fits():
    reply = court.fit_report({"cases": [{"case_name": "x" * 200, "why": "y" * 200}] * 30,
                              "ruled_out": ["z" * 60] * 20, "files": [{"title": "t" * 60}] * 30, "unjudged": [],
                              "report": "SYNTHETIC report " * 1000})
    assert len(json.dumps(reply)) <= RESULT_MAX and len(reply["cases"]) == 8 and reply["report"]


def test_main_agent_gets_court_research_not_the_helper_tools(tool_ctx, world, adk_model):
    """Through a real job: the main agent sees court_research only; a case the helper read but never
    judged is a gap at the end of the run, a judged one is not."""
    world({"recap/synthetic.11.2.pdf": pdf(OWN)})
    adk_model.script = [call("court_research"), call("court_search"), call("court_file", document_id=1102),
                        "SYNTHETIC report", "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    main = set(adk_model.seen[0].tools_dict)
    assert "court_research" in main and "ask_pdf" in main and not main & {"court_search", "court_filings",
                                                                          "court_file", "court_verdict"}
    assert "court_search" not in first_message(EXPERT, None) and "- court_research: Court research" in \
        first_message(EXPERT, None)
    assert store.get_job(tool_ctx.job_id).status == "done"
    assert ("main_agent", court.UNJUDGED_GAP.format("Doe v. Example Co. 11")) in gaps(tool_ctx)


def test_a_judged_case_is_no_gap(tool_ctx, world, adk_model):
    world({"recap/synthetic.11.2.pdf": pdf(OWN)})
    adk_model.script = [call("court_research"), call("court_search"), call("court_file", document_id=1102),
                        call("court_verdict", docket_id=11, same_person=5, role="party", why=WHY),
                        "SYNTHETIC report", "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    assert gaps(tool_ctx) == [] and [f.kind for f in tool_ctx.findings()] == ["court_docket"]


# --- shared ---------------------------------------------------------------------------------------


def test_known_so_far_caps_each_kind(tool_ctx):
    for n in range(PER_KIND + 3):
        tool_ctx.add_finding("site_hit", f"SYNTHETIC hit {n}", f"https://example.org/{n}")
    text = known_so_far(tool_ctx)
    assert text.count("- site_hit:") == PER_KIND and "Affiliations: Example State University" in text
    assert "- Faculty page" in text  # the candidate's evidence from the search


def test_own_ids_never_look_like_secrets(tool_ctx):
    """A random id can hold 12+ digits in a row (a card number to looks_secret); facts hold file_ids.
    Only ids with a hex letter are exempt: a card number after one of our prefixes is still caught."""
    assert not secret("file_1234567890123a56") and not secret("see fnd_a1b2c3d4e5f60718 for the case")
    assert secret("file_4111111111111111") and secret("fnd_4111111111111111 note")
    assert secret("fnd_a1b2c3d4e5f60718 4111 1111 1111 1111")  # a card number next to a real id
    ref = tool_ctx.add_finding("court_docket", "SYNTHETIC case", "https://www.courtlistener.com/docket/1/x/",
                               facts={"files": [{"file_id": "file_1234567890123a56", "title": "SYNTHETIC"}]})
    assert ref and tool_ctx.findings()[0].facts["files"][0]["file_id"] == "file_1234567890123a56"


def test_new_ids_are_never_all_digits(monkeypatch):
    """gotcha 60: an all-digit id looks like a card number; store.new_id draws again."""
    tokens = iter(["4111111111111111", "0123456789abcdef"])
    monkeypatch.setattr(store.secrets, "token_hex", lambda n: next(tokens))
    assert store.new_id("file") == "file_0123456789abcdef"


# --- pay_for_document (the payment service in app/purchases.py, a dry run in v1) -------------------

PAY_WHY = "SYNTHETIC-WHY the expert's own report"  # marker: the value judge must never see it


@pytest.fixture(name="judge")
def judge_fixture(monkeypatch):
    """A fake value judge: replies pop from `replies` (an Exception is raised) and every prompt is kept.
    Each call yields once, so parallel tool calls really interleave."""
    state = {"replies": [], "prompts": []}

    async def fake(instructions, input, schema=None, web_search=False, output_type=None):
        state["prompts"].append(input)
        await asyncio.sleep(0)
        reply = state["replies"].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return llm.ModelResult(data=reply)

    monkeypatch.setattr(llm, "call_model", fake)
    return state


def offered(ctx, *docs):
    """Filings court_search showed for docket 11 (so their ids work in pay_for_document)."""
    ctx.run.cases[11] = cl_docket(11, "2021-05-01")
    ctx.run.caps["pay_for_document"] = 10
    court.remember(ctx, 11, list(docs))


def not_public(n, pages=5, description="Exhibit 8, Expert report of Alex J. Rivera"):
    return cl_filing(11, n=n, available=False, pages=pages, description=description) | {
        "entry": "SYNTHETIC EXHIBIT re Motion for Summary Judgment filed by Defendant Example Co."}


async def paid(ctx, *document_ids, why=PAY_WHY):
    """pay_for_document for each id, in parallel, with the run's trace on."""
    token = trace.start(ctx.job_id)
    try:
        return await asyncio.gather(*[run_tool(ctx, "pay_for_document", document_id=d, why=why) for d in document_ids])
    finally:
        trace.stop(token)


def pay(ctx, document_id, why=PAY_WHY):
    return asyncio.run(paid(ctx, document_id, why=why))[0]


def test_pay_approves_in_dry_run_and_logs_the_decision(tool_ctx, judge):
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 8, "reason": "SYNTHETIC the expert's own report"}]
    assert pay(tool_ctx, 1108) == {"decision": "approved", "reason": "dry_run", "cents": 50, "score": 8,
                                   "judge": "SYNTHETIC the expert's own report", "note": purchases.DRY_RUN_NOTE}
    assert tool_ctx.run.filings[1108]["purchase"]["status"] == "would_buy"
    assert rows(tool_ctx) == [("pay_for_document", "done", "would buy 50c, score 8")]
    [event] = [e for e in store.events(tool_ctx.job_id) if e.kind == "purchase"]
    assert event.data["decision"] == "approved" and event.data["cents"] == 50 and event.data["document_id"] == 1108
    # the judge sees CourtListener's record and the price, never the agent's reason
    [prompt] = judge["prompts"]
    assert "Exhibit 8, Expert report of Alex J. Rivera" in prompt and "Doe v. Example Co. 11" in prompt
    assert "Price: $0.50" in prompt and "Name: Alex Rivera" in prompt and "SYNTHETIC-WHY" not in prompt
    assert "Docket entry: SYNTHETIC EXHIBIT re Motion for Summary Judgment" in prompt


def test_pay_public_filing_is_free_and_needs_no_judge(tool_ctx, judge):
    offered(tool_ctx, cl_filing(11))
    assert pay(tool_ctx, 1102) == {"decision": "rejected", "reason": "free_copy_found", "note": purchases.FREE_NOTE}
    assert judge["prompts"] == [] and rows(tool_ctx) == [("pay_for_document", "done", "rejected: free_copy_found")]


def test_pay_low_value_is_final_for_the_filing(tool_ctx, judge):
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 4, "reason": "SYNTHETIC already covered"}]
    first = pay(tool_ctx, 1108)
    assert first["decision"] == "rejected" and first["reason"] == "low_value" and first["score"] == 4
    assert pay(tool_ctx, 1108) == first | {"already_requested": True}
    assert len(judge["prompts"]) == 1  # the repeat costs no judge call


def test_pay_judge_failure_can_be_retried(tool_ctx, judge):
    offered(tool_ctx, not_public(8))
    judge["replies"] = [llm.ModelError("SYNTHETIC model down"), {"score": "high"}, {"score": 9, "reason": "ok"}]
    assert pay(tool_ctx, 1108)["reason"] == "judge_failed"
    assert pay(tool_ctx, 1108)["reason"] == "judge_failed"  # bad JSON too
    assert "purchase" not in tool_ctx.run.filings[1108]
    assert pay(tool_ctx, 1108)["reason"] == "dry_run"


def test_pay_a_failure_mid_judge_leaves_nothing_reserved(tool_ctx, judge):
    offered(tool_ctx, not_public(8))
    judge["replies"] = [RuntimeError("SYNTHETIC crash")]
    assert pay(tool_ctx, 1108)["error"].startswith("source call failed")
    assert "purchase" not in tool_ctx.run.filings[1108] and rows(tool_ctx)[0][1] == "failed"


def test_pay_price_limits_and_refusals(tool_ctx, judge):
    offered(tool_ctx, not_public(8, description="TRANSCRIPT of Hearing held 9-30-10"))
    assert pay(tool_ctx, 1108) == {"decision": "rejected", "reason": "over_price", "cents": None}
    assert pay(tool_ctx, 999) == {"error": court.UNKNOWN_FILING}
    assert pay(tool_ctx, 1108, why=" ") == {"error": court.PAY_WHY}
    assert pay(tool_ctx, 1108, why="x" * (court.PAY_WHY_MAX + 1)) == {"error": court.PAY_WHY}
    assert judge["prompts"] == []


def test_pay_parallel_requests_cannot_overspend(tool_ctx, judge, monkeypatch):
    """Two filings racing for the run's last $3.50: one is judged and approved, the other is over budget."""
    monkeypatch.setattr(purchases, "RUN_BUDGET_CENTS", 350)
    offered(tool_ctx, not_public(8, pages=None), not_public(9, pages=None))
    judge["replies"] = [{"score": 9, "reason": "SYNTHETIC"}]
    replies = asyncio.run(paid(tool_ctx, 1108, 1109))
    assert sorted(r["reason"] for r in replies) == ["dry_run", "over_budget"]
    assert len(judge["prompts"]) == 1 and purchases.spent_cents(tool_ctx.run.filings) == 300


def test_pay_the_same_filing_twice_at_once_is_one_decision(tool_ctx, judge):
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 9, "reason": "SYNTHETIC"}]
    first, second = asyncio.run(paid(tool_ctx, 1108, 1108))
    assert first["reason"] == "dry_run" and second == {"decision": "rejected", "reason": "already_requested",
                                                       "note": purchases.BUSY_NOTE, "already_requested": True}
    assert len(judge["prompts"]) == 1


# --- live payments: a one-time Ramp card (PAYMENTS_LIVE=true, fake Ramp answers) ------------------


@pytest.fixture(name="live")
def live_fixture(monkeypatch, ramp_http):
    monkeypatch.setenv("PAYMENTS_LIVE", "true")
    return ramp_http


def closed(ctx):
    async def close():
        token = trace.start(ctx.job_id)
        try:
            await purchases.close_cards(ctx)
        finally:
            trace.stop(token)

    asyncio.run(close())


def purchase_events(ctx):
    return [e.data for e in store.events(ctx.job_id) if e.kind == "purchase"]


def test_pay_live_makes_a_card_the_agent_never_sees(tool_ctx, judge, live):
    seen = live()
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 8, "reason": "SYNTHETIC the expert's own report"}]
    reply = pay(tool_ctx, 1108)
    assert reply == {"decision": "approved", "reason": "card_ready", "cents": 50, "score": 8,
                     "judge": "SYNTHETIC the expert's own report", "card_last4": "1111", "note": purchases.CARD_NOTE}
    assert tool_ctx.run.cards[1108].pan == RAMP_PAN and purchases.spent_cents(tool_ctx.run.filings) == 50
    assert rows(tool_ctx) == [("pay_for_document", "done", "card ready 50c, ends 1111, score 8")]
    made = json.loads(seen[-1].content)
    assert made["display_name"] == "ExpertWitness filing 1108"  # no filing title (names) sent to Ramp
    assert made["spending_restrictions"]["limit"]["amount"] == 50
    assert seen[-1].headers["x-idempotency-key"] == f"{tool_ctx.job_id}-1108"
    [event] = purchase_events(tool_ctx)
    assert event["reason"] == "card_ready" and event["card_id"] == "card-synthetic-1"
    # gotcha 64: the number and CVV are only on the in-memory card
    shown = json.dumps(reply) + json.dumps(purchase_events(tool_ctx)) + str(rows(tool_ctx))
    assert RAMP_PAN not in shown and "SYNTHETIC-CVV" not in shown
    # a second ask is the same decision, not a second card
    assert pay(tool_ctx, 1108) == reply | {"already_requested": True} and len(seen) == 2

    closed(tool_ctx)
    assert seen[-1].method == "DELETE" and seen[-1].url.path == "/developer/v1/funds/fund-synthetic-1"
    assert tool_ctx.run.cards == {}
    assert purchase_events(tool_ctx)[-1].items() >= {"document_id": 1108, "status": "card_closed",
                                                     "card_id": "card-synthetic-1", "card_last4": "1111"}.items()


def test_close_cards_never_fails_the_run(tool_ctx, judge, live):
    live(close=(500, {"error_v2": {"message": "SYNTHETIC"}}))
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 9, "reason": "SYNTHETIC"}]
    assert pay(tool_ctx, 1108)["reason"] == "card_ready"
    closed(tool_ctx)  # does not raise: the card's lock date is the backstop
    assert purchase_events(tool_ctx)[-1]["status"] == "card_close_failed" and tool_ctx.run.cards == {}


def test_pay_live_ramp_failure_reserves_nothing(tool_ctx, judge, live):
    live(card=(500, {"error_v2": {"message": "SYNTHETIC-RAMP-ERROR"}}))
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 9, "reason": "SYNTHETIC"}]
    assert pay(tool_ctx, 1108) == {"decision": "rejected", "reason": "payment_failed", "cents": 50,
                                   "note": "Ramp new card failed (HTTP 500); asking again later may help"}
    assert "purchase" not in tool_ctx.run.filings[1108] and tool_ctx.run.cards == {}
    assert purchases.spent_cents(tool_ctx.run.filings) == 0


def test_pay_live_without_ramp_keys_is_a_payment_failure(tool_ctx, judge, monkeypatch):
    monkeypatch.setenv("PAYMENTS_LIVE", "true")
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 9, "reason": "SYNTHETIC"}]
    reply = pay(tool_ctx, 1108)
    assert reply["reason"] == "payment_failed" and reply["note"] == ramp.NO_KEYS


def test_pay_live_low_value_makes_no_card(tool_ctx, judge, live):
    seen = live()
    offered(tool_ctx, not_public(8))
    judge["replies"] = [{"score": 3, "reason": "SYNTHETIC"}]
    assert pay(tool_ctx, 1108)["reason"] == "low_value" and seen == [] and tool_ctx.run.cards == {}


def test_court_helper_can_ask_to_buy(tool_ctx, research_sources, adk_model):
    research_sources()
    research(tool_ctx, adk_model, [call("court_search"), call("pay_for_document", document_id=1102, why=PAY_WHY),
                                   "SYNTHETIC done", "SYNTHETIC report"])
    assert ("pay_for_document", "done", "rejected: free_copy_found") in rows(tool_ctx)
