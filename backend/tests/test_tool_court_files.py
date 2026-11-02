"""Court filings of a found case (app/tools/court_files.py): fake CourtListener functions, fake PDF text
(pdf_text faked: a "PDF" here is synthetic page texts joined by form feeds) and a fake AI. Synthetic
people only (gotcha 8)."""

import asyncio
import json
from pathlib import Path

import pytest

from app import llm, memory, pdf_text, store
from app.sources import SourceFailed
from app.tools import RESULT_MAX, court_files, secret
from app.tools.court_files import (
    MAX_CASES_PER_RUN,
    NAME_MISSING,
    NOT_ABOUT,
    NOT_PDF,
    SAVED,
    TITLE_NO,
    TITLE_SCHEMA,
    check_case_files,
)
from tests.fakes import (  # noqa: F401 - fixtures
    cl_docket,
    research_sources_fixture,
    run_tool,
    tool_ctx_fixture,
)

HIGH = {"score": 85, "level": "high"}
REASON = "SYNTHETIC-AI-REASON the filing is this expert's report on the claim"
OWN = "Alex J. Rivera, PhD. My opinions in this matter are as follows."
OTHER = "The court sets the schedule. Counsel shall appear. Dr. Rivera is not mentioned in full."
FIXED = {TITLE_NO, NAME_MISSING, NOT_ABOUT, NOT_PDF, *SAVED.values(), court_files.NO_TEXT,
         court_files.DOWNLOAD_FAILED, court_files.AI_FAILED, court_files.CHECK_FAILED, court_files.FINDING_DROPPED,
         court_files.TIME_LIMIT}


def filing(n, description="Expert report of Alex Rivera", pages=3):
    return {"id": 900 + n, "description": description, "document_number": n, "attachment_number": None,
            "date_filed": "2021-05-01", "page_count": pages, "filepath_local": f"recap/synthetic.{n}.pdf",
            "url": f"https://www.courtlistener.com/docket/11/{n}/doe-v-example/"}


def pdf(*pages: str) -> bytes:
    return b"%PDF-" + "\f".join(pages).encode()


class Fake:
    """The fake world: PDF bytes per filepath_local, CourtListener OCR text per doc id, the AI's title
    answers (D-id -> likely) and file verdicts (a queue; an Exception is raised)."""

    def __init__(self, files, likely, verdicts=(), cl_text=None, ocr=None, ocr_finished=True):
        self.files, self.likely, self.verdicts = files, likely, list(verdicts)
        self.cl_text, self.ocr, self.ocr_finished = cl_text or {}, ocr, ocr_finished
        self.downloads, self.prompts, self.ocr_calls = [], [], 0

    def download(self, filepath_local, dest):
        self.downloads.append(Path(dest))
        Path(dest).write_bytes(self.files[filepath_local])
        return len(self.files[filepath_local])

    def model(self, instructions, input, schema=None, web_search=False, output_type=None):
        self.prompts.append(input)
        if schema is TITLE_SCHEMA:
            return llm.ModelResult(data={"files": [{"id": d, "likely": ok, "reason": "SYNTHETIC title reason"}
                                                   for d, ok in self.likely.items()]})
        item = self.verdicts.pop(0)
        if isinstance(item, Exception):
            raise item
        return llm.ModelResult(data=item)


def yes(kind="own_work"):
    return {"relevant": True, "kind": kind, "reason": REASON}


NO = {"relevant": False, "kind": "neither", "reason": "SYNTHETIC only a passing mention"}


@pytest.fixture(name="world")
def world_fixture(research_sources, monkeypatch):
    """setup(docs, fake) installs the fakes; returns the research_sources call list."""

    def page_texts(path):
        data = Path(path).read_bytes()
        if not data.startswith(b"%PDF-"):
            raise pdf_text.PdfUnreadable("not a PDF")
        return data[5:].decode().split("\f")

    def batches(pages, max_pages=2, max_chars=10**9):
        return [(i + 1, min(i + max_pages, len(pages)), court_files.numbered(pages, i + 1, min(i + max_pages, len(pages))))
                for i in range(0, len(pages), max_pages)]

    monkeypatch.setattr(pdf_text, "page_texts", page_texts)
    monkeypatch.setattr(pdf_text, "mostly_empty", lambda pages: not pages or
                        sum(not p.strip() for p in pages) * 2 > len(pages))
    monkeypatch.setattr(pdf_text, "split_text", lambda text: text.split("\f"))
    monkeypatch.setattr(pdf_text, "batches", batches)

    def setup(docs, fake, overrides=None, more_left=False):
        def ocr_pages(path, stop_at=None):
            fake.ocr_calls += 1
            return fake.ocr or [], fake.ocr_finished

        monkeypatch.setattr(pdf_text, "ocr_pages", ocr_pages)
        return research_sources({"courtlistener.docket_documents": (docs, more_left),
                                 "courtlistener.download_document": fake.download,
                                 "courtlistener.document_text": lambda doc_id: fake.cl_text.get(doc_id, ""),
                                 "llm.call_model": fake.model} | (overrides or {}))

    return setup


def check(ctx, hit=None, same=HIGH):
    return asyncio.run(check_case_files(ctx, hit or cl_docket(11, "2021-05-01"), same, "F1"))


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [g.reason for g in store.get_research(ctx.dossier_id).gaps]


def tmp_files(ctx, tmp_path):
    folder = tmp_path / "data" / "files" / ctx.dossier_id / "tmp"
    return list(folder.iterdir()) if folder.exists() else []


def test_title_rejected_is_never_downloaded(tool_ctx, world):
    fake = Fake({}, {"D1": False})
    world([filing(1, "Scheduling order")], fake)
    assert check(tool_ctx) == []
    assert fake.downloads == [] and len(fake.prompts) == 1
    assert "D1: Scheduling order | doc 1 | filed 2021-05-01 | 3 pages" in fake.prompts[0]
    assert "Name: Alex Rivera" in fake.prompts[0]  # known_so_far is the context
    assert rows(tool_ctx) == [("court_file", "skipped", TITLE_NO)]
    assert store.activity(tool_ctx.job_id)[0].args == {"title": "Scheduling order", "picked": court_files.TITLE_PICKED}


def test_full_name_missing_is_dropped_without_ai(tool_ctx, world, tmp_path):
    fake = Fake({"recap/synthetic.1.pdf": pdf(OTHER, OTHER)}, {"D1": True})
    world([filing(1)], fake)
    assert check(tool_ctx) == []
    assert len(fake.prompts) == 1 and fake.downloads  # title check only
    assert rows(tool_ctx) == [("court_file", "skipped", NAME_MISSING)]
    assert tmp_files(tool_ctx, tmp_path) == [] and memory.files(tool_ctx.dossier_id) == []


def test_ai_no_is_deleted(tool_ctx, world, tmp_path):
    fake = Fake({"recap/synthetic.1.pdf": pdf(OWN, OTHER, OTHER)}, {"D1": True}, [NO, NO])
    world([filing(1)], fake)
    assert check(tool_ctx) == []
    assert len(fake.prompts) == 3  # title + two batches (2 pages, then 1)
    assert rows(tool_ctx) == [("court_file", "skipped", NOT_ABOUT)]
    assert tmp_files(tool_ctx, tmp_path) == [] and tool_ctx.findings() == []


def test_ai_yes_saves_the_file_its_text_and_a_finding(tool_ctx, world, tmp_path):
    fake = Fake({"recap/synthetic.1.pdf": pdf(OTHER, OTHER, OWN), "recap/synthetic.2.pdf": pdf(OWN)},
                {"D1": True, "D2": True}, [NO, yes(), yes("about_testimony")])
    world([filing(1), filing(2, "Motion to exclude the testimony of Alex Rivera")], fake)
    saved = check(tool_ctx)
    assert len(fake.prompts) == 4  # D1: batch 1 no, batch 2 yes; D2: yes
    assert "[Page 3]" in fake.prompts[2] and "The filing: Expert report of Alex Rivera (doc 1)" in fake.prompts[2]
    first, second = tool_ctx.findings("court_file")
    assert saved == [{"file_id": first.facts["file_id"], "title": "Expert report of Alex Rivera", "pages": 3},
                     {"file_id": second.facts["file_id"], "title": "Motion to exclude the testimony of Alex Rivera",
                      "pages": 3}]
    assert (first.url, first.source_type, first.confidence, first.title) == (
        "https://www.courtlistener.com/docket/11/1/doe-v-example/", "courtlistener_document", "high",
        "Expert report of Alex Rivera")
    assert first.facts == {"case_name": "Doe v. Example Co. 11", "case_ref": "F1", "document_number": "1",
                           "date_filed": "2021-05-01", "page_count": 3, "kind": "own_work", "reason": REASON,
                           "file_id": first.facts["file_id"], "text_source": "text layer"}
    row = memory.file(first.facts["file_id"])
    path = Path(row["path"])
    assert path.parent == tmp_path / "data" / "files" / tool_ctx.dossier_id and path.suffix == ".pdf"
    assert path.read_bytes() == pdf(OTHER, OTHER, OWN) and row["bytes"] == path.stat().st_size
    assert Path(row["text_path"]).read_text() == f"[Page 1]\n{OTHER}\n[Page 2]\n{OTHER}\n[Page 3]\n{OWN}"
    assert tool_ctx.file_text(first.facts["file_id"]).endswith(OWN)
    assert tmp_files(tool_ctx, tmp_path) == []
    assert rows(tool_ctx) == [("court_file", "done", SAVED["own_work"]),
                              ("court_file", "done", SAVED["about_testimony"])]
    assert REASON not in json.dumps([a.model_dump() for a in store.activity(tool_ctx.job_id)])  # gotcha 47


def test_medium_case_saves_low_confidence(tool_ctx, world):
    world([filing(1)], Fake({"recap/synthetic.1.pdf": pdf(OWN)}, {"D1": True}, [yes()]))
    check(tool_ctx, same={"level": "medium"})
    assert tool_ctx.findings("court_file")[0].confidence == "low"


def test_http_400_splits_the_batch_in_half(tool_ctx, world, monkeypatch):
    """A batch too big for the model (HTTP 400) is asked about in halves, down to one page."""
    too_big = llm.ModelError("model call failed (BadRequestError, HTTP 400)")
    fake = Fake({"recap/synthetic.1.pdf": pdf(OTHER, OTHER, OTHER, OWN)}, {"D1": True},
                [too_big, too_big, NO, NO, too_big, NO, yes()])
    world([filing(1)], fake)
    monkeypatch.setattr(pdf_text, "batches", lambda pages: [(1, 4, court_files.numbered(pages, 1, 4))])
    assert len(check(tool_ctx)) == 1
    asked = [sorted(int(x.split("]")[0]) for x in p.split("[Page ")[1:]) for p in fake.prompts[1:]]
    assert asked == [[1, 2, 3, 4], [1, 2], [1], [2], [3, 4], [3], [4]]


def test_other_model_error_is_a_gap_and_drops_the_file(tool_ctx, world, tmp_path):
    fake = Fake({"recap/synthetic.1.pdf": pdf(OWN)}, {"D1": True}, [llm.ModelError("model call failed (RuntimeError)")])
    world([filing(1)], fake)
    assert check(tool_ctx) == []
    assert rows(tool_ctx) == [("court_file", "failed", court_files.AI_FAILED)]
    assert gaps(tool_ctx) == ["AI check of a court filing failed (model call failed (RuntimeError))"]
    assert tmp_files(tool_ctx, tmp_path) == []


def test_unreadable_pdf_and_failed_download(tool_ctx, world, tmp_path):
    fake = Fake({"recap/synthetic.1.pdf": b"<html>SYNTHETIC not a pdf</html>"}, {"D1": True, "D2": True})

    def download(path, dest):
        if path.endswith("2.pdf"):
            raise SourceFailed("SYNTHETIC storage down")
        return fake.download(path, dest)

    world([filing(1), filing(2)], fake, {"courtlistener.download_document": download})
    assert check(tool_ctx) == []
    assert rows(tool_ctx) == [("court_file", "skipped", NOT_PDF), ("court_file", "failed", court_files.DOWNLOAD_FAILED)]
    assert len(fake.prompts) == 1 and tmp_files(tool_ctx, tmp_path) == []


def test_scan_uses_courtlistener_ocr_then_local_ocr(tool_ctx, world):
    fake = Fake({"recap/synthetic.1.pdf": pdf("", "", ""), "recap/synthetic.2.pdf": pdf("", "")},
                {"D1": True, "D2": True}, [yes(), yes()], cl_text={901: f"{OWN}\f{OTHER}"}, ocr=[OWN])
    world([filing(1), filing(2)], fake)
    assert len(check(tool_ctx)) == 2
    assert [f.facts["text_source"] for f in tool_ctx.findings("court_file")] == ["courtlistener ocr", "local ocr"]
    assert fake.ocr_calls == 1  # only the file CourtListener has no text for


def test_no_text_at_all_is_skipped(tool_ctx, world):
    world([filing(1)], Fake({"recap/synthetic.1.pdf": pdf("", "")}, {"D1": True}))
    assert check(tool_ctx) == []
    assert rows(tool_ctx) == [("court_file", "skipped", court_files.NO_TEXT)]


def test_time_limit_leaves_a_gap(tool_ctx, world, tmp_path):
    fake = Fake({f"recap/synthetic.{n}.pdf": pdf(OWN) for n in (1, 2, 3)}, {"D1": True, "D2": True, "D3": True})

    def verdict_then_stop(instructions, input, schema=None, **kw):
        if schema is not TITLE_SCHEMA:
            tool_ctx.run.seconds = -1  # the clock runs out during the first file's AI check
            return llm.ModelResult(data=yes())
        return fake.model(instructions, input, schema)

    world([filing(1), filing(2), filing(3)], fake, {"llm.call_model": verdict_then_stop})
    assert len(check(tool_ctx)) == 1 and len(fake.downloads) == 1
    assert gaps(tool_ctx) == ["2 court filings not checked: time limit"]
    assert tmp_files(tool_ctx, tmp_path) == []


def test_which_cases_are_checked(tool_ctx, world):
    """Only high/medium cases with a docket id, each docket once, at most MAX_CASES_PER_RUN per run."""
    calls = world([], Fake({}, {}))
    check(tool_ctx, same={"level": "low"})
    check(tool_ctx, hit=cl_docket(11, "2021-05-01") | {"docket_id": None})
    check(tool_ctx)
    check(tool_ctx)  # same docket again
    assert [c[1] for c in calls if c[0] == "courtlistener.docket_documents"] == [(11,)]
    tool_ctx.run.dockets = set(range(100, 100 + MAX_CASES_PER_RUN))
    check(tool_ctx, hit=cl_docket(12, "2021-05-01"))
    assert len([c for c in calls if c[0] == "courtlistener.docket_documents"]) == 1


def test_listing_or_title_check_failing_is_a_gap(tool_ctx, world):
    world([], Fake({}, {}), {"courtlistener.docket_documents": SourceFailed("SYNTHETIC CourtListener down")})
    assert check(tool_ctx) == []
    world([filing(1)], Fake({}, {}), {"llm.call_model": llm.ModelError("model call failed (RuntimeError)")})
    assert check(tool_ctx, hit=cl_docket(12, "2021-05-01")) == []
    assert gaps(tool_ctx) == [
        "court filings of Doe v. Example Co. 11 not listed: SYNTHETIC CourtListener down",
        ("AI title check of court filings failed (model call failed (RuntimeError)); filings of Doe v. Example Co. 12"
         " not checked")]


def test_activity_rows_hold_fixed_details_only(tool_ctx, world):
    fake = Fake({"recap/synthetic.2.pdf": pdf(OTHER), "recap/synthetic.3.pdf": pdf(OWN),
                 "recap/synthetic.4.pdf": pdf(OWN)}, {"D2": True, "D3": True, "D4": True}, [NO, yes()])
    world([filing(1, "Notice of appearance"), filing(2), filing(3), filing(4)], fake)
    check(tool_ctx)
    assert {r[2] for r in rows(tool_ctx)} <= FIXED and len(rows(tool_ctx)) == 4


def test_court_records_reply_lists_saved_files(tool_ctx, world):
    def model(instructions, input, schema=None, **kw):
        if schema is TITLE_SCHEMA or schema is court_files.FILE_SCHEMA:
            return fake.model(instructions, input, schema)
        return llm.ModelResult(data={"cases": [{"id": "C1", "same_person": 90, "role": "expert witness",
                                                "reason": "SYNTHETIC same field"}]})

    fake = Fake({"recap/synthetic.1.pdf": pdf(OWN)}, {"D1": True}, [yes()])
    world([filing(1)], fake, {"courtlistener.search_dockets": [cl_docket(11, "2021-05-01")], "llm.call_model": model})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    [saved] = tool_ctx.findings("court_file")
    assert reply["files"] == [{"file_id": saved.facts["file_id"], "title": "Expert report of Alex Rivera", "pages": 3}]
    assert saved.facts["case_ref"] == reply["cases"][0]["ref"] and saved.collector == "court_records"
    assert [r[0] for r in rows(tool_ctx)] == ["court_records", "court_file"]


def test_unfinished_local_ocr_is_the_time_limit(tool_ctx, world, tmp_path):
    """OCR stopped at the deadline: counted as not checked, never as "full name not in the file"."""
    fake = Fake({"recap/synthetic.1.pdf": pdf("", ""), "recap/synthetic.2.pdf": pdf(OWN)}, {"D1": True, "D2": True},
                ocr=["SYNTHETIC first page without the name"], ocr_finished=False)
    world([filing(1), filing(2)], fake)
    assert check(tool_ctx) == []
    assert rows(tool_ctx) == [("court_file", "skipped", court_files.TIME_LIMIT)]
    assert gaps(tool_ctx) == ["2 court filings not checked: time limit"] and len(fake.downloads) == 1
    assert tmp_files(tool_ctx, tmp_path) == []


def test_filings_left_unlisted_are_a_gap(tool_ctx, world):
    world([filing(1, "Scheduling order")], Fake({}, {"D1": False}), more_left=True)
    check(tool_ctx)
    assert gaps(tool_ctx) == ["court filings past the first 1 of Doe v. Example Co. 11 not checked"]


def test_no_orphan_file_when_saving_fails(tool_ctx, world, monkeypatch, tmp_path):
    """memory.add_file failing after the move: the moved PDF and its .md are removed again."""
    world([filing(1)], Fake({"recap/synthetic.1.pdf": pdf(OWN)}, {"D1": True}, [yes()]))

    def broken(*args):
        raise RuntimeError("SYNTHETIC disk full")

    monkeypatch.setattr(memory, "add_file", broken)
    assert check(tool_ctx) == []
    assert rows(tool_ctx) == [("court_file", "failed", court_files.CHECK_FAILED)]
    folder = tmp_path / "data" / "files" / tool_ctx.dossier_id
    assert [p.name for p in folder.iterdir()] == ["tmp"] and tmp_files(tool_ctx, tmp_path) == []


def test_finding_dropped_keeps_no_file(tool_ctx, world, tmp_path):
    world([filing(1)], Fake({"recap/synthetic.1.pdf": pdf(OWN)}, {"D1": True}, [yes()]))
    tool_ctx.add_finding = lambda *args, **kwargs: None  # gotcha 46: e.g. a fact looked like a secret
    assert check(tool_ctx) == []
    assert rows(tool_ctx) == [("court_file", "skipped", court_files.FINDING_DROPPED)]
    assert memory.files(tool_ctx.dossier_id) == []
    assert [p.name for p in (tmp_path / "data" / "files" / tool_ctx.dossier_id).iterdir()] == ["tmp"]


def test_worst_case_court_records_reply_fits(tool_ctx, world):
    """10 cases x 10 saved files, longest texts: the reply stays under RESULT_MAX (a longer reply is
    dropped whole by the wrapper)."""
    long_name = "Doe v. " + "Example Holdings " * 12  # ~200 characters
    dockets = [cl_docket(n, f"2021-01-{n:02d}", passage="Alex Rivera " + "x" * 388) | {"case_name": long_name + str(n)}
               for n in range(1, 11)]
    def docs_of(docket_id):  # every filing its own URL (one finding per kind + URL, gotcha 49)
        docs = [filing(n, "Expert report of Alex Rivera " + "y" * 200) for n in range(1, 11)]
        return [d | {"id": docket_id * 100 + n, "url": d["url"].replace("/11/", f"/{docket_id}/")}
                for n, d in enumerate(docs, 1)], False

    fake = Fake({f"recap/synthetic.{n}.pdf": pdf(OWN) for n in range(1, 11)}, {f"D{n}": True for n in range(1, 11)},
                [yes()] * 100)

    def model(instructions, input, schema=None, **kw):
        if schema is TITLE_SCHEMA or schema is court_files.FILE_SCHEMA:
            return fake.model(instructions, input, schema)
        return llm.ModelResult(data={"cases": [{"id": f"C{n}", "same_person": 90, "role": "consultant or reviewer",
                                                "reason": f"SYNTHETIC {n} " + "z" * 290} for n in range(1, 11)]})

    tool_ctx.run.seconds = 600
    world([], fake, {"courtlistener.search_dockets": dockets, "courtlistener.docket_documents": docs_of,
                     "llm.call_model": model})
    reply = asyncio.run(run_tool(tool_ctx, "court_records"))
    assert "error" not in reply and len(tool_ctx.findings("court_file")) == 100
    assert len(reply["cases"]) == 10 and len(json.dumps(reply)) <= RESULT_MAX
    listed = {f["file_id"] for f in reply["files"]}  # every saved file listed: none left for the agent to guess
    assert listed == {f.facts["file_id"] for f in tool_ctx.findings("court_file")} and "files_not_listed" not in reply


def test_own_ids_never_look_like_secrets(tool_ctx):
    """A random id can hold 12+ digits in a row (a card number to looks_secret); facts hold file_ids.
    Only ids with a hex letter are exempt: a card number after one of our prefixes is still caught."""
    assert not secret("file_1234567890123a56") and not secret("see fnd_a1b2c3d4e5f60718 for the case")
    assert secret("file_4111111111111111") and secret("fnd_4111111111111111 note")
    assert secret("fnd_a1b2c3d4e5f60718 4111 1111 1111 1111")  # a card number next to a real id
    ref = tool_ctx.add_finding("court_file", "SYNTHETIC filing", "https://www.courtlistener.com/docket/1/2/x/",
                               facts={"file_id": "file_1234567890123a56", "case_ref": "fnd_9876543210987b54"})
    assert ref and tool_ctx.findings()[0].facts["file_id"] == "file_1234567890123a56"


def searched(did=11, n=1):
    """A docket whose search hit matched filing n on the full name (the hit's documents hold its URL)."""
    hit = cl_docket(did, "2021-05-01")
    hit["documents"][0]["url"] = filing(n)["url"].upper() + "/"  # normalized before comparing
    return hit


def test_search_matched_filing_skips_the_title_check(tool_ctx, world):
    """CourtListener's full-text search found the name in filing 2: downloaded first, never in the title
    prompt; the full file check (name + AI) still runs."""
    fake = Fake({"recap/synthetic.2.pdf": pdf(OWN)}, {"D1": False}, [yes()])
    world([filing(1, "Scheduling order"), filing(2, "Exhibit B, Appeal Decision Correspondence")], fake)
    saved = check(tool_ctx, hit=searched(n=2))
    title_prompt = fake.prompts[0]
    assert "D1: Scheduling order" in title_prompt and "Appeal Decision" not in title_prompt
    assert [d.name for d in fake.downloads] == ["902.pdf"] and len(saved) == 1
    assert [(a.args.get("picked"), a.detail) for a in store.activity(tool_ctx.job_id)] == [
        (court_files.TITLE_PICKED, TITLE_NO), (court_files.SEARCH_PICKED, SAVED["own_work"])]


def test_title_call_skipped_when_all_filings_are_search_matched(tool_ctx, world):
    fake = Fake({"recap/synthetic.1.pdf": pdf(OTHER)}, {})
    world([filing(1)], fake)
    assert check(tool_ctx, hit=searched(n=1)) == []
    assert fake.prompts == [] and len(fake.downloads) == 1  # name check drops it before any AI call
    assert rows(tool_ctx) == [("court_file", "skipped", NAME_MISSING)]


def test_search_matched_filing_is_checked_when_the_title_check_fails(tool_ctx, world):
    fake = Fake({"recap/synthetic.2.pdf": pdf(OWN)}, {}, [yes()])
    titles_down = llm.ModelError("model call failed (RuntimeError)")

    def model(instructions, input, schema=None, **kw):
        if schema is TITLE_SCHEMA:
            raise titles_down
        return fake.model(instructions, input, schema)

    world([filing(1), filing(2)], fake, {"llm.call_model": model})
    assert len(check(tool_ctx, hit=searched(n=2))) == 1


def test_instructions_cover_reviewing_physicians():
    assert "appeal decision letters or correspondence, peer reviews, independent medical exams" in \
        court_files.TITLE_INSTRUCTIONS.replace("\n", " ")
    assert "they are not routine exhibits" in court_files.TITLE_INSTRUCTIONS.replace("\n", " ")
    assert ("quotes the expert's peer review, records review, independent medical exam or medical opinion"
            in court_files.FILE_INSTRUCTIONS.replace("\n", " "))


def test_new_ids_are_never_all_digits(monkeypatch):
    """gotcha 60: an all-digit id looks like a card number; store.new_id draws again."""
    tokens = iter(["4111111111111111", "0123456789abcdef"])
    monkeypatch.setattr(store.secrets, "token_hex", lambda n: next(tokens))
    assert store.new_id("file") == "file_0123456789abcdef"


def test_fit_lists_files_before_case_details():
    from app.tools.courtlistener import fit
    files = [{"file_id": f"file_{n:015x}a", "title": "SYNTHETIC title " * 4, "pages": 3} for n in range(200)]
    reply = fit({"cases": [{"case_name": "x" * 200, "passage": "y" * 400, "why": "z"}], "files": files})
    assert len(json.dumps(reply)) <= RESULT_MAX and reply["files_not_listed"] > 0  # too many: the last are left out
    assert "passage" not in reply["cases"][0] and "title" not in reply["files"][0]
