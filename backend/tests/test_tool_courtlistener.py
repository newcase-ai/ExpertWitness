"""Tool 1: court records (DESIGN 4B.9): court_search, court_filings, court_file and court_verdict with
fake CourtListener functions and fake PDF text (a "PDF" here is synthetic page texts joined by form
feeds). Synthetic people only (gotcha 8)."""

import asyncio
import json
from pathlib import Path

import pytest

from app import jobs, memory, pdf_text, store
from app.profile import PER_KIND, finding_line, known_so_far
from app.sources import SourceFailed
from app.sources import courtlistener as courtlistener_source
from app.tools import RESULT_MAX, secret
from app.tools import courtlistener as court
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    call,
    cl_docket,
    cl_filing,
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
    calls = research_sources({"courtlistener.search_dockets": [
        cl_docket(1, "2020-01-01", documents=[surname]), cl_docket(2, "2019-01-01", named_as=["party"], documents=[sealed]),
        cl_docket(3, "2018-01-01") | {"docket_id": None}]})
    reply = search(tool_ctx)
    assert calls[0] == ("courtlistener.search_dockets", ("Alex Rivera", "", ""), {})
    case = {"court": "District Court, N.D. Example", "nature_of_suit": "791 ERISA"}
    assert reply == {"cases": [
        case | {"docket_id": 1, "case_name": "Doe v. Example Co. 1", "docket_number": "1:1-cv-0001",
                "date_filed": "2020-01-01", "filings": [{"document_id": 102, "title": "Exhibit 2, Appeal Decision Letter",
                                                         "pages": 3, "available": True,
                                                         "passage": f"{SNIPPET} reviewed by Dr. Rivera"}]},
        case | {"docket_id": 2, "case_name": "Doe v. Example Co. 2", "docket_number": "1:2-cv-0001",
                "date_filed": "2019-01-01", "named_as": ["party"],
                "filings": [{"document_id": 205, "title": "Sealed exhibit", "pages": 3, "available": False}]}]}
    assert tool_ctx.findings() == [] and gaps(tool_ctx) == []
    assert set(tool_ctx.run.cases) == {1, 2} and tool_ctx.run.filings[205]["docket_id"] == 2
    assert rows(tool_ctx) == [("court_search", "done", "2 cases found")]
    assert SNIPPET not in stored(tool_ctx, tmp_path)


def test_search_words_and_case_name(tool_ctx, research_sources):
    calls = research_sources()
    search(tool_ctx, words='"motion to exclude"', case_name="Doe v. Example Co.")
    assert calls[-1] == ("courtlistener.search_dockets", ("Alex Rivera", '"motion to exclude"', "Doe v. Example Co."), {})
    assert search(tool_ctx, words="x" * (court.WORDS_MAX + 1)) == {"error": court.TOO_LONG}
    assert search(tool_ctx, case_name="x" * (court.CASE_NAME_MAX + 1)) == {"error": court.TOO_LONG}


def test_no_cases_is_a_gap_for_the_plain_search_only(tool_ctx, research_sources):
    research_sources({"courtlistener.search_dockets": []})
    assert search(tool_ctx, words="Medtronic") == {"cases": []} and gaps(tool_ctx) == []
    assert search(tool_ctx) == {"cases": []}
    assert gaps(tool_ctx) == [("court_search", court.NONE_GAP)]


def test_search_reply_fits(tool_ctx, research_sources):
    """20 cases x 3 long filings: the reply stays under RESULT_MAX (a longer one is dropped whole,
    gotcha 59); every case is remembered, the rest counted."""
    def docs(did):
        return [cl_filing(did, n, passage="Alex Rivera " + "x" * 400, description="y" * 200) for n in (1, 2, 3)]

    research_sources({"courtlistener.search_dockets": [cl_docket(d, "2021-01-01", documents=docs(d))
                                                       | {"case_name": "z" * 200} for d in range(1, 21)]})
    reply = search(tool_ctx)
    assert len(json.dumps(reply)) <= RESULT_MAX and reply["not_shown"] > 0
    assert len(reply["cases"]) + reply["not_shown"] == 20 and len(tool_ctx.run.cases) == 20


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
        research_sources({"courtlistener.search_dockets": [cl_docket(11, "2021-05-01", documents=docs)],
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
    research_sources({"courtlistener.search_dockets": [cl_docket(11, "2021-05-01",
                                                                 documents=[cl_filing(11, passage=passage)])]})
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


def test_court_tools_run_through_the_main_agent(tool_ctx, research_sources, adk_model):
    research_sources()
    adk_model.script = [call("court_search", words="toxicology"),
                        call("court_verdict", docket_id=11, same_person=80, role="expert witness", why=WHY), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done"
    assert [(a.tool, a.label, a.status) for a in job.activity] == [
        ("court_search", "Court search", "done"), ("court_verdict", "Court case verdict", "done")]
    assert [f.kind for f in tool_ctx.findings()] == ["court_docket"]
    assert {"court_filings", "court_file"} <= set(adk_model.seen[0].tools_dict)
    assert gaps(tool_ctx) == []  # searched, nothing downloaded: no unjudged case


def test_a_case_read_but_not_judged_is_a_gap_however_the_run_ends(tool_ctx, world, adk_model):
    """The run stops (here a model error) after a filing was downloaded and before any verdict."""
    world({"recap/synthetic.11.2.pdf": pdf(OWN)})
    adk_model.script = [call("court_search"), call("court_file", document_id=1102), RuntimeError("SYNTHETIC down")]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    assert ("main_agent", court.UNJUDGED_GAP.format("Doe v. Example Co. 11")) in gaps(tool_ctx)
    assert tool_ctx.findings() == []


def test_a_judged_case_is_no_gap(tool_ctx, world, adk_model):
    world({"recap/synthetic.11.2.pdf": pdf(OWN)})
    adk_model.script = [call("court_search"), call("court_file", document_id=1102),
                        call("court_verdict", docket_id=11, same_person=5, role="party", why=WHY), "DONE"]
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
