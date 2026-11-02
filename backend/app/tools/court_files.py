"""Court filings of a found case (tool 1's helper, not a tool; plan 2026-11-02): after court_records or
court_case saves a high/medium case, list its public RECAP filings, let the AI pick the titles about
the expert, download those, and keep only files that name the expert in full (gotcha 13) and that the
AI reads as the expert's own work or about their testimony. Everything else is deleted at once.

One activity row per filing with a fixed reason (gotcha 47); the AI's reasons go to the trace and to
the saved finding only (copy-checked by ask_model, gotcha 15). Court files come from CourtListener on
purpose: `download_document` is its own source call, never a page fetch (gotcha 40 stays)."""

import asyncio
import time

from app import llm, pdf_text, store, trace
from app.profile import known_so_far
from app.sources import SourceFailed, SourceSkipped, courtlistener, has_full_name
from app.tools import Ctx, short_args

MAX_CASES_PER_RUN = 10
LEVELS = ("high", "medium")
REASON_MAX = 300
TITLE_SHOWN = 60  # characters of a saved filing's title in the tool reply (court_records may cut more)
KINDS = ("own_work", "about_testimony", "neither")
TEXT_LAYER, COURTLISTENER_OCR, LOCAL_OCR = "text layer", "courtlistener ocr", "local ocr"
# Fixed activity details (never file, title or model text).
SAVED = {"own_work": "saved: expert's own work", "about_testimony": "saved: about the expert's testimony"}
FINDING_DROPPED = "skipped: finding dropped"
TIME_LIMIT = "skipped: time limit"
TITLE_NO = "skipped: title not about the expert"
SEARCH_PICKED = "name found by CourtListener search"  # why a filing skipped the title check
TITLE_PICKED = "title check"
NAME_MISSING = "skipped: full name not in the file"
NOT_ABOUT = "skipped: not about the expert"
NOT_PDF = "skipped: not a readable PDF"
NO_TEXT = "skipped: no text"
DOWNLOAD_FAILED = "skipped: download failed"
AI_FAILED = "skipped: AI check failed"
CHECK_FAILED = "skipped: file check failed"
TIME_GAP = "{} court filings not checked: time limit"
MORE_GAP = "court filings past the first {} of {} not checked"
LIST_GAP = "court filings of {} not listed: {}"
TITLE_GAP = "AI title check of court filings failed ({}); filings of {} not checked"
FILE_GAP = "AI check of a court filing failed ({})"
ERROR_GAP = "court filing check failed ({})"

TITLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["files"],
    "properties": {"files": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "likely", "reason"],
        "properties": {"id": {"type": "string"}, "likely": {"type": "boolean"},
                       "reason": {"type": "string", "maxLength": REASON_MAX}},
    }}},
}
TITLE_INSTRUCTIONS = """You pick which filings of one federal court case are likely about one specific
expert witness. You get what the research knows about the expert and the case's public filings (D1,
D2, ...: description, document number, date, page count). Mark a filing likely=true only if its title
suggests it is the expert's own work (an expert report, declaration, affidavit, deposition transcript,
CV) or about the expert's testimony (a motion, brief or order to admit, limit or exclude this expert's
testimony, a Daubert motion about them). For a reviewing, consulting or examining physician (and
similar experts who review claims or records), the administrative record, claim file, appeal decision
letters or correspondence, peer reviews, independent medical exams and records reviews often hold
their opinions: mark those likely=true; they are not routine exhibits. Routine filings (complaints,
scheduling orders, notices, filings about other experts) are likely=false. Give every filing, with a
short reason in your own words. Use only what you are given."""

FILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relevant", "kind", "reason"],
    "properties": {"relevant": {"type": "boolean"}, "kind": {"type": "string", "enum": list(KINDS)},
                   "reason": {"type": "string", "maxLength": REASON_MAX}},
}
FILE_INSTRUCTIONS = """You read part of one federal court filing ([Page N] marks each page) and decide
whether it is about one specific expert witness. You get what the research knows about the expert,
the case and the filing's title. relevant=true only if this is the expert's own work (their report,
declaration, affidavit, deposition, CV: kind "own_work") or about their testimony (a motion, brief or
ruling to admit, limit or exclude this expert: kind "about_testimony"). A filing that contains or
quotes the expert's peer review, records review, independent medical exam or medical opinion (for
example inside an administrative record or an appeal decision letter) is relevant, kind "own_work".
A filing that only mentions the
name, or is about someone else with the same name, is relevant=false with kind "neither". reason: one
sentence in your own words, never quoting the filing. Professional facts only."""


def row(ctx: Ctx, doc: dict, status: str, detail: str, picked: str | None = None) -> None:
    """One finished activity row for a filing: its title (cut, secrets hidden), why it was picked (fixed
    text) and a fixed detail."""
    args = {"title": doc.get("description") or ""} | ({"picked": picked} if picked else {})
    store.start_activity(ctx.job_id, "court_file", "Court file", short_args(args), status, detail)


def same_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def case_line(hit: dict) -> str:
    return (f"{hit.get('case_name') or 'unnamed case'} | {hit.get('court') or 'court unknown'} | "
            f"docket {hit.get('docket_number') or 'unknown'} | filed {hit.get('date_filed') or 'unknown'}")


def doc_number(doc: dict) -> str:
    n = str(doc.get("document_number") or "?")
    return f"{n}-{doc['attachment_number']}" if doc.get("attachment_number") else n


def filing_line(i: int, doc: dict) -> str:
    return (f"D{i}: {doc.get('description') or 'no description'} | doc {doc_number(doc)} | filed "
            f"{doc.get('date_filed') or 'unknown'} | {doc.get('page_count') or 'unknown'} pages")


def numbered(pages: list[str], first: int, last: int) -> str:
    """Pages first..last (1-based), each after a "[Page N]" line."""
    return "\n".join(f"[Page {n}]\n{pages[n - 1]}" for n in range(first, last + 1))


async def pick_titles(ctx: Ctx, hit: dict, docs: list[dict], about: str) -> set[str] | None:
    """The D-ids whose titles the AI marks likely; None (and a gap) when the model fails."""
    prompt = (f"The expert:\n{about}\n\nThe case: {case_line(hit)}\n\nPublic filings:\n"
              + "\n".join(filing_line(i, d) for i, d in enumerate(docs, 1)))
    try:
        data = await ctx.ask_model(TITLE_INSTRUCTIONS, prompt, schema=TITLE_SCHEMA)
    except llm.ModelError as e:
        ctx.add_gap(TITLE_GAP.format(e, hit.get("case_name") or "a case"))
        return None
    answers = [a for a in (data.get("files") or [] if isinstance(data, dict) else []) if isinstance(a, dict)]
    trace.record("court_file", cut=trace.PREVIEW, step="titles", case=hit.get("case_name"),
                 answers=[{k: a.get(k) for k in ("id", "likely", "reason")} for a in answers])
    return {a.get("id") for a in answers if a.get("likely") is True}


async def judge(ctx: Ctx, head: str, pages: list[str], first: int, last: int, text: str) -> dict:
    """The AI verdict on pages first..last. An HTTP 400 (input too big) splits them in half and asks
    about each half (down to one page), stopping at the first relevant half. Raises llm.ModelError."""
    try:
        data = await ctx.ask_model(FILE_INSTRUCTIONS, f"{head}\n\n{text}", schema=FILE_SCHEMA)
    except llm.ModelError as e:
        if "HTTP 400" not in str(e) or first == last:
            raise
        middle = (first + last) // 2
        verdict = await judge(ctx, head, pages, first, middle, numbered(pages, first, middle))
        if verdict["relevant"] or ctx.time_left() <= 0:
            return verdict
        return await judge(ctx, head, pages, middle + 1, last, numbered(pages, middle + 1, last))
    data = data if isinstance(data, dict) else {}
    kind = data.get("kind") if data.get("kind") in KINDS else "neither"
    return {"relevant": data.get("relevant") is True and kind != "neither", "kind": kind,
            "reason": (data.get("reason") or "")[:REASON_MAX], "pages": [first, last]}


async def read_text(ctx: Ctx, doc: dict, path) -> tuple[list[str], str, bool]:
    """(page texts, text source, finished): the PDF's text layer; for a scan, CourtListener's OCR text,
    else local OCR until the run's time is up (finished False). Raises pdf_text.PdfUnreadable."""
    pages = await asyncio.to_thread(pdf_text.page_texts, path)
    if not pdf_text.mostly_empty(pages):
        return pages, TEXT_LAYER, True
    try:
        text = await courtlistener.document_text(doc["id"])
    except (SourceFailed, SourceSkipped):
        text = ""
    if text.strip():
        return pdf_text.split_text(text), COURTLISTENER_OCR, True
    pages, finished = await asyncio.to_thread(pdf_text.ocr_pages, path, time.monotonic() + ctx.time_left())
    trace.record("court_file", step="ocr", doc=doc.get("id"), pages=len(pages), finished=finished)
    return pages, LOCAL_OCR, finished


async def save(ctx: Ctx, hit: dict, same: dict, case_ref: str, doc: dict, tmp, pages: list[str],
               source: str, verdict: dict) -> dict | None:
    """The finding first (with the new file_id), then the file moved into place: a finding that is
    not saved leaves no file behind (gotcha 46). The reply entry, or None if the finding was dropped."""
    file_id, title = store.new_id("file"), doc.get("description") or "Court filing"
    ref = ctx.add_finding(
        "court_file", title, doc["url"], confidence="high" if same["level"] == "high" else "low",
        facts={"case_name": hit.get("case_name"), "case_ref": case_ref, "document_number": doc_number(doc),
               "date_filed": doc.get("date_filed"), "page_count": doc.get("page_count") or len(pages),
               "kind": verdict["kind"], "reason": verdict["reason"], "file_id": file_id, "text_source": source},
        source_type="courtlistener_document")
    if ref is None:
        return None
    await ctx.keep_file(doc["url"], tmp, "application/pdf", numbered(pages, 1, len(pages)), file_id=file_id)
    return {"file_id": file_id, "title": title[:TITLE_SHOWN], "pages": doc.get("page_count") or len(pages)}


async def check_file(ctx: Ctx, hit: dict, same: dict, case_ref: str, doc: dict,
                     about: str) -> tuple[str, str, dict | None]:
    """Download, read and check one filing: (activity status, fixed detail, the saved file's reply
    entry or None). Detail TIME_LIMIT when the run's time ran out first. The temp file never stays."""
    folder = ctx.files_folder() / "tmp"
    folder.mkdir(exist_ok=True)
    tmp = folder / f"{doc['id']}.pdf"
    try:
        try:
            await courtlistener.download_document(doc["filepath_local"], tmp)
        except (SourceFailed, SourceSkipped):
            return "failed", DOWNLOAD_FAILED, None
        try:
            pages, source, finished = await read_text(ctx, doc, tmp)
        except pdf_text.PdfUnreadable:
            return "skipped", NOT_PDF, None
        if not finished:  # local OCR stopped at the deadline: not checked, never "name missing"
            return "skipped", TIME_LIMIT, None
        if not "".join(pages).strip():
            return "skipped", NO_TEXT, None
        if not has_full_name("\n".join(pages), ctx.expert.display_name):  # gotcha 13: no AI call
            return "skipped", NAME_MISSING, None
        head = (f"The expert:\n{about}\n\nThe case: {case_line(hit)}\n\nThe filing: "
                f"{doc.get('description') or 'no description'} (doc {doc_number(doc)})")
        verdict = None
        for first, last, text in pdf_text.batches(pages):
            if ctx.time_left() <= 0:
                return "skipped", TIME_LIMIT, None
            try:
                verdict = await judge(ctx, head, pages, first, last, text)
            except llm.ModelError as e:
                ctx.add_gap(FILE_GAP.format(e))
                return "failed", AI_FAILED, None
            if verdict["relevant"]:
                break
        trace.record("court_file", cut=trace.PREVIEW, step="file", doc=doc.get("id"), title=doc.get("description"),
                     text_source=source, pages=len(pages), verdict=verdict)
        if not verdict or not verdict["relevant"]:
            return "skipped", NOT_ABOUT, None
        entry = await save(ctx, hit, same, case_ref, doc, tmp, pages, source, verdict)
        if entry is None:
            return "skipped", FINDING_DROPPED, None
        return "done", SAVED[verdict["kind"]], entry
    except Exception as e:  # noqa: BLE001 - one bad file (OCR crash, odd PDF) never breaks the case tool
        ctx.add_gap(ERROR_GAP.format(type(e).__name__))
        return "failed", CHECK_FAILED, None
    finally:
        tmp.unlink(missing_ok=True)  # gone already when kept (moved by keep_file)


async def check_case_files(ctx: Ctx, hit: dict, same: dict, case_ref: str) -> list[dict]:
    """Plan steps 1-9 for one saved case: [{file_id, title, pages}] of the filings saved. Only for
    a high/medium case with a docket id, each docket once per run, at most MAX_CASES_PER_RUN."""
    docket_id = hit.get("docket_id")
    if (same.get("level") not in LEVELS or not docket_id or docket_id in ctx.run.dockets
            or len(ctx.run.dockets) >= MAX_CASES_PER_RUN):
        return []
    ctx.run.dockets.add(docket_id)
    case = hit.get("case_name") or "a case"
    try:
        docs, more_left = await courtlistener.docket_documents(docket_id)
    except (SourceFailed, SourceSkipped) as e:
        ctx.add_gap(LIST_GAP.format(case, e))
        return []
    if more_left:
        ctx.add_gap(MORE_GAP.format(len(docs), case))
    if not docs:
        return []
    about = known_so_far(ctx)
    # Filings CourtListener's full-text search matched on the name skip the title check (user,
    # 2026-11-02: titles like "Exhibit B, Appeal Decision Correspondence" were all rejected).
    found = {same_url(d.get("url")) for d in hit.get("documents") or [] if d.get("url")}
    todo = [(d, SEARCH_PICKED) for d in docs if same_url(d.get("url")) in found]
    rest = [d for d in docs if same_url(d.get("url")) not in found]
    if rest and (picked := await pick_titles(ctx, hit, rest, about)) is not None:
        for i, doc in enumerate(rest, 1):
            if f"D{i}" in picked:
                todo.append((doc, TITLE_PICKED))
            else:
                row(ctx, doc, "skipped", TITLE_NO, TITLE_PICKED)
    saved, late = [], 0
    for n, (doc, why) in enumerate(todo):
        if ctx.time_left() <= 0:
            late = len(todo) - n
            break
        trace.record("court_file", step="picked", doc=doc.get("id"), picked=why)
        status, detail, entry = await check_file(ctx, hit, same, case_ref, doc, about)
        row(ctx, doc, status, detail, why)
        if detail == TIME_LIMIT:
            late = len(todo) - n
            break
        if entry:
            saved.append(entry)
    if late:
        ctx.add_gap(TIME_GAP.format(late))
    return saved
