"""Research collectors (DESIGN 4.2-4.4). Each returns (findings, gaps) and never raises: a source
that is skipped or fails becomes a gap and a skipped/failed job step."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import quote_plus, urlsplit

from google.adk.tools import FunctionTool

from app import llm, store
from app.evidence import Run, copied, refuse, squash, words
from app.models import Candidate, Finding, Gap
from app.sources import (
    SourceSkipped,
    courtlistener,
    crossref,
    firecrawl,
    fold,
    given_family,
    has_full_name,
    host_of,
    is_blocked,
    is_expert_directory,
    leie,
    name_words,
    on_domain,
    openalex,
    state_of,
    websearch,
)

Step = Callable[..., None]  # step(key, status, detail=None)
MAX_OPINIONS = 6
MAX_OPENALEX_IDS = 3
MAX_CROSSREF_DOIS = 50  # newest first; older works rely on OpenAlex's flag (PLAN M3 cuts)
STATE_COURTS_GAP = "CourtListener does not hold every state trial court ruling."
REASONS_GAP = "Retraction reasons are not collected (the Retraction Watch CSV is not used)."
LEIE_NOTE = "possible match by name only; verify at the OIG online search"
STEM = 6  # ponytail: crude stemming, a shared 6-letter prefix counts ("chiropractic" ~ "chiropractor")


def failure(label: str, exc: Exception) -> str:
    """Short generic reason: never the raw error or model text."""
    return f"{label} timed out" if isinstance(exc, TimeoutError) else f"{label} failed ({type(exc).__name__})"


async def safe(key: str, label: str, gaps: list[Gap], call: Callable[[], Awaitable]) -> tuple:
    """Await call(). A skip or error adds a gap and returns (None, "skipped" | "failed")."""
    try:
        return await call(), None
    except SourceSkipped as e:
        gaps.append(Gap(step=key, reason=str(e) or f"{label} skipped"))
        return None, "skipped"
    except Exception as e:  # noqa: BLE001 - a failing source is a gap, never a crash (M3 3d)
        gaps.append(Gap(step=key, reason=failure(label, e)))
        return None, "failed"


async def run_step(key: str, label: str, step: Step, gaps: list[Gap], call) -> list[Finding]:
    """One job step: `call` sets the step done itself; a skip or error marks it skipped/failed."""
    step(key, "running")
    found, status = await safe(key, label, gaps, call)
    if status:
        step(key, status, gaps[-1].reason)
    return found or []


def web_findings(results: list[dict], collector: str, kind: str, note: str) -> list[Finding]:
    """Web results -> low-confidence findings. Only URL, title and our extracted facts are kept,
    never a snippet (gotcha 15); blocked domains are dropped again here (gotcha 2)."""
    at = store.now()
    return [
        Finding(collector=collector, kind=kind, title=r.get("title") or r["url"], url=r["url"],
                source_type="web", retrieved_at=at, confidence="low",
                facts={"facts": [str(f) for f in r.get("facts") or []]}, note=note)
        for r in results
        if r.get("url") and not is_blocked(r["url"])
    ]


def context_of(candidate: Candidate) -> str | None:
    """Field and location, so the web search targets this person and not a same-name other."""
    return ", ".join(x for x in (candidate.field, candidate.location) if x) or None


async def web_step(key: str, step: Step, gaps: list[Gap], candidate: Candidate, queries: list[str], topic: str,
                   collector: str, kind: str, note: str) -> list[Finding]:
    async def call():
        results = await websearch.search_facts(candidate.display_name, queries, topic, context=context_of(candidate))
        found = web_findings(results, collector, kind, note)
        if not found:
            gaps.append(Gap(step=key, reason="no web results"))
        step(key, "done", f"{len(found)} pages")
        return found

    return await run_step(key, "Web search", step, gaps, call)


# Testimony (DESIGN 4.2)


def plain_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def newest_opinions(hits: list[dict], evidence: set[str] = frozenset()) -> list[dict]:
    """Up to MAX_OPINIONS hits with distinct opinions: those in the candidate's evidence first, then
    newest filing date first."""
    seen, picked = set(), []
    for hit in sorted(hits, key=lambda h: (h.get("case") or ("", "", ""))[2] or "", reverse=True):
        key = hit.get("opinion_id") or hit["url"]
        if key not in seen:
            seen.add(key)
            picked.append(hit)
    picked.sort(key=lambda h: plain_url(h["url"]) not in evidence)  # stable: dates stay in order
    return picked[:MAX_OPINIONS]


def mentions_field(passages: list[str], field: str | None) -> bool:
    """True if a passage around the name shares a specialty word (or its STEM prefix) with the field."""
    wanted = {w[:STEM] for w in words(field)}
    return bool(wanted) and any(w[:STEM] in wanted for w in words(" ".join(passages)))


async def collect_testimony(candidate: Candidate, step: Step) -> tuple[list[Finding], list[Gap]]:
    name = candidate.display_name
    gaps = [Gap(step="courtlistener", reason=STATE_COURTS_GAP)]
    evidence = {plain_url(e.url) for e in candidate.evidence}

    async def court():
        hits = await courtlistener.search_opinions(name)
        if not hits:
            gaps.append(Gap(step="courtlistener", reason="no court opinions name this person as an expert"))
        found, missing, skipped = [], 0, 0
        for hit in newest_opinions(hits, evidence):
            case_name, court_name, date_filed = hit.get("case") or ("", "", "")
            passages, note = [], None
            try:
                if hit.get("opinion_id") is None:
                    raise ValueError("no opinion id")
                passages = await courtlistener.opinion_passages(hit["opinion_id"], name)
            except Exception:  # noqa: BLE001 - the case is still a finding, just without passages
                missing += 1
                note = "opinion text not fetched; no passages"
            # Same-name people are common in opinions: keep only what the picked search tied to this
            # person (evidence) or what names this person's field near the name.
            if plain_url(hit["url"]) not in evidence and not mentions_field(passages, candidate.field):
                skipped += 1
                continue
            # `snippet` is never stored (gotcha 15): only fields copied by code plus our passages
            found.append(Finding(
                collector="testimony", kind="court_opinion", title=hit.get("title") or "Court opinion",
                url=hit["url"], source_type="courtlistener_opinion", retrieved_at=store.now(),
                confidence="high", note=note,
                facts={"case_name": case_name, "court": court_name, "date_filed": date_filed,
                       "passages": list(passages)},
            ))
        if missing:
            gaps.append(Gap(step="courtlistener", reason=f"opinion text not fetched for {missing} opinions"))
        if skipped:
            gaps.append(Gap(step="courtlistener",
                            reason=f"{skipped} same-name court opinions skipped: not tied to this person"))
        step("courtlistener", "done", f"{len(found)} opinions")
        return found

    field = f" {candidate.field}" if candidate.field else ""
    queries = [f'"{name}" expert witness testimony{field}',
               f'"{name}" deposition OR testified OR "expert report"{field}']
    court_found, web_found = await asyncio.gather(
        run_step("courtlistener", "CourtListener", step, gaps, court),
        web_step("web_testimony", step, gaps, candidate, queries, "expert testimony",
                 "testimony", "web_testimony", "verify"),
    )
    return court_found + web_found, gaps


# Publications (DESIGN 4.3)


def work_key(work: dict) -> str:
    return (work.get("doi") or "").lower() or work["id"]


def work_url(work: dict) -> str:
    return work.get("url") or (f"https://doi.org/{work['doi']}" if work.get("doi") else work["id"])


async def collect_publications(candidate: Candidate, step: Step) -> tuple[list[Finding], list[Gap]]:
    gaps = [Gap(step="crossref", reason=REASONS_GAP)]
    ids = candidate.identifiers.openalex[:MAX_OPENALEX_IDS]
    if not ids:
        gaps.append(Gap(step="openalex", reason="no OpenAlex id for this candidate"))
        step("openalex", "skipped", "no OpenAlex id")
        step("crossref", "skipped", "no works to check")
        return [], gaps
    works: dict[str, dict] = {}

    async def authors_and_works():
        found, statuses = [], []
        for aid in ids:
            author, status = await safe("openalex", "OpenAlex", gaps, lambda aid=aid: openalex.author(aid))
            statuses.append(status)
            if author:
                found.append(Finding(
                    collector="publications", kind="author_stats",
                    title=f"OpenAlex author record: {author.get('display_name') or aid}",
                    url=author.get("url") or author["id"], source_type="openalex_author",
                    retrieved_at=store.now(), confidence="high",
                    facts={k: author.get(k) for k in ("id", "display_name", "works_count", "cited_by_count",
                                                      "h_index", "orcid", "last_institution")},
                ))
            listed, status = await safe("openalex", "OpenAlex", gaps,
                                        lambda aid=aid: openalex.works(aid, limit=100))
            statuses.append(status)
            for work in listed or []:
                works.setdefault(work_key(work), work)
        at = store.now()
        found += [
            Finding(collector="publications", kind="work", title=w.get("title") or "Untitled work",
                    url=work_url(w), source_type="openalex_work", retrieved_at=at, confidence="high",
                    facts={k: w.get(k) for k in ("id", "year", "venue", "doi", "cited_by_count", "is_retracted")})
            for w in works.values()
        ]
        if None not in statuses:  # every call failed or was skipped
            status = "skipped" if set(statuses) == {"skipped"} else "failed"
            step("openalex", status, gaps[-1].reason)
        else:
            step("openalex", "done", f"{len(works)} works")
        return found

    found = await run_step("openalex", "OpenAlex", step, gaps, authors_and_works)
    found += await run_step("crossref", "Crossref", step, gaps, lambda: retractions(works, step, gaps))
    return found, gaps


async def retractions(works: dict[str, dict], step: Step, gaps: list[Gap]) -> list[Finding]:
    """Crossref by DOI for the newest MAX_CROSSREF_DOIS works, one at a time (the limiter spaces
    calls); a work is also flagged when OpenAlex says is_retracted."""
    if not works:
        step("crossref", "skipped", "no works to check")
        return []
    newest = sorted(works.values(), key=lambda w: w.get("year") or 0, reverse=True)
    to_check = [w for w in newest if w.get("doi")][:MAX_CROSSREF_DOIS]
    unchecked = len(works) - len(to_check)
    if unchecked:
        gaps.append(Gap(step="crossref", reason=f"{unchecked} works not checked in Crossref (no DOI or beyond"
                                                f" the newest {MAX_CROSSREF_DOIS}); only OpenAlex's flag was read"))
    found, failed, skipped, flagged = [], 0, None, set()
    for work in to_check:
        try:
            hit = await crossref.retraction(work["doi"])
        except SourceSkipped as e:
            skipped = str(e) or "Crossref skipped"
            break
        except Exception:  # noqa: BLE001 - one failed DOI is counted, the rest still run
            failed += 1
            continue
        if hit:
            flagged.add(work_key(work))
            found.append(Finding(
                collector="publications", kind="retraction", title=work.get("title") or "Untitled work",
                url=hit.get("notice_url") or hit.get("url") or work_url(work), source_type="crossref_work",
                retrieved_at=store.now(), confidence="high",
                facts={"doi": hit.get("doi") or work["doi"], "date": hit.get("date"), "source": hit.get("source"),
                       "notice_url": hit.get("notice_url"), "openalex_is_retracted": bool(work.get("is_retracted"))},
            ))
    for work in newest:
        if work.get("is_retracted") and work_key(work) not in flagged:
            found.append(Finding(
                collector="publications", kind="retraction", title=work.get("title") or "Untitled work",
                url=work_url(work), source_type="openalex_work", retrieved_at=store.now(), confidence="high",
                facts={"doi": work.get("doi"), "flagged_by": "openalex"},
                note="flagged as retracted by OpenAlex; no Crossref notice found",
            ))
    if skipped:
        gaps.append(Gap(step="crossref", reason=skipped))
    if failed:
        gaps.append(Gap(step="crossref", reason=f"Crossref check failed for {failed} DOIs"))
    if to_check and failed == len(to_check):
        step("crossref", "failed", f"Crossref check failed for {failed} DOIs")
    elif skipped and not found:
        step("crossref", "skipped", skipped)
    else:
        step("crossref", "done", f"{len(found)} retractions")
    return found


# Credentials (DESIGN 4.4)


def middle_initial(name: str) -> str | None:
    words = name_words(name)
    return words[1][0].upper() if len(words) > 2 else None


async def collect_credentials(candidate: Candidate, step: Step) -> tuple[list[Finding], list[Gap]]:
    name = candidate.display_name
    gaps: list[Gap] = []

    async def exclusions():
        first, last = given_family(name)
        if not first or first == last:
            raise SourceSkipped("need a given and a family name for the LEIE check")
        matches = await leie.possible_matches(first, last, middle_initial=middle_initial(name))
        if state := state_of(candidate.location):  # unknown state: every name match stays possible
            other = [m for m in matches if m.get("state") and str(m["state"]).strip().upper() != state]
            matches = [m for m in matches if m not in other]
            if other:
                gaps.append(Gap(step="oig_leie", reason=f"{len(other)} OIG exclusion list name matches skipped:"
                                                        f" listed in another state than {state}"))
        at = store.now()
        found = [
            Finding(collector="credentials", kind="leie_possible_match",
                    title=f"OIG exclusion list: possible match ({m.get('general') or 'unknown category'})",
                    url=m["url"], source_type="oig_leie", retrieved_at=at, confidence="low", note=LEIE_NOTE,
                    facts={k: m.get(k) for k in ("general", "specialty", "excltype", "excldate", "reindate",
                                                 "state")})
            for m in matches
        ]
        step("oig_leie", "done", f"{len(found)} possible matches")
        return found

    field = f" {candidate.field}" if candidate.field else ""
    queries = [f'"{name}"{field} license', f'"{name}" board certification OR disciplinary action']
    leie_found, web_found = await asyncio.gather(
        run_step("oig_leie", "OIG LEIE", step, gaps, exclusions),
        web_step("web_credentials", step, gaps, candidate, queries, "licences, board certification, discipline",
                 "credentials", "web_credential", "verify manually at the official board site"),
    )
    return leie_found + web_found, gaps


# Expert's own pages (PLAN M3 3j): pages about this person read with Firecrawl. Page text stays in
# memory for one AI call; only the cases and short facts the AI extracts are stored.

MAX_PAGES = 5
PAGE_TEXT_MAX = 40_000  # characters of page markdown sent to the AI (prompt only, never stored)
MAX_PAGE_CASES = 25  # per page
MAX_PAGE_FACTS = 8  # per page
FACT_MAX = 200
CASE_NAME_MAX = 200
COURT_MAX = 120
ROLES = ("deposition", "trial", "report", "unknown")
SIDES = ("plaintiff", "defense", "prosecution", "unknown")
PAGE_CASE_NOTE = "listed by the expert or a directory; verify"
PAGE_CREDENTIAL_NOTE = "from the expert's own page or a directory; verify manually at the official board site"
CV_PATH = re.compile(r"(^|[/_.-])(cv|vitae|resume|curriculum)([/_.-]|$)", re.IGNORECASE)

PAGE_INSTRUCTIONS = (
    "You read one web page (as markdown) for a legal research tool that checks an expert witness. "
    "Say whether the page is about the named person (same person, also in field and place when given). "
    "If it is, list (1) the court cases the page says this person gave expert testimony in: case name, "
    "court, year, the side they worked for if stated, and the role (deposition, trial, report, or "
    "unknown); and (2) up to 8 short facts about the person's professional credentials (degrees, "
    "licences, board certifications, positions, discipline). Write each credential fact in your own "
    "words in one short sentence; do not copy text. Use only what the page says; never add facts. "
    "Leave court or year empty when the page does not give them. Professional information only."
)

CASE_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["case_name", "court", "year"],
    "properties": {"case_name": {"type": "string"}, "court": {"type": "string"}, "year": {"type": "string"}},
}
PAGE_CASE_ITEM = CASE_ITEM | {
    "required": [*CASE_ITEM["required"], "side", "role"],
    "properties": CASE_ITEM["properties"] | {"side": {"type": "string", "enum": list(SIDES)},
                                              "role": {"type": "string", "enum": list(ROLES)}},
}
PAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["about_person", "cases", "credentials"],
    "properties": {
        "about_person": {"type": "boolean"},
        "cases": {"type": "array", "items": PAGE_CASE_ITEM},
        "credentials": {"type": "array", "items": {"type": "string"}},
    },
}


def one_line(value: object, limit: int) -> str | None:
    return " ".join(str(value or "").split())[:limit] or None


def clean_case(item: dict, page: bool = False) -> dict | None:
    """AI case item -> {case_name, court, year[, side, role]} with capped lengths; None if unusable."""
    case_name = one_line(item.get("case_name"), CASE_NAME_MAX)
    if not case_name or len(case_name) < 3:
        return None
    year = str(item.get("year") or "").strip()
    case = {"case_name": case_name, "court": one_line(item.get("court"), COURT_MAX),
            "year": year if re.fullmatch(r"(19|20)\d\d", year) else None}
    if page:
        case["side"] = item.get("side") if item.get("side") in SIDES else "unknown"
        case["role"] = item.get("role") if item.get("role") in ROLES else "unknown"
    return case


def page_rank(url: str, name: str) -> int:
    """0 the expert's own site (family name in the host), 1 a CV or PDF, 2 an expert directory, 3 other."""
    family = given_family(name)[1]
    if len(family) > 2 and family in fold(host_of(url) or ""):
        return 0
    path = urlsplit(url).path
    if path.lower().endswith(".pdf") or CV_PATH.search(path):
        return 1
    return 2 if is_expert_directory(url) else 3


def pick_pages(candidate: Candidate, found: list[Finding]) -> list[str]:
    """Up to MAX_PAGES URLs about this person from the candidate's web evidence and the web
    collectors' results, best kind first. Blocked domains (gotcha 2) are never picked."""
    urls = [e.url for e in candidate.evidence if e.source == "web"]
    urls += [f.url for f in found if f.kind in ("web_testimony", "web_credential")]
    seen, picked = set(), []
    for url in urls:
        if url.startswith(("http://", "https://")) and not is_blocked(url) and plain_url(url) not in seen:
            seen.add(plain_url(url))
            picked.append(url)
    picked.sort(key=lambda u: page_rank(u, candidate.display_name))  # stable: same kind keeps its order
    return picked[:MAX_PAGES]


async def read_one_page(url: str, candidate: Candidate) -> dict | None:
    """Scrape one page and extract cases and credential facts. None if the page is not about this
    person. The markdown never leaves this function."""
    name = candidate.display_name
    page = await firecrawl.scrape(url)  # refuses blocked sites itself, also after a redirect
    url = page.get("url") or url
    text = (page.get("markdown") or "")[:PAGE_TEXT_MAX]
    title = one_line(page.get("title"), websearch.TITLE_MAX) or url
    if not has_full_name(f"{title} {url} {text}", name):
        return None  # the page must name the person in full (gotcha 13)
    prompt = "\n".join([websearch.person_line(name, context_of(candidate)), f"Page: {title} | {url}",
                        "Page markdown:", text])
    data = (await llm.call_model(PAGE_INSTRUCTIONS, prompt, schema=PAGE_SCHEMA)).data or {}
    if not data.get("about_person"):
        return None
    cases, keys = [], set()
    for item in data.get("cases") or []:
        case = clean_case(item, page=True)
        if case and case_key(case["case_name"]) not in keys and len(cases) < MAX_PAGE_CASES:
            keys.add(case_key(case["case_name"]))
            cases.append(case)
    page_text = squash(text)
    facts = []
    for fact in data.get("credentials") or []:
        fact = one_line(fact, FACT_MAX)
        if fact and not copied(fact, page_text) and fact not in facts and len(facts) < MAX_PAGE_FACTS:
            facts.append(fact)
    return {"url": url, "title": title, "cases": cases, "facts": facts}


def page_findings(page: dict) -> list[Finding]:
    """One page read by read_one_page -> page_testimony_case findings plus one page_credential."""
    at = store.now()
    findings = [
        Finding(collector="testimony", kind="page_testimony_case", title=case["case_name"], url=page["url"],
                source_type="web", retrieved_at=at, confidence="low", facts=case, note=PAGE_CASE_NOTE)
        for case in page["cases"]
    ]
    if page["facts"]:
        findings.append(Finding(
            collector="credentials", kind="page_credential", title=page["title"], url=page["url"],
            source_type="web", retrieved_at=at, confidence="low", facts={"facts": page["facts"]},
            note=PAGE_CREDENTIAL_NOTE,
        ))
    return findings


async def collect_pages(candidate: Candidate, found: list[Finding], step: Step) -> tuple[list[Finding], list[Gap]]:
    """Step `read_pages`. `found` is what the other collectors returned (web results are read)."""
    gaps: list[Gap] = []

    async def call():
        urls = pick_pages(candidate, found)
        if not urls:
            raise SourceSkipped("no web pages about this person to read")
        results = await asyncio.gather(*(read_one_page(u, candidate) for u in urls), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if errors and len(errors) == len(results):
            raise next((e for e in errors if isinstance(e, SourceSkipped)), errors[0])
        if errors:
            gaps.append(Gap(step="read_pages", reason=f"{len(errors)} pages could not be read"))
        pages = [r for r in results if isinstance(r, dict)]
        if other := results.count(None):
            gaps.append(Gap(step="read_pages", reason=f"{other} pages skipped: not about this person"))
        findings = [f for page in pages for f in page_findings(page)]
        cases = sum(len(p["cases"]) for p in pages)
        step("read_pages", "done", f"{len(pages)} pages read, {cases} cases")
        return findings

    return await run_step("read_pages", "Page reader", step, gaps, call), gaps


# Testimony list check (PLAN M3 3k): cases from the expert's pages and from a pasted list, each
# looked up in CourtListener, then on an official court site found by one web search.

MAX_CASES = 10  # CourtListener allows 125 calls a day; find_case uses one per case
NOT_FOUND_NOTE = "not in CourtListener; state trial records may not be online \u2014 verify manually"
COURT_SITE_NOTES = {
    "found": "an official court page appears to show this case and this person; verify",
    "unclear": "an official court page was found but does not clearly show this case and this person; verify",
}

LIST_INSTRUCTIONS = (
    "You read a list of court cases an expert witness says they testified in, pasted by a user of a "
    "legal research tool. Return each case once: case name as written, court, and year. Leave court "
    "or year empty when the list does not give them. Do not add cases that are not in the list."
)
LIST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cases"],
    "properties": {"cases": {"type": "array", "items": CASE_ITEM}},
}

COURT_PAGE_INSTRUCTIONS = (
    "You read one page from an official court website for a legal research tool. Answer two "
    "questions: does the page show the named court case, and does it name the given person in that "
    "case? Answer false when the page does not clearly show it."
)
COURT_PAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["shows_case", "shows_person"],
    "properties": {"shows_case": {"type": "boolean"}, "shows_person": {"type": "boolean"}},
}


def case_key(case_name: str) -> str:
    """Case names compared without case, accents, punctuation, and "vs" vs "v"."""
    words_ = re.sub(r"[^a-z0-9]+", " ", fold(case_name)).split()
    return " ".join("v" if w == "vs" else w for w in words_)


def is_court_site(url: str) -> bool:
    """Official court sites: .gov or .us hosts, or "court" in the host. CourtListener is not one."""
    host = host_of(url) or ""
    return (host.endswith((".gov", ".us")) or "court" in host) and not host.endswith("courtlistener.com")


def search_link(case_name: str) -> str:
    return f"{courtlistener.SITE}/?q=" + quote_plus('"' + case_name.replace('"', "") + '"')


async def parse_testimony_list(text: str) -> list[dict]:
    """One AI call: pasted text -> [{case_name, court, year}]. The text itself is never stored."""
    reply = await llm.call_model(LIST_INSTRUCTIONS, f"Testimony list:\n{text}", schema=LIST_SCHEMA)
    return [c for c in (clean_case(i) for i in (reply.data or {}).get("cases") or []) if c]


async def court_page_shows(url: str, case: dict, name: str) -> bool:
    page = await firecrawl.scrape(url)
    text = (page.get("markdown") or "")[:PAGE_TEXT_MAX]
    if not has_full_name(text, name):
        return False
    where = ", ".join(x for x in (case["court"], case["year"]) if x)
    prompt = "\n".join([f"Person: {name}", f"Case: {case['case_name']}" + (f" ({where})" if where else ""),
                        "Page markdown:", text])
    data = (await llm.call_model(COURT_PAGE_INSTRUCTIONS, prompt, schema=COURT_PAGE_SCHEMA)).data or {}
    return bool(data.get("shows_case") and data.get("shows_person"))


async def check_case(case: dict, candidate: Candidate) -> tuple[Finding, list[str]]:
    """One case -> a case_check finding plus short gap reasons. Never raises for a source error."""
    name, problems = candidate.display_name, []
    base = {k: case.get(k) for k in ("case_name", "court", "year", "listed_in")}
    try:
        year = int(case["year"]) if case["year"] else None  # clean_case keeps 4 digits only
        hit = await courtlistener.find_case(case["case_name"], name, case["court"], year)
    except SourceSkipped as e:
        hit = None
        problems.append(f"CourtListener case lookup skipped: {e}" if str(e) else "CourtListener case lookup skipped")
    except Exception as e:  # noqa: BLE001 - one failed lookup still gets the web check
        hit = None
        problems.append(failure("CourtListener case lookup", e))
    if hit:
        return Finding(
            collector="testimony", kind="case_check", title=case["case_name"], url=hit["url"],
            source_type="courtlistener_opinion", retrieved_at=store.now(), confidence="high",
            note="found in court records (CourtListener)",
            facts=base | {"status": "found", "how": "courtlistener", "link": hit["url"],
                          "court_record": {k: hit.get(k) for k in ("case_name", "court", "date_filed",
                                                                   "docket_number")},
                          "passages": list(hit.get("passages") or [])},
        ), problems

    status, how, url, source_type, note = "not_found", "courtlistener", search_link(case["case_name"]), \
        "courtlistener_search", NOT_FOUND_NOTE
    query = " ".join(x for x in ('"' + case["case_name"].replace('"', "") + '"', case["court"], case["year"],
                                 '"' + name.replace('"', "") + '"') if x)
    try:
        results = await websearch.search_facts(name, [query], "court records of this case",
                                               context=context_of(candidate))
        court_page = next((r for r in results if r.get("url") and is_court_site(r["url"])
                           and not is_blocked(r["url"])), None)
    except Exception as e:  # noqa: BLE001 - incl. SourceSkipped: the case stays not_found
        court_page = None
        problems.append(failure("Web search", e))
    if court_page:
        url, source_type, how = court_page["url"], "web", "court_site"
        try:
            status = "found" if await court_page_shows(url, case, name) else "unclear"
        except SourceSkipped as e:
            status = "unclear"
            problems.append(f"court page not read: {e}" if str(e) else "court page not read")
        except Exception as e:  # noqa: BLE001 - a court page we cannot read leaves the case unclear
            status = "unclear"
            problems.append(failure("Court page read", e))
        note = COURT_SITE_NOTES[status]
    return Finding(
        collector="testimony", kind="case_check", title=case["case_name"], url=url, source_type=source_type,
        retrieved_at=store.now(), confidence="low", note=note,
        facts=base | {"status": status, "how": how, "link": url},
    ), problems


async def cross_check(candidate: Candidate, found: list[Finding], testimony_list: str | None,
                      step: Step) -> tuple[list[Finding], list[Gap]]:
    """Step `cross_check`. Cases: the pasted list first, then page_testimony_case findings in `found`;
    deduped by case name, at most MAX_CASES."""
    gaps: list[Gap] = []

    async def call():
        cases, list_status = [], None
        if testimony_list and testimony_list.strip():
            parsed, list_status = await safe("cross_check", "Testimony list", gaps,
                                             lambda: parse_testimony_list(testimony_list))
            cases += [c | {"listed_in": "testimony_list"} for c in parsed or []]
        cases += [{k: f.facts.get(k) for k in ("case_name", "court", "year")} | {"listed_in": "expert_page"}
                  for f in found if f.kind == "page_testimony_case" and f.facts.get("case_name")]
        seen, unique = set(), []
        for case in cases:
            if case_key(case["case_name"]) not in seen:
                seen.add(case_key(case["case_name"]))
                unique.append(case)
        if len(unique) > MAX_CASES:
            gaps.append(Gap(step="cross_check", reason=f"{len(unique) - MAX_CASES} cases not checked"
                                                       f" (at most {MAX_CASES} per research)"))
        if not unique:
            if list_status:
                step("cross_check", list_status, gaps[-1].reason)
            else:
                step("cross_check", "skipped", "no cases to check")
            return []
        checked = await asyncio.gather(*(check_case(c, candidate) for c in unique[:MAX_CASES]))
        problems: dict[str, int] = {}
        for _, reasons in checked:
            for reason in reasons:
                problems[reason] = problems.get(reason, 0) + 1
        gaps.extend(Gap(step="cross_check", reason=f"{reason} ({n} cases)") for reason, n in problems.items())
        findings = [f for f, _ in checked]
        counts = {s: sum(f.facts["status"] == s for f in findings) for s in ("found", "unclear", "not_found")}
        step("cross_check", "done", f"{len(findings)} cases: {counts['found']} found, {counts['unclear']} unclear,"
                                    f" {counts['not_found']} not found")
        return findings

    return await run_step("cross_check", "Testimony list check", step, gaps, call), gaps


# Follow-up loop (DESIGN 4A.1, 4A.3-4A.6): after round 1, one ADK agent follows leads with source
# tools. Findings are built by code from tool results only; the agent's own text is thrown away.

JOB_SECONDS = 300  # the whole research job (PLAN 5)
FOLLOW_MAX_SECONDS = 120
FOLLOW_MIN_SECONDS = 30  # less time left: the step is skipped
FOLLOW_MAX_CALLS = 10  # model calls (ADK max_llm_calls)
FOLLOW_WEB_QUERIES = 3
FOLLOW_PAGES = 3  # pages read on top of round 1
MAX_LEADS = 60  # findings shown to the agent
LEAD_KINDS = ("court_opinion", "case_check", "page_testimony_case", "page_credential", "web_testimony",
              "web_credential")
# topic -> (search topic, collector, kind, note): the same as round 1
FOLLOW_TOPICS = {
    "testimony": ("expert testimony", "testimony", "web_testimony", "verify"),
    "credentials": ("licences, board certification, discipline", "credentials", "web_credential",
                    "verify manually at the official board site"),
}
TOOL_LABELS = {"web_facts": "Web search", "read_page": "Page reader", "find_case": "Case lookup",
               "opinion_passages": "Opinion text"}
# Fixed tool replies to the model (never source text)
NEED_FULL_NAME = "the query must contain the person's full name"
BLOCKED_SITE_QUERY = "the query must not name a site that must not be searched"
BAD_TOPIC = "topic must be testimony or credentials"
BLOCKED_URL = "this site must not be read"
UNKNOWN_URL = "unknown URL: use a URL from the findings"
API_SITE = "this site is read through its own source, not as a page"
# Hosts we read through an API source (token, rate limits, caches: gotchas 5-7, 11): never scraped.
API_HOSTS = ("courtlistener.com", "openalex.org", "crossref.org", "oig.hhs.gov")
ALREADY_READ = "page already read"
UNKNOWN_CASE = "unknown case: use a case named in the findings"
ALREADY_CHECKED = "case already checked"
UNKNOWN_OPINION = "use the id of a court_opinion finding without passages"
NOT_AVAILABLE = "source not available: use another tool or reply DONE"

FOLLOW_INSTRUCTIONS = (
    "You help a legal research tool check one expert witness. The findings collected so far for this "
    "person follow, one per line with an id. Follow open leads with your tools: cases on their pages not "
    "yet found in court records (find_case), pages about them not yet read (read_page), web results "
    "naming a case or a credential worth a closer look (web_facts, then read_page), court "
    "opinions without passages (opinion_passages). Use only ids, URLs and case names from the findings or "
    "from your tool results; every web query must contain the person's full name. Professional records "
    "only: never private life, family, home address or health. Reply DONE when nothing new is likely."
)


def lead_line(ref: str, f: Finding) -> str:
    """One finding as the agent sees it: our own facts, never snippet or page text."""
    if f.kind == "page_testimony_case":
        detail = ", ".join(x for x in (f.facts.get("court"), f.facts.get("year")) if x)
    elif f.kind == "case_check":
        detail = f"status {f.facts.get('status')}"
    elif f.kind == "court_opinion":
        detail = "passages fetched" if f.facts.get("passages") else "no passages"
    else:
        detail = "; ".join(f.facts.get("facts") or [])
    return " | ".join(x for x in (ref, f.kind, f.title, detail, f.url) if x)


def named_in(key: str, run: Run) -> Finding | None:
    """The page case whose name is exactly `key` (normalised); never a prefix or a phrase in a web
    fact: "Doe v. Smith" must not match "Doe v. Smith Industries Holdings"."""
    for hit in run.evidence.values():
        f = hit.get("finding")
        if f and f.kind == "page_testimony_case" and case_key(f.facts.get("case_name") or "") == key:
            return f
    return None


async def follow_up(candidate: Candidate, found: list[Finding], step: Step,
                    started: float) -> tuple[list[Finding], list[Gap]]:
    """Step `follow_up`. `found` is every round-1 finding; `started` is the job's start (monotonic).
    Returns new findings only; a round-1 court_opinion may get its passages filled in place."""
    key, gaps = "follow_up", []
    seconds = min(FOLLOW_MAX_SECONDS, JOB_SECONDS - (time.monotonic() - started))
    if seconds < FOLLOW_MIN_SECONDS:
        step(key, "skipped", "no time left")
        return [], [Gap(step=key, reason="no time left to follow leads")]
    step(key, "running")
    name, context = candidate.display_name, context_of(candidate)
    checked = {case_key(f.facts["case_name"]) for f in found if f.kind == "case_check" and f.facts.get("case_name")}
    no_passages = sum(f.kind == "court_opinion" and not f.facts.get("passages") for f in found)
    run = Run(seconds, prefix="F", caps={
        "web_facts": FOLLOW_WEB_QUERIES, "read_page": FOLLOW_PAGES, "find_case": max(0, MAX_CASES - len(checked)),
        "opinion_passages": min(MAX_OPINIONS, no_passages)})
    read = {plain_url(u) for u in pick_pages(candidate, found)}  # round 1 read these
    new: list[Finding] = []
    problems: list[str] = []
    followed = 0

    def keep(finding: Finding, is_new: bool = True) -> str:
        if is_new:
            new.append(finding)
        return run.add({"url": finding.url, "title": finding.title, "finding": finding})

    def not_available(e: SourceSkipped, label: str) -> dict:
        problems.append(str(e) or f"{label} skipped")
        return refuse(NOT_AVAILABLE)

    lines = []
    for e in candidate.evidence:
        if e.source == "web" and not is_blocked(e.url):
            lines.append(" | ".join([run.add({"url": e.url, "title": e.title or e.url}), "web_page", e.title or "",
                                     e.url]))
    for f in found:
        if f.kind in LEAD_KINDS and not is_blocked(f.url):
            lines.append(lead_line(keep(f, is_new=False), f))

    async def web_facts(query: str, topic: str) -> dict:
        """Search the web once. `query` must contain the person's full name; `topic` is "testimony"
        or "credentials". Returns new pages about the person with short facts."""
        nonlocal followed
        query = query[:websearch.QUERY_MAX]  # cut first: the checks below see exactly what is sent
        if not has_full_name(query, name):
            return refuse(NEED_FULL_NAME)
        if websearch.names_blocked_site(query):  # gotcha 2: never ask the search for those sites
            return refuse(BLOCKED_SITE_QUERY)
        if topic not in FOLLOW_TOPICS:
            return refuse(BAD_TOPIC)
        search_topic, collector, kind, note = FOLLOW_TOPICS[topic]
        try:
            results = await websearch.search_facts(name, [query], search_topic, context=context)
        except SourceSkipped as e:
            return not_available(e, "Web search")
        followed += 1
        pages = [{"id": keep(f), "url": f.url, "title": f.title, "facts": f.facts["facts"]}
                 for f in web_findings(results, collector, kind, note) if not run.has_url(f.url)]
        return {"pages": pages}

    async def read_page(url: str) -> dict:
        """Read one page about the person (a URL from the findings) and list the court cases and
        credentials it names."""
        nonlocal followed
        url = url.strip()
        if is_blocked(url):
            return refuse(BLOCKED_URL)
        if on_domain(host_of(url) or "", API_HOSTS):
            return refuse(API_SITE)  # e.g. a CourtListener opinion or search link
        if not run.has_url(url):
            return refuse(UNKNOWN_URL)  # no made-up URLs
        if plain_url(url) in read:
            return refuse(ALREADY_READ)
        read.add(plain_url(url))
        try:
            page = await read_one_page(url, candidate)  # Firecrawl rules (gotchas 24-25) live in the source
        except SourceSkipped as e:
            return not_available(e, "Page reader")
        followed += 1
        if page is None:
            return {"about_person": False}
        cases = []
        for f in page_findings(page):
            ref = keep(f)
            if f.kind == "page_testimony_case":
                cases.append({"id": ref} | {k: f.facts[k] for k in ("case_name", "court", "year")})
        return {"about_person": True, "cases": cases, "credentials": page["facts"]}

    async def find_case(case_name: str) -> dict:
        """Look up one page_testimony_case from the findings, by its exact case name: CourtListener
        first, then an official court site. Returns whether it was found."""
        nonlocal followed
        case = clean_case({"case_name": case_name})
        source = named_in(case_key(case["case_name"]), run) if case else None
        if source is None:
            return refuse(UNKNOWN_CASE)  # a case no source named would be an uncited claim
        if case_key(case["case_name"]) in checked:
            return refuse(ALREADY_CHECKED)
        checked.add(case_key(case["case_name"]))
        # court and year come from the page case, never from the model
        case = {k: source.facts.get(k) for k in ("case_name", "court", "year")} | {"listed_in": "expert_page"}
        finding, reasons = await check_case(case, candidate)
        problems.extend(reasons)
        followed += 1
        return {"id": keep(finding), "status": finding.facts["status"], "url": finding.url}

    async def opinion_passages(finding_id: str) -> dict:
        """Fetch the passages naming the person from a court_opinion finding that has none yet."""
        nonlocal followed
        hit = run.get(finding_id.strip().upper()) or {}
        f = hit.get("finding")
        if not f or f.kind != "court_opinion" or f.facts.get("passages"):
            return refuse(UNKNOWN_OPINION)
        try:
            hits = await courtlistener.search_opinions(name)  # the cached round-1 search (gotcha 5)
            oid = next((h["opinion_id"] for h in hits if h.get("opinion_id") is not None
                        and plain_url(h["url"]) == plain_url(f.url)), None)
            passages = await courtlistener.opinion_passages(oid, name) if oid is not None else []
        except SourceSkipped as e:
            return not_available(e, "CourtListener")
        followed += 1
        if passages:
            f.facts["passages"], f.note = list(passages), None
        return {"id": finding_id, "passages": len(passages)}

    prompt = "\n".join([websearch.person_line(name, context), "Findings:", *lines[:MAX_LEADS]])
    stopped, model_failed = None, None
    try:
        agent = llm.make_agent("research_followup", FOLLOW_INSTRUCTIONS, tools=[
            FunctionTool(run.tool(t)) for t in (web_facts, read_page, find_case, opinion_passages)])
        stopped = (await llm.run_agent(agent, prompt, max_llm_calls=FOLLOW_MAX_CALLS, timeout=seconds)).stopped
    except llm.ModelError as e:  # fixed text; the tool results so far are kept
        model_failed = failure("Follow-up", e)
        gaps.append(Gap(step=key, reason=model_failed))
    counts: dict[str, int] = {}
    for reason in problems:
        counts[reason] = counts.get(reason, 0) + 1
    gaps.extend(Gap(step=key, reason=r if n == 1 else f"{r} ({n} times)") for r, n in counts.items())
    gaps.extend(Gap(step=key, reason=f"{TOOL_LABELS[t]} failed ({exc})") for t, exc in run.failures)
    new = [f for f in new if f.url.startswith(("http://", "https://")) and not is_blocked(f.url)]
    if stopped:
        gaps.append(Gap(step=key, reason=f"follow-up stopped at its {stopped.replace('_', ' ')}"))
    if model_failed:
        step(key, "failed", model_failed)
    else:
        step(key, "done", f"{followed} leads followed" + (f"; stopped at the {stopped.replace('_', ' ')}"
                                                          if stopped else ""))
    return new, gaps
