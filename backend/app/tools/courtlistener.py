"""Tool 1: court records (CourtListener's RECAP archive of PACER dockets; DESIGN 4B.9). The main agent
calls court_research; its court-records helper (a sub-agent, user decision 2026-11-02) chooses what
to search, which filings to read, and decides itself whether a case is about the expert, with five
helper-only tools: court_search -> cases and the filings that matched the name; court_filings -> all
public filings of a case; court_file -> one filing downloaded with its text, for ask_pdf;
court_verdict -> the same-person decision, saved as the case's finding; pay_for_document -> a request
to buy a filing that is not public (app/purchases.py decides). court_research replies with
what was saved plus the helper's report (cases, leads, what it searched).

The search always holds the full name (gotcha 13); code drops and scores nothing. Rate limits, cache
and the token check stay in app/sources/courtlistener.py (gotchas 5, 11, 53). Search passages go to
the agent only, never stored (gotcha 15); a verdict's `why` must not copy them or the filings."""

import asyncio
import json
import time

from app import llm, pdf_text, purchases, trace
from app.evidence import copied, refuse, squash
from app.profile import known_so_far
from app.sources import SourceFailed, SourceSkipped, courtlistener, fold
from app.tools import RESULT_MAX, Ctx, tool

WORDS_MAX = 200
CASE_NAME_MAX = 200
TITLE_SHOWN = 100
PASSAGE_SHOWN = 300
REASON_MAX = 300
PAY_WHY_MAX = 300
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
NO_SEARCH = "more=true continues a search: call court_search with these words and case_name first"
UNKNOWN_CASE = "unknown docket_id: use one from a court_search reply"
UNKNOWN_FILING = "unknown document_id: use one from a court_search or court_filings reply"
NOT_AVAILABLE = ("this filing is not public on CourtListener (nobody bought it from PACER): it cannot be "
                 "downloaded; if it matters, ask for it with pay_for_document")
PAY_WHY = f"why: say in at most {PAY_WHY_MAX} characters what this filing would add"
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


def shown_case(hit: dict) -> dict:
    return compact({
        "docket_id": hit["docket_id"], "case_name": hit["case_name"], "court": hit["court"],
        "docket_number": hit["docket_number"], "date_filed": hit["date_filed"],
        "nature_of_suit": hit["nature_of_suit"], "named_as": hit["named_as"],
        "filings": [compact({"document_id": d["id"], "title": title(d), "pages": d["page_count"],
                             "available": d["available"], "passage": d["passage"][:PASSAGE_SHOWN]})
                    for d in hit["documents"]],
        "more_matched_filings": hit["more_filings"] or None})


@tool(label="Court search", cap=5, main=False)
async def court_search(ctx: Ctx, words: str = "", case_name: str = "", more: bool = False) -> dict:
    """Search federal court filings (CourtListener's RECAP archive of PACER dockets) for cases whose
    filings, parties or lawyers name the expert in full. The full name is always part of the search:
    never put it in `words`. `words` only filters those cases (every word must appear), it never finds
    a case the plain search does not have; `case_name` limits the search to one case (e.g. "Doe v.
    Example Co."). Reply: `total` cases, `cases` (best match first) and `more`: true while some are not
    shown yet; call again with the same words and case_name and more=true for the next ones. Each case
    has `docket_id`, case name, court, docket number, date filed, nature of suit, `named_as` (the
    docket lists the expert as party or lawyer) and up to 3 `filings` that matched: `document_id`,
    title, pages, `available` (can be downloaded) and `passage` (the search's text around the
    match). Many people share a name: nothing is saved here. Federal cases only, and only filings
    someone bought from PACER: an empty result does not mean the expert never testified.
    CourtListener allows 50 calls an hour."""
    if len(words) > WORDS_MAX or len(case_name) > CASE_NAME_MAX:
        return refuse(TOO_LONG)
    key, name = (words.strip(), case_name.strip()), ctx.expert.display_name
    search = ctx.run.searches.get(key) if more else None
    if more and search is None:
        return refuse(NO_SEARCH)
    if search is None:
        hits, total, nxt = await courtlistener.search_dockets(name, *key)
        search = ctx.run.searches[key] = {"queue": hits, "next": nxt, "total": total}
        if not total and key == ("", ""):
            ctx.add_gap(NONE_GAP)
    elif not search["queue"] and search["next"]:
        search["queue"], _, search["next"] = await courtlistener.search_dockets(name, next_url=search["next"])
    hits = [hit for hit in search["queue"] if hit["docket_id"] is not None]  # no id: nothing to call later
    for hit in hits:
        ctx.run.cases[hit["docket_id"]] = hit
        remember(ctx, hit["docket_id"], hit["documents"])
    shown, _ = fitted([shown_case(hit) for hit in hits])
    search["queue"] = hits[len(shown):]
    left = bool(search["queue"] or search["next"])
    ctx.log(f"{len(shown)} of {search['total']} cases shown" + (", more left" if left else ""))
    return {"total": search["total"], "cases": shown, "more": left}


@tool(label="Court case filings", cap=5, main=False)
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


@tool(label="Buy a filing", cap=2, main=False)
async def pay_for_document(ctx: Ctx, document_id: int, why: str) -> dict:
    """Ask to buy one court filing that is not public (`available` false), a `document_id` from
    court_search or court_filings, when it matters: the expert's own report, declaration, deposition
    or CV, or a motion or ruling about their testimony. `why`: what it would add, in a sentence. A
    separate payment service decides; it reads the price, title and case from CourtListener, not from
    you. Reply: `decision` (approved or rejected) and `reason`: dry_run (approved, but purchases are
    off: not bought), card_ready (approved, a one-time card was made, but checkout is not built yet:
    not bought), free_copy_found (it is public: use court_file), over_price, over_budget (the run's
    limit), low_value (not worth it), judge_failed or payment_failed (ask again later). A rejection is
    final for this filing: move on."""
    doc = ctx.run.filings.get(document_id)
    if doc is None:
        return refuse(UNKNOWN_FILING)
    if not why.strip() or len(why) > PAY_WHY_MAX:
        return refuse(PAY_WHY)
    case = (ctx.run.cases.get(doc.get("docket_id")) or {}).get("case_name") or "unknown case"
    return await purchases.request(ctx, doc, case)


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


@tool(label="Court file", cap=10, main=False)
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


@tool(label="Court case verdict", cap=10, main=False)
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


def unjudged(ctx: Ctx) -> list[str]:
    """Names of the cases with downloaded filings but no verdict."""
    judged = {f.url for f in ctx.findings("court_docket")}
    read = dict.fromkeys(d["docket_id"] for d in ctx.run.filings.values() if d.get("file_id"))
    return [case["case_name"] for case in (ctx.run.cases[n] for n in read) if case["url"] not in judged]


def gap_unjudged(ctx: Ctx) -> None:
    """At the end of a run, however it ended: a gap for each case read but never judged, so work
    that was not recorded is not lost silently."""
    for case_name in unjudged(ctx):
        ctx.add_gap(UNJUDGED_GAP.format(case_name))


# The court-records helper (a sub-agent run by court_research).
HELPER_TOOLS = ["court_search", "court_filings", "court_file", "ask_pdf", "court_verdict", "pay_for_document"]
HELPER_CALLS = 80  # model calls per court_research; the report after it is one more
FOCUS_MAX = 500
CONTEXT_MAX = 3_000
RULED_OUT_SHOWN = 20
HELPER_INSTRUCTIONS = """You are the court-records researcher for one expert witness. You search
CourtListener (federal court filings bought from PACER) for every federal case where this expert
appears, decide which cases are really theirs, learn what each says about them, and report back to
the lead researcher. You get what the research knows about the expert and what the lead researcher
asks for this time.

Work in batches: your turns are limited, and one turn can hold many tool calls. Put every call you
can make now into the same turn: all the verdicts you have decided, all the filings worth
downloading, all the ask_pdf questions on files you have. One call per turn runs out of turns
before every case is judged.

How to work:
1. Search. court_search already searches the expert's full name: never put the name in `words`.
Search once with no words and read every page: while the reply says `more`, call court_search again
with more=true. Extra words only filter the cases you already get (every word must appear), so they
never find a new case; use one or two only when `total` is too large to read (over 100). Use
case_name only for a case you were asked about or a filing names, and never search again for a case
you already have. CourtListener allows 50 calls an hour for the whole app.
2. Sort the results. Many people share a name. Compare each case with what we know: court and
state, kind of case (nature of suit), dates, filing titles and passages, and whether the docket lists
the person as a party or lawyer. A clearly different person (another first, middle or last name
such as "Alex Rivera Smith" for Alex Rivera, a debtor in a personal bankruptcy, a lawyer, court
staff, a different profession) is ruled out at once with court_verdict; download nothing for it.
3. Read the cases that may be the expert's. Filing titles naming the expert, expert reports,
declarations, depositions, and motions or orders to exclude, limit or strike an expert are the best
clues. Download a matched filing with court_file when it is available; when none is, list the case's
public filings (court_filings) and pick the court's ruling on the expert or the expert's own filing.
A key filing that is not available (the expert's report or deposition, or a ruling on their
testimony): ask for it with pay_for_document and say why; it may be refused, then move on. With ask_pdf read only the pages court_file says name the expert (name_on_pages), with one page
before and after, never the whole file, and give the expert's details as context. Find out: is it
the expert, their role, which side hired them, what they gave (report, declaration, deposition,
trial testimony), fees, and how the court ruled on their testimony.
4. Decide and record. Call court_verdict for each case as soon as you have decided: it is the only
thing saved. Before any new search, record a verdict for every case whose filings you have read.
5. Follow clues. When a filing names another case the expert worked on, search it with case_name.
Co-experts, law firms, parties and employers are leads for the report, not CourtListener searches.
Stop when every case is judged. Professional records only: never private life, family, home
address or health. When you stop, reply in one line; the report for the lead researcher is written
from your work afterwards."""

REPORT_INSTRUCTIONS = """You write the report of a court-records search for the lead researcher of
an expert witness. You get the task and everything the court-records helper did: its words, its tool
calls and their results. Write in your own words, never copy the filings, in four parts:
Cases: each case judged to be the expert's: name, court, docket number, year, role and side, what
they did, the ruling on their testimony, fees, and the file_ids worth reading further.
Ruled out: one line on the people with the same name.
Leads: things worth searching outside CourtListener (state cases, other forms of the name, law
firms, parties, companies, case numbers) and why.
Searched: what was searched and what it found, so it is not repeated. If the helper stopped before
judging every case, name the cases left and what is known about each."""


async def write_report(ctx: Ctx, prompt: str, run) -> str:
    """The report for the main agent: one model call after the helper stops, however it stopped
    (not counted in HELPER_CALLS), written from the helper's whole run."""
    # ponytail: the whole transcript goes in one call; cut it if a run outgrows the model's context
    work = (f"The task:\n{prompt}\n\nThe helper's work ({run.stopped or 'finished'}, "
            f"{run.calls} model calls):\n" + "\n".join(run.transcript))
    out = await ctx.sub_agent(REPORT_INSTRUCTIONS, work, max_llm_calls=1, role="reporter")
    return out.text.strip() or f"no report ({out.stopped or 'empty reply'})"


def case_line(f) -> dict:
    """A saved verdict for the court_research reply: fields copied by code from its finding."""
    facts, match = f.facts, f.facts.get("match") or {}
    return compact({"ref": f.finding_id, "case_name": f.title, "court": facts.get("court"),
                    "docket_number": facts.get("docket_number"), "date_filed": facts.get("date_filed"),
                    "level": match.get("level"), "role": match.get("role"),
                    "why": ((match.get("reasons") or [""])[0])[:200],
                    "file_ids": [x["file_id"] for x in facts.get("files") or []]})


def fit_report(reply: dict) -> dict:
    """`reply` within RESULT_MAX: the lists first (each item is saved anyway, read_profile shows them
    all), then the report cut to the room left."""
    for key, keep in (("ruled_out", 5), ("files", 5), ("cases", 8)):
        if len(json.dumps(reply | {"report": ""})) <= RESULT_MAX:
            break
        reply[key] = reply[key][:keep]
    while len(json.dumps(reply)) > RESULT_MAX and reply["report"]:
        reply["report"] = reply["report"][:len(reply["report"]) * 9 // 10]
    return reply


@tool(label="Court research", cap=3)
async def court_research(ctx: Ctx, focus: str = "", context: str = "") -> dict:
    """Research the expert in federal court records (CourtListener: filings bought from PACER) with a
    court-records helper. It searches, sorts out people with the same name, downloads and reads the
    filings that may be the expert's, and saves each case it judges (the profile then shows them).
    `focus`: what to look for this time; empty = every federal case naming the expert, or name cases
    you found elsewhere (e.g. "Doe v. Example Co., D. Minn. 0:00-cv-00000"). `context`: what you know
    that helps tell the expert from people with the same name and is not in the profile yet (e.g.
    the CV's case list with the expert's side, employers, other forms of the name). Reply: `cases`
    saved as the expert's (level, role, why, file_ids), `ruled_out` (same-name people), `files`
    downloaded (file_id, title, case, pages naming the expert: read more with ask_pdf), `unjudged`
    (cases read but not decided) and `report`: the helper's account of the cases, leads to follow
    outside CourtListener, and what it searched. Takes a few minutes. Federal cases only: an empty
    result does not mean the expert never testified."""
    if len(focus) > FOCUS_MAX or len(context) > CONTEXT_MAX:
        return refuse(f"focus: at most {FOCUS_MAX} characters, context: at most {CONTEXT_MAX}")
    before = {f.finding_id for f in ctx.findings("court_docket")}
    had = {n for n, d in ctx.run.filings.items() if d.get("file_id")}
    prompt = (f"What the research knows about the expert:\n{known_so_far(ctx)}\n\n"
              f"The lead researcher asks: {focus.strip() or 'find every federal case naming the expert'}\n"
              f"What they know that may help: {context.strip() or 'nothing more'}")
    try:
        run = await ctx.sub_agent(HELPER_INSTRUCTIONS, prompt, tools=HELPER_TOOLS, max_llm_calls=HELPER_CALLS)
        report = await write_report(ctx, prompt, run)
    except llm.ModelError as e:  # what it saved before the failure still counts
        report = f"the helper stopped early ({e}); what it saved is listed"
        ctx.add_gap(f"court research stopped early ({e})")
    new = [f for f in ctx.findings("court_docket") if f.finding_id not in before]
    theirs = [case_line(f) for f in new if (f.facts.get("match") or {}).get("level") != "ruled out"]
    ruled_out = [f.title[:60] for f in new if (f.facts.get("match") or {}).get("level") == "ruled out"]
    files = [{"file_id": d["file_id"], "title": title(d)[:60], "case": ctx.run.cases[d["docket_id"]]["case_name"][:60],
              "name_on_pages": d["name_on_pages"]}
             for n, d in ctx.run.filings.items() if d.get("file_id") and n not in had]
    ctx.log(f"{len(theirs)} cases, {len(ruled_out)} ruled out, {len(files)} files")
    return fit_report({"cases": theirs, "ruled_out": ruled_out[:RULED_OUT_SHOWN], "files": files,
                       "unjudged": unjudged(ctx), "report": report})
