"""Tool 1: court records (CourtListener's RECAP archive of PACER dockets; DESIGN 4B.9), driven by the
main agent (user decision 2026-11-02): it chooses what to search, which filings to read, and decides
itself whether a case is about the expert. court_search -> cases and the filings that matched the
name; court_filings -> all public filings of a case; court_file -> one filing downloaded with its
text, for ask_pdf; court_verdict -> the agent's same-person decision, saved as the case's finding.

The search always holds the full name (gotcha 13); code drops and scores nothing. Rate limits, cache
and the token check stay in app/sources/courtlistener.py (gotchas 5, 11, 53). Search passages go to
the agent only, never stored (gotcha 15); a verdict's `why` must not copy them or the filings."""

import asyncio
import json
import time

from app import pdf_text, trace
from app.evidence import copied, refuse, squash
from app.sources import SourceFailed, SourceSkipped, courtlistener, fold
from app.tools import RESULT_MAX, Ctx, tool

WORDS_MAX = 200
CASE_NAME_MAX = 200
TITLE_SHOWN = 100
PASSAGE_SHOWN = 300
REASON_MAX = 300
FILES_KEPT = 10  # downloaded filings listed in a verdict's facts
NAME_PAGES_MAX = 20
REPLY_BUDGET = RESULT_MAX - 300  # room for the counts next to the list
ROLES = ("expert witness", "consultant or reviewer", "party", "judge", "lawyer", "other witness",
         "mentioned only", "unclear")
HIGH_SCORE, MEDIUM_SCORE, LOW_SCORE = 75, 40, 25
TEXT_LAYER, COURTLISTENER_OCR, LOCAL_OCR = "text layer", "courtlistener ocr", "local ocr"
# Fixed texts (never source or model text)
TOO_LONG = f"words and case_name: at most {WORDS_MAX} characters each"
NONE_GAP = "no federal court filings in CourtListener (RECAP) name this person"
MORE_GAP = "court filings of {} past the first {} not listed"
UNKNOWN_CASE = "unknown docket_id: use one from a court_search reply"
UNKNOWN_FILING = "unknown document_id: use one from a court_search or court_filings reply"
NOT_AVAILABLE = "this filing is not public on CourtListener (nobody bought it from PACER): it cannot be downloaded"
NO_TEXT = "no text could be read from this filing"
OCR_TIME = "the run's time ran out while reading this scanned filing"
BAD_ROLE = f"role must be one of: {', '.join(ROLES)}"
NO_WHY = "why must say in your own words what matched or conflicted"
COPIED_WHY = "why copies text from the passages or filings: say it in your own words"
NOT_SAVED = "the verdict was not saved (it looked like a secret): reword why"
LOW_NOTE = "may be another person with the same name; verify it is the same person"
RULED_OUT = "another person with the same name (ruled out)"
UNJUDGED_GAP = "court case {} was read but not judged (no court_verdict)"


def level_of(score: int) -> str:
    return ("high" if score >= HIGH_SCORE else "medium" if score >= MEDIUM_SCORE
            else "low" if score >= LOW_SCORE else "ruled out")


def compact(item: dict) -> dict:
    """`item` without empty values: smaller replies."""
    return {k: v for k, v in item.items() if v not in (None, "", [])}


def fitted(items: list[dict]) -> tuple[list[dict], int]:
    """The first items that fit REPLY_BUDGET as JSON, and how many were left out."""
    shown, size = [], 2
    for item in items:
        size += len(json.dumps(item)) + 2
        if size > REPLY_BUDGET:
            break
        shown.append(item)
    return shown, len(items) - len(shown)


def remember(ctx: Ctx, docket_id: int, docs: list[dict]) -> None:
    """Filings shown to the agent: their ids work in court_file; an older entry keeps its file_id."""
    for doc in docs:
        ctx.run.filings[doc["id"]] = ctx.run.filings.get(doc["id"], {}) | doc | {"docket_id": docket_id}


def title(doc: dict) -> str:
    return (doc.get("description") or "no description")[:TITLE_SHOWN]


def doc_number(doc: dict) -> str | None:
    n = doc.get("document_number")
    return f"{n}-{doc['attachment_number']}" if n and doc.get("attachment_number") else n


@tool(label="Court search", cap=5)
async def court_search(ctx: Ctx, words: str = "", case_name: str = "") -> dict:
    """Search federal court filings (CourtListener's RECAP archive of PACER dockets) for cases whose
    filings, parties or lawyers name the expert in full. The full name is always searched; `words`
    adds search words (e.g. `Medtronic`, `"motion to exclude"`, `declaration OR report`) and
    `case_name` limits it to one case (e.g. "Doe v. Example Co."). Reply: `cases` (first 20 hits,
    best match first), each with `docket_id`, case name, court, docket number, date filed, nature of
    suit, `named_as` (the docket lists the expert as party or lawyer) and up to 3 `filings` that
    matched: `document_id`, title, pages, `available` (can be downloaded) and `passage` (the search's
    text around the name). Many people share a name: nothing is saved here. Judge each case yourself:
    read the passages, download the filings that may be the expert's (court_file; court_filings lists
    all of a case's filings, e.g. to find their own report) and read them with ask_pdf, then record
    your decision with court_verdict. Federal cases only, and only filings someone bought from PACER:
    an empty result does not mean the expert never testified. CourtListener allows 50 calls an hour."""
    if len(words) > WORDS_MAX or len(case_name) > CASE_NAME_MAX:
        return refuse(TOO_LONG)
    hits = await courtlistener.search_dockets(ctx.expert.display_name, words, case_name)
    if not hits and not words.strip() and not case_name.strip():
        ctx.add_gap(NONE_GAP)
    cases = []
    for hit in hits:
        if hit["docket_id"] is None:
            continue  # no id to read its filings or record a verdict with
        ctx.run.cases[hit["docket_id"]] = hit
        remember(ctx, hit["docket_id"], hit["documents"])
        cases.append(compact({
            "docket_id": hit["docket_id"], "case_name": hit["case_name"], "court": hit["court"],
            "docket_number": hit["docket_number"], "date_filed": hit["date_filed"],
            "nature_of_suit": hit["nature_of_suit"], "named_as": hit["named_as"],
            "filings": [compact({"document_id": d["id"], "title": title(d), "pages": d["page_count"],
                                 "available": d["available"], "passage": d["passage"][:PASSAGE_SHOWN]})
                        for d in hit["documents"]],
            "more_matched_filings": hit["more_filings"] or None}))
    shown, not_shown = fitted(cases)
    ctx.log(f"{len(cases)} cases found" + (f", {not_shown} not shown" if not_shown else ""))
    return {"cases": shown} | ({"not_shown": not_shown} if not_shown else {})


@tool(label="Court case filings", cap=5)
async def court_filings(ctx: Ctx, docket_id: int) -> dict:
    """All public (downloadable) filings of one case from court_search, in docket order, up to 100:
    `document_id`, title, document number, date filed and pages. Use it to find the expert's own
    filings in a case (report, declaration, deposition, CV) or a motion or ruling about their
    testimony, then download them with court_file. Costs one CourtListener call per 20 filings."""
    case = ctx.run.cases.get(docket_id)
    if case is None:
        return refuse(UNKNOWN_CASE)
    docs, more_left = await courtlistener.docket_documents(docket_id)
    remember(ctx, docket_id, docs)
    if more_left:
        ctx.add_gap(MORE_GAP.format(case["case_name"], len(docs)))
    shown, not_shown = fitted([compact({"document_id": d["id"], "title": title(d), "number": doc_number(d),
                                        "date_filed": d["date_filed"], "pages": d["page_count"]}) for d in docs])
    ctx.log(f"{len(docs)} filings" + (f", {not_shown} not shown" if not_shown else ""))
    return ({"docket_id": docket_id, "filings": shown} | ({"not_shown": not_shown} if not_shown else {})
            | ({"more_not_listed": True} if more_left else {}))


def numbered(pages: list[str]) -> str:
    """Every page after a "[Page N]" line: the saved text version ask_pdf falls back to for scans."""
    return "\n".join(f"[Page {n}]\n{page}" for n, page in enumerate(pages, 1))


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


@tool(label="Court file", cap=10)
async def court_file(ctx: Ctx, document_id: int) -> dict:
    """Download one court filing (a `document_id` from court_search or court_filings) and save it with
    its text; scanned filings are read with OCR. Reply: `file_id` to read with ask_pdf, `pages`, and
    `name_on_pages`: the pages that name the expert in full; read those first to see who the person
    is and their role in the case. The same filing twice gives the same file_id."""
    doc = ctx.run.filings.get(document_id)
    if doc is None:
        return refuse(UNKNOWN_FILING)
    if not doc["available"]:
        return refuse(NOT_AVAILABLE)
    if doc.get("file_id"):
        ctx.log("already downloaded")
        return {"file_id": doc["file_id"], "title": title(doc), "pages": doc["pages"],
                "name_on_pages": doc["name_on_pages"], "already_downloaded": True}
    folder = ctx.files_folder() / "tmp"
    folder.mkdir(exist_ok=True)
    tmp = folder / f"{document_id}.pdf"
    try:
        await courtlistener.download_document(doc["filepath_local"], tmp)
        try:
            pages, source, finished = await read_text(ctx, doc, tmp)
        except pdf_text.PdfUnreadable:
            return refuse(pdf_text.UNREADABLE)
        if not finished:
            return refuse(OCR_TIME)
        if not "".join(pages).strip():
            return refuse(NO_TEXT)
        file_id = await ctx.keep_file(doc["url"], tmp, "application/pdf", numbered(pages))
    finally:
        tmp.unlink(missing_ok=True)  # gone already when kept (moved by keep_file)
    pattern = courtlistener.name_pattern(ctx.expert.display_name)
    on = [n for n, page in enumerate(pages, 1) if pattern and pattern.search(fold(page))][:NAME_PAGES_MAX]
    doc |= {"file_id": file_id, "pages": len(pages), "name_on_pages": on}
    ctx.log(f"{len(pages)} pages, {source}, name on {len(on)} pages")
    return {"file_id": file_id, "title": title(doc), "pages": len(pages), "text_source": source,
            "name_on_pages": on}


@tool(label="Court case verdict", cap=10)
async def court_verdict(ctx: Ctx, docket_id: int, same_person: int, role: str, why: str) -> dict:
    """Record your decision on one case from court_search: is the person named in it this expert?
    Call it for every case you looked into, also when it is someone else (the profile then lists it
    as ruled out). `same_person` 0-100: 75+ sure it is the expert, 40-74 likely, 25-39 unsure, below
    25 someone else. `role`: the person's role in the case, one of: expert witness, consultant or
    reviewer, party, judge, lawyer, other witness, mentioned only, unclear. `why`: one or two
    sentences in your own words naming what matched or conflicted (profession, specialty, employer,
    place, kind of case); never quote the filings. The case is saved with the filings you downloaded
    from it. Call it as soon as you have decided, not at the end: a case without a verdict when the
    run stops is lost. One verdict per case: a second call returns the first."""
    case = ctx.run.cases.get(docket_id)
    if case is None:
        return refuse(UNKNOWN_CASE)
    if old := next((f for f in ctx.findings("court_docket") if f.url == case["url"]), None):
        ctx.log(f"already decided ({old.finding_id})")
        return {"ref": old.finding_id, "level": old.facts.get("match", {}).get("level"), "already_decided": True}
    if role not in ROLES:
        return refuse(BAD_ROLE)
    why = " ".join(why.split())[:REASON_MAX]
    if not why:
        return refuse(NO_WHY)
    filings = [d for d in ctx.run.filings.values() if d["docket_id"] == docket_id]
    read = [d for d in filings if d.get("file_id")]
    sources = " ".join([d.get("passage") or "" for d in filings] + [ctx.file_text(d["file_id"]) for d in read])
    if copied(why, squash(sources)):  # gotcha 15: the finding holds our own words only
        return refuse(COPIED_WHY)
    score = max(0, min(100, same_person))
    level = level_of(score)
    facts = {k: case[k] for k in ("case_name", "court", "docket_number", "date_filed", "nature_of_suit", "named_as")}
    facts |= {"files": [{"file_id": d["file_id"], "title": title(d)} for d in read[:FILES_KEPT]],
              "match": {"score": score, "level": level, "role": role, "reasons": [why], "checked_by": "agent"}}
    ref = ctx.add_finding("court_docket", case["case_name"], case["url"], facts=facts,
                          confidence="high" if level == "high" else "low",
                          note={"low": LOW_NOTE, "ruled out": RULED_OUT}.get(level),
                          source_type="courtlistener_docket")
    if ref is None:
        return refuse(NOT_SAVED)
    ctx.log(f"{level}, {role}")
    return {"ref": ref, "level": level}


def gap_unjudged(ctx: Ctx) -> None:
    """At the end of a run, however it ended: a gap for each case with downloaded filings but no
    verdict, so work the agent never recorded is not lost silently."""
    judged = {f.url for f in ctx.findings("court_docket")}
    read = dict.fromkeys(d["docket_id"] for d in ctx.run.filings.values() if d.get("file_id"))
    for case in (ctx.run.cases[n] for n in read):
        if case["url"] not in judged:
            ctx.add_gap(UNJUDGED_GAP.format(case["case_name"]))
