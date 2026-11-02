"""CourtListener: opinion search (DESIGN 4.1, the candidate search) and opinion passages (DESIGN 4.2);
RECAP docket search (the research tools: PACER dockets and filings, user decision 2026-11-01);
gotchas 5, 11, 13."""

import asyncio
import html
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from app import http, store
from app.config import get_settings
from app.sources import (
    TITLES,
    SourceFailed,
    SourceSkipped,
    fold,
    given_family,
    has_full_name,
    name_tokens,
    name_words,
    strip_accents,
)

SITE = "https://www.courtlistener.com"
SEARCH = f"{SITE}/api/rest/v4/search/"
MAX_HITS = 20
CAPS = ((3600, 50, "hourly"), (86400, 125, "daily"))  # gotcha 5; per-minute spacing is in app/http.py
# The RECAP search is sometimes slow on a query it has not run lately (2026-11-01: 20 s timed out,
# the same query took 2 s a minute later): a longer wait, and one retry after a timeout or a 502-504.
TIMEOUT = httpx.Timeout(60.0, connect=5.0)
RETRY_STATUS = {502, 503, 504}
TIMED_OUT = (f"CourtListener did not answer within {TIMEOUT.read:g} s (tried twice). It is sometimes slow: "
             "call the tool again in a minute")
log = logging.getLogger("app.agent")  # shown in the server log (app/main.py)


def failure(status: int) -> str:
    """What an HTTP error from CourtListener means for the caller, in fixed words."""
    if status == 429:
        return "CourtListener HTTP 429: rate limited. Wait a few minutes before calling again"
    if status in (401, 403):
        return f"CourtListener HTTP {status}: the API token was refused. Calling again will not help"
    if status >= 500:
        return f"CourtListener HTTP {status}: server error (tried twice if it was 502-504). Try again in a few minutes"
    return f"CourtListener HTTP {status}: the request was refused. Calling again with the same input will not help"


def name_variants(name: str) -> list[str]:
    """Full-name phrases only, never the surname alone, titles removed:
    "Dr. Jordan K. Avery" -> "Jordan K. Avery" and "Jordan Avery"."""
    # Keep only characters a name needs; quotes, backslashes, wildcards etc. would break the query.
    words = [w for w in name_words(re.sub(r"[^\w\s.,'’-]", " ", name)) if w]
    full = " ".join(words)
    given, family = given_family(full)
    if not given or given == family:
        return []
    real = [w for w in words if name_tokens(w)]  # no initials
    short = f"{real[0]} {real[-1]}"
    return [full] if short.lower() == full.lower() else [full, short]


# Expert-role words that must sit near the name, so a party or a judge with the name does not count.
# Not "testified"/"testimony"/"deposition": parties and lay witnesses testify too. Not "Rule 702":
# "702" also matches page cites ("F.2d at 702"). Phrases are exact (no stemming): plural listed too.
ROLE_WORDS = ("expert", "experts", "Daubert")
NEAR = 5  # max word distance between the name and a role word


def build_query(name: str) -> str:
    """The full name must appear, and given + family name must be within NEAR words of a role word.
    A name with accents is also searched without them ("José García" OR "Jose Garcia").
    Proximity syntax `"word1 word2"~N` ("the words in the phrase are within a desired distance of
    each other") and "a phrase query behaves as an exact query for each term":
    https://wiki.free.law/c/courtlistener/help/search/advanced-search-and-query-techniques
    Not verified live (no token in dev): word order inside a proximity phrase and >2-word phrases."""
    variants = name_variants(name)
    shorts = [variants[-1]]  # given + family name; its words are in every variant
    for plain in (strip_accents(v) for v in list(variants)):
        if plain not in variants:
            variants.append(plain)
    if (plain := strip_accents(shorts[0])) != shorts[0]:
        shorts.append(plain)
    phrases = " OR ".join(f'"{v}"' for v in variants)
    near = " OR ".join(f'"{short} {w}"~{NEAR}' for short in shorts for w in ROLE_WORDS)
    return f"({phrases}) AND ({near})"


def is_search_body(body: object) -> bool:
    """Only a well-formed search answer may be cached or used: {"results": [{"absolute_url": "/..."}]}."""
    results = body.get("results") if isinstance(body, dict) else None
    return isinstance(results, list) and all(
        isinstance(x, dict) and isinstance(x.get("absolute_url"), str) and x["absolute_url"].startswith("/")
        for x in results
    )


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


def clean(value: object) -> str:
    """Text of a search field: highlight tags (<mark>) removed, entities decoded, whitespace collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text(value))).split())


def opinion_id_of(opinions: list[dict]) -> int | None:
    """The opinion record to read: the first one whose snippet highlights a match, else the first.
    Not the number in `absolute_url`: that is the case (cluster) id, a different number."""
    with_id = [o for o in opinions if isinstance(o.get("id"), int)]
    best = next((o for o in with_id if "<mark>" in text(o.get("snippet"))), with_id[0] if with_id else None)
    return best["id"] if best else None


def to_hit(result: dict) -> dict:
    opinions = result.get("opinions")
    opinions = [o for o in opinions if isinstance(o, dict)] if isinstance(opinions, list) else []
    snippet = " ".join(text(o.get("snippet")) for o in opinions)
    case_name, court, filed = clean(result.get("caseName")), clean(result.get("court")), clean(result.get("dateFiled"))
    return {
        "source": "courtlistener",
        "url": SITE + result["absolute_url"],
        "title": case_name or "Court opinion",
        "note": f"{court or 'court'}, filed {filed or 'unknown date'}",
        "query": "expert",  # the search requires expert-role words near the name
        # same case filed twice (e.g. two opinion records): app/candidate_agent.py keeps the lowest opinion id
        "case": (case_name, court, filed),
        "opinion_id": opinion_id_of(opinions),
        # prompt only, never returned (the raw search body is cached, DESIGN 4)
        "snippet": clean(snippet)[:600],
    }


def token() -> str:
    value = get_settings().courtlistener_token
    if not value:
        raise SourceSkipped("COURTLISTENER_TOKEN is not set")
    return value


async def get_json(url: str, params: dict | None, valid: Callable[[object], bool], token: str) -> dict:
    """One CourtListener read with the hour/day caps, call counting and cache rules (gotcha 5).
    Only a body that passes `valid` is cached or returned. A timeout or a 502-504 is retried once;
    failures raise SourceFailed saying what happened and whether calling again can help."""
    for attempt in (1, 2):
        for seconds, cap, label in CAPS:  # each attempt is a call
            if store.calls_since("courtlistener", seconds) >= cap:
                raise SourceSkipped(f"CourtListener {label} limit of {cap} calls reached")
        # Earliest moment the request can go out. If other callers queue ahead it goes out later, so a
        # timeout in that gap is still counted: the safe side for the hour/day caps.
        limiter = http.limiters.get("courtlistener")
        send_at = max(time.monotonic(), limiter.last + limiter.interval) if limiter else time.monotonic()
        try:
            r = await http.fetch("courtlistener", url, params=params, headers={"Authorization": f"Token {token}"},
                                 cache=True, cache_if=valid, timeout=TIMEOUT)
        except BaseException as e:
            # Failed or timed out after the rate-limit wait: the request went out, so it counts (gotcha 5).
            if time.monotonic() >= send_at:
                store.record_call("courtlistener")
            if not isinstance(e, httpx.TimeoutException):
                raise
            if attempt == 2:
                raise SourceFailed(TIMED_OUT) from None
            log.warning("CourtListener did not answer within %g s (%s); retrying once", TIMEOUT.read,
                        type(e).__name__)
            continue
        if not r.from_cache:  # cache hits cost nothing
            store.record_call("courtlistener")
        if r.status_code in RETRY_STATUS and attempt == 1:
            log.warning("CourtListener HTTP %s; retrying once", r.status_code)
            continue
        if r.status_code != 200:
            raise SourceFailed(failure(r.status_code))
        try:
            body = r.json()
        except ValueError:
            body = None
        if not valid(body):
            raise SourceFailed("CourtListener returned an unexpected response. Try again later")
        return body
    raise AssertionError("unreachable")


async def search_opinions(name: str) -> list[dict]:
    key = token()
    if not name_variants(name):
        raise SourceSkipped("need a given and a family name; surname-only search is not used")
    body = await get_json(SEARCH, {"type": "o", "q": build_query(name), "highlight": "on"}, is_search_body, key)
    return [to_hit(x) for x in body["results"][:MAX_HITS]]


# RECAP (PACER) dockets: federal cases whose filings (or parties, lawyers) name the person. Filings
# say "reviewed by Dr. Alex Rivera" or "consulting physician", so only the full name is required;
# the AI same-person check in app/tools/courtlistener.py decides the rest.
DOCS_PER_CASE = 3


def is_docket_body(body: object) -> bool:
    """A well-formed RECAP search answer: {"results": [{"docket_absolute_url": "/..."}]}."""
    results = body.get("results") if isinstance(body, dict) else None
    return isinstance(results, list) and all(
        isinstance(x, dict) and isinstance(x.get("docket_absolute_url"), str)
        and x["docket_absolute_url"].startswith("/") for x in results
    )


def to_docket(result: dict, name: str) -> dict:
    """One RECAP search result: the case (fields copied by code), the filings that matched (description
    and link) and `passages`, their highlighted search snippets: in memory only, never stored (gotcha 15)."""
    docs = [d for d in result.get("recap_documents") or [] if isinstance(d, dict)][:DOCS_PER_CASE]
    named_as = [role for role in ("party", "attorney")
                if has_full_name(" ".join(text(x) for x in result.get(role) or []), name)]
    return {
        "url": SITE + result["docket_absolute_url"],
        "docket_id": result["docket_id"] if isinstance(result.get("docket_id"), int) else None,
        "case_name": clean(result.get("caseName")) or "Court case",
        "court": clean(result.get("court")),
        "docket_number": clean(result.get("docketNumber")),
        "date_filed": clean(result.get("dateFiled")),
        "nature_of_suit": clean(result.get("suitNature")),
        "named_as": named_as,
        "documents": [{"description": clean(d.get("short_description") or d.get("description"))[:200],
                       "date_filed": clean(d.get("entry_date_filed")),
                       "url": SITE + d["absolute_url"] if text(d.get("absolute_url")).startswith("/") else None}
                      for d in docs],
        "passages": [clean(d.get("snippet"))[:PASSAGE_CHARS] for d in docs if "<mark>" in text(d.get("snippet"))],
    }


def name_query(name: str) -> str:
    """The full-name phrases (with and without middle name and accents), OR-ed; never the surname alone."""
    variants = name_variants(name)
    variants += [p for p in (strip_accents(v) for v in variants) if p not in variants]
    return " OR ".join(f'"{v}"' for v in variants)


async def search_dockets(name: str) -> list[dict]:
    """RECAP dockets naming the person in full (in a filing, or as a party or lawyer), first page."""
    key = token()
    if not name_variants(name):
        raise SourceSkipped("need a given and a family name; surname-only search is not used")
    body = await get_json(SEARCH, {"type": "r", "q": name_query(name), "highlight": "on"}, is_docket_body, key)
    return [to_docket(x, name) for x in body["results"][:MAX_HITS]]


# Opinion text (DESIGN 4.2 call 3): passages around the candidate's full name, cut by code.
PASSAGE_CHARS = 400
MAX_PASSAGES = 3


def is_opinion_body(body: object) -> bool:
    return isinstance(body, dict) and any(isinstance(body.get(k), str) for k in ("plain_text", "html_with_citations"))


def opinion_text(body: dict) -> str:
    """Plain text with whitespace collapsed; HTML (tags removed, entities decoded) if no plain text."""
    raw = text(body.get("plain_text")).strip()
    if not raw:
        raw = html.unescape(re.sub(r"<[^>]+>", " ", text(body.get("html_with_citations"))))
    return " ".join(raw.split())


def name_pattern(name: str) -> re.Pattern | None:
    """Full-name mentions in folded text: "alex rivera", "dr. alex j. rivera", "rivera, alex".
    The middle name may be left out. If `name` has one, only a middle name or initial with the same
    first letter matches ("Alex J. Rivera" -> "Alex James Rivera", not "Alex M. Rivera");
    without one, any middle initial matches. Never the surname alone (gotcha 13).
    None if the name has no given name."""
    given, family = given_family(name)
    if not given or given == family:
        return None
    parts = re.findall(r"[^\W\d_]+", fold(name))
    middles = sorted({re.escape(p[0]) + "[a-z]*" for p in parts if p not in (given, family) and p not in TITLES})
    middle = "|".join(middles) or "[a-z]"
    g, f = re.escape(given), re.escape(family)
    return re.compile(rf"\b{g}(?:[\s.]+(?:{middle})){{0,2}}[\s.]+{f}\b|\b{f},\s*{g}\b")


def folded_with_index(value: str) -> tuple[str, list[int]]:
    """fold() of `value` plus, per folded character, its index in `value` (fold can change length)."""
    chars, index = [], []
    for i, ch in enumerate(value):
        f = fold(ch)
        chars.append(f)
        index += [i] * len(f)
    return "".join(chars), index


def passages(value: str, name: str) -> list[str]:
    """Up to MAX_PASSAGES slices of `value`, about PASSAGE_CHARS long, centred on full-name mentions.
    Each passage is an exact substring of `value`."""
    pattern = name_pattern(name)
    if not pattern or not value:
        return []
    folded, index = folded_with_index(value)
    out, covered = [], -1
    for m in pattern.finditer(folded):
        start_i, end_i = index[m.start()], index[m.end() - 1] + 1
        if start_i < covered:
            continue  # already inside the last passage
        mid = (start_i + end_i) // 2
        start = max(0, mid - PASSAGE_CHARS // 2)
        end = min(len(value), start + PASSAGE_CHARS)
        start = max(0, end - PASSAGE_CHARS)
        # trim to whole words, but never cut the name itself
        if start > 0 and (sp := value.find(" ", start, start_i)) != -1:
            start = sp + 1
        if end < len(value) and (sp := value.rfind(" ", end_i, end)) != -1:
            end = sp
        out.append(value[start:end])
        covered = end
        if len(out) == MAX_PASSAGES:
            break
    return out


async def opinion_passages(opinion_id: int, name: str) -> list[str]:
    """Fetch one opinion's text and return up to 3 passages around the full name (no AI)."""
    key = token()
    if not name_pattern(name):
        raise SourceSkipped("need a given and a family name; surname-only matching is not used")
    body = await get_json(
        f"{SITE}/api/rest/v4/opinions/{int(opinion_id)}/",
        {"fields": "id,plain_text,html_with_citations"},
        is_opinion_body,
        key,
    )
    return passages(opinion_text(body), name)


# Testimony list check (PLAN M3 3k): is a named case in CourtListener, and does it name the person?


def case_phrase(case_name: str) -> str:
    """Case name safe inside a quoted phrase: quotes, backslashes and query operators removed."""
    return " ".join(re.sub(r"[^\w\s.,&'’-]", " ", case_name).split())


# ponytail: party key words by word lists, not a parser. Ceiling: an entity with none of these words
# reads as a person (its last word is used), a role word missing here stays in, and a first party
# that is not the one CourtListener names in its short caseName ("Doe" vs "Roe v. Springfield") misses.
ENTITY_WORDS = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "llp", "lp", "plc", "corp", "corporation", "co",
    "company", "group", "the", "of", "city", "town", "township", "village", "county", "state", "states",
    "united", "commonwealth", "people", "department", "dept", "board", "district", "hospital",
    "university", "school", "bank",
})
ROLE_TITLES = frozenset({"patrol", "police", "trooper", "deputy", "sheriff", "chief", "warden", "sergeant", "agent"})


def plain_words(value: str) -> list[str]:
    """Letters (and inner apostrophes) only: quotes, parentheses and query operators cannot get through."""
    return re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", value)


def party_key(side: str) -> str:
    """Key words of the first party on one side of "v.": a person's surname ("Patrol Officer Sam
    Roe and the Town of X" -> "Roe"), an entity's first meaningful words ("City of Springfield" ->
    "Springfield", "Example Holdings, Inc., Other Co." -> "Example Holdings")."""
    side = re.sub(r"\([^)]*\)", " ", side)  # "(Dr. Ann)", "(In re ...)" asides
    side = re.split(r"\s+(?:and|&)\s+|\bet\.?\s+al\b", side, maxsplit=1, flags=re.IGNORECASE)[0]
    first, *rest = side.split(",")
    for part in rest:  # ", MD" / ", Inc." belong to the first party; ", Next Party" does not
        if not all(w.lower() in TITLES | ENTITY_WORDS for w in plain_words(part)):
            break
        first += " " + part
    words = [w for w in plain_words(first) if w.lower() not in TITLES]
    if any(w.lower() in ENTITY_WORDS for w in words):
        run = []
        for w in words:  # first run of meaningful words, at most two
            if w.lower() in ENTITY_WORDS:
                if run:
                    break
                continue
            run.append(w)
            if len(run) == 2:
                break
        return " ".join(run)
    person = [w for w in words if w.lower() not in ROLE_TITLES and len(w) > 1]
    return person[-1] if person else ""


def case_name_query(case_name: str) -> str:
    """caseName:("Doe" AND "Roe") from the party key words; the whole name as a phrase if the
    name has no "v." / "vs." / "versus" or no key word comes out of it."""
    sides = re.split(r"\s+(?:v|vs|versus)\.?\s+", case_name, maxsplit=1, flags=re.IGNORECASE)
    keys = [k for k in (party_key(s) for s in sides) if k] if len(sides) == 2 else []
    if not keys:
        return f'caseName:"{case_phrase(case_name)}"'
    return "caseName:(" + " AND ".join(f'"{k}"' for k in keys) + ")"


def case_query(case_name: str, name: str) -> str:
    return f"{case_name_query(case_name)} AND ({name_query(name)})"


def case_score(result: dict, court: str | None, year: int | None) -> int:
    score = 0
    if year and text(result.get("dateFiled")).startswith(str(year)):
        score += 2
    found, wanted = fold(text(result.get("court"))), fold(court or "").strip()
    if wanted and found and (wanted in found or found in wanted):
        score += 1
    return score


async def find_case(case_name: str, name: str, court: str | None = None, year: int | None = None) -> dict | None:
    """One RECAP docket search for a case by name whose filings (or parties) also name the person in
    full. Several hits: the one matching `year` (date filed) and `court` wins, else the first. The
    docket as `to_docket` gives it (passages from the search snippets, no second call). None if not found."""
    key = token()
    if not name_variants(name):
        raise SourceSkipped("need a given and a family name; surname-only search is not used")
    if not case_phrase(case_name):
        return None
    body = await get_json(SEARCH, {"type": "r", "q": case_query(case_name, name), "highlight": "on"},
                          is_docket_body, key)
    results = body["results"][:MAX_HITS]
    if not results:
        return None
    return to_docket(max(results, key=lambda r: case_score(r, court, year)), name)  # max keeps the first of equals


# Filings of one docket, to download (app/tools/court_files.py). The list and the text come from the
# API (token, caps, cache); the PDF itself from the public storage host: no token, not an API call.
STORAGE = "https://storage.courtlistener.com/"
STORAGE_HOST = "storage.courtlistener.com"
MAX_DOC_PAGES = 5  # 20 filings per page, each page one API call (gotcha 5)
STORAGE_TIMEOUT = httpx.Timeout(120.0, connect=10.0)  # big scans stream slowly
CHUNK = 1 << 20
# "recap/gov.uscourts.nysd.1234/gov.uscourts.nysd.1234.26.0.pdf": relative, plain characters only
SAFE_PATH = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*")
BAD_PATH = "CourtListener gave an unexpected file path; the file was not downloaded"
DOWNLOAD_FAILED = "CourtListener storage did not send the file (network error or timeout). Try again later"


def safe_path(value: object) -> bool:
    return isinstance(value, str) and ".." not in value and SAFE_PATH.fullmatch(value) is not None


def is_documents_body(body: object) -> bool:
    """A well-formed filings search page whose `next` link (sent with the token) stays on the API."""
    nxt = body.get("next") if isinstance(body, dict) else None
    return is_search_body(body) and (nxt is None or (isinstance(nxt, str) and nxt.startswith(SEARCH + "?")))


def to_document(result: dict) -> dict | None:
    """One public filing, or None (not available, no file, odd id or path)."""
    if result.get("is_available") is not True or not isinstance(result.get("id"), int):
        return None
    if not safe_path(result.get("filepath_local")):
        return None
    number, attachment, pages = (result.get(k) for k in ("document_number", "attachment_number", "page_count"))
    return {
        "id": result["id"],
        "description": clean(result.get("short_description") or result.get("description"))[:200],
        "document_number": str(number) if isinstance(number, int | str) and str(number) else None,
        "attachment_number": attachment if isinstance(attachment, int) else None,
        "date_filed": clean(result.get("entry_date_filed")),
        "page_count": pages if isinstance(pages, int) else None,
        "filepath_local": result["filepath_local"],
        "url": SITE + result["absolute_url"],
    }


async def docket_documents(docket_id: int) -> tuple[list[dict], bool]:
    """(docs, more_left): the docket's public filings [{id, description, document_number (str or None),
    attachment_number (int or None), date_filed, page_count (int or None), filepath_local, url}], and
    True when filings were left unlisted. Follows the `next` cursor for up to MAX_DOC_PAGES pages, each
    one counted API call."""
    key = token()
    url, params = SEARCH, {"type": "rd", "q": f"docket_id:{int(docket_id)} AND is_available:true"}
    out = []
    for _ in range(MAX_DOC_PAGES):  # ponytail: filings past 5 pages (100) are not listed, only flagged
        body = await get_json(url, params, is_documents_body, key)
        out += [d for d in map(to_document, body["results"]) if d]
        url, params = body.get("next"), None  # the next link already holds the query and cursor
        if not url:
            break
    return out, bool(url)


def is_text_body(body: object) -> bool:
    return isinstance(body, dict) and "plain_text" in body and isinstance(body["plain_text"], str | None)


async def document_text(doc_id: int) -> str:
    """CourtListener's own text of one filing (its OCR for scans); "" if none. One API call."""
    body = await get_json(f"{SITE}/api/rest/v4/recap-documents/{int(doc_id)}/",
                          {"fields": "plain_text,ocr_status"}, is_text_body, token())
    return text(body.get("plain_text"))


async def download_document(filepath_local: str, dest: str | Path) -> int:
    """Stream one filing's PDF to `dest` (its folder must exist) in chunks; returns the bytes written.
    No token, no size cap, not cached, not counted against the API caps. Raises SourceFailed (fixed
    text) and removes `dest` on any failure."""
    if not safe_path(filepath_local):
        raise SourceFailed(BAD_PATH)
    dest = Path(dest)
    if limiter := http.limiters.get("courtlistener_storage"):
        await limiter.wait()
    written = 0
    try:
        async with http.get_client().stream("GET", STORAGE + filepath_local, timeout=STORAGE_TIMEOUT) as r:
            if r.status_code != 200:
                raise SourceFailed(f"CourtListener storage HTTP {r.status_code}: the file was not downloaded")
            if r.url.host != STORAGE_HOST:
                raise SourceFailed("CourtListener storage redirected elsewhere; the file was not downloaded")
            f = await asyncio.to_thread(dest.open, "wb")  # disk work off the event loop
            try:
                async for chunk in r.aiter_bytes(CHUNK):
                    await asyncio.to_thread(f.write, chunk)
                    written += len(chunk)
            finally:
                await asyncio.to_thread(f.close)
    except BaseException as e:
        dest.unlink(missing_ok=True)
        if isinstance(e, httpx.HTTPError):
            raise SourceFailed(DOWNLOAD_FAILED) from None
        raise
    return written
