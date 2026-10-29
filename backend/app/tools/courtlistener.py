"""Tool 1: court records (CourtListener; DESIGN 4B.9). Gotchas 5, 11, 13, 23: rate limits, cache and
the token check stay in app/sources/courtlistener.py; surname-only matches are never kept."""

import re

from app.evidence import refuse, words
from app.sources import US_STATES, courtlistener, fold, has_full_name, state_of
from app.tools import Ctx, tool

MAX_OPINIONS = 6
STEM = 6  # ponytail: crude stemming, a shared 6-letter prefix counts ("chiropractic" ~ "chiropractor")
KEEP_SCORE = 25  # full name alone: kept as level "low" (user decision 2026-10-29)
MEDIUM_SCORE = 40
HIGH_SCORE = 75
CASE_NAME_MAX = 200
# Same-person score (0-100): points and the code-made reason shown with them.
EVIDENCE = (50, "opinion link is in the candidate's evidence")
FULL_NAME = (25, "full name in the opinion text")
FIELD_WORD = (15, "field word near the name")
STATE_COURT = (10, "court in the expert's state")
SKIPPED_GAP = "{} same-name court opinions skipped: not tied to this person"
FOUND_NOTE = "found in court records (CourtListener)"
LOW_NOTE = "full name only; verify it is the same person"


def plain_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def newest_opinions(hits: list[dict], evidence: set[str]) -> list[dict]:
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


def court_in_state(court: str, state: str | None) -> bool:
    """True if the court's name holds the state's name ("District Court, N.D. Ohio" for OH).
    Longer state names holding it are removed first ("West Virginia" is not Virginia)."""
    text = fold(court)
    for wanted in (n for n, code in US_STATES.items() if code == state):
        for other in US_STATES:
            if wanted in other and other != wanted:
                text = text.replace(other, " ")
        if re.search(rf"\b{wanted}\b", text):
            return True
    return False


def match(in_evidence: bool, passages: list[str], court: str, ctx: Ctx) -> dict | None:
    """Code-made same-person score, or None for a surname-only opinion (gotcha 13): no full name in
    the passages and not in the candidate's evidence."""
    expert = ctx.expert
    full_name = has_full_name(" ".join(passages), expert.display_name)
    if not in_evidence and not full_name:
        return None
    checks = [(in_evidence, EVIDENCE), (full_name, FULL_NAME),
              (full_name and mentions_field(passages, expert.field), FIELD_WORD),
              (court_in_state(court, state_of(expert.location)), STATE_COURT)]
    score = sum(points for ok, (points, _) in checks if ok)
    level = "high" if score >= HIGH_SCORE else "medium" if score >= MEDIUM_SCORE else "low"
    return {"score": score, "level": level,
            "reasons": [reason for ok, (_, reason) in checks if ok]}


@tool(label="Court records", cap=1)
async def court_records(ctx: Ctx) -> dict:
    """Find court opinions (CourtListener) that name the expert near the word "expert", and score
    each one 0-100 as the same person: 75+ is level "high", 40-74 "medium", 25-39 "low".
    "low" means unconfirmed: only the full name matches, nothing else ties the opinion to this
    expert, so it may be someone else with the same name; say so if you use it. Lower scores and
    opinions that give only the surname are not kept. Use it once, early. Takes no arguments.
    Each opinion comes with one short passage around the expert's name; `skipped_same_name`
    counts opinions about someone else with the same name."""
    name = ctx.expert.display_name
    hits = await courtlistener.search_opinions(name)
    if not hits:
        ctx.add_gap("no court opinions name this person as an expert")
    evidence = {plain_url(e.url) for e in ctx.expert.evidence}
    opinions, skipped, missing = [], 0, 0
    for hit in newest_opinions(hits, evidence):
        case_name, court, date_filed = hit.get("case") or ("", "", "")
        passages, note = [], None
        try:
            if hit.get("opinion_id") is None:
                raise ValueError("no opinion id")
            passages = await courtlistener.opinion_passages(hit["opinion_id"], name)
        except Exception:  # noqa: BLE001 - the opinion is still scored, just without passages
            missing += 1
            note = "opinion text not fetched; no passages"
        same = match(plain_url(hit["url"]) in evidence, passages, court, ctx)
        if same is None or same["score"] < KEEP_SCORE:
            skipped += 1
            continue
        if same["level"] == "low":
            note = LOW_NOTE
        # The search `snippet` is never stored (gotcha 15): only fields copied by code plus our passages.
        ref = ctx.add_finding(
            "court_opinion", hit.get("title") or "Court opinion", hit["url"],
            facts={"case_name": case_name, "court": court, "date_filed": date_filed,
                   "passages": list(passages), "match": same},
            confidence="high" if same["level"] == "high" else "low", note=note,
            source_type="courtlistener_opinion")
        if ref:
            opinions.append({"ref": ref, "case_name": case_name, "court": court, "date_filed": date_filed,
                             "score": same["score"], "level": same["level"],
                             "passage": passages[0] if passages else ""})
    if missing:
        ctx.add_gap(f"opinion text not fetched for {missing} opinions")
    if skipped:
        ctx.add_gap(SKIPPED_GAP.format(skipped))
    ctx.log(f"{len(hits)} found, {len(opinions)} kept, {skipped} same-name skipped")
    return {"opinions": opinions, "skipped_same_name": skipped}


@tool(label="Court case lookup", cap=6)
async def court_case(ctx: Ctx, case_name: str, court: str = "", year: int = 0) -> dict:
    """Check whether one named court case (for example "Doe v. Example Co.") is in CourtListener and
    names the expert. Use it for a case some finding or the testimony list names, never a guessed
    one. `court` and `year` (filing year) are optional and pick the best of several hits.
    Not found -> {"found": false} and nothing is saved."""
    if len(case_name) > CASE_NAME_MAX:
        return refuse("case name too long")
    hit = await courtlistener.find_case(case_name, ctx.expert.display_name, court or None, year or None)
    if not hit:
        ctx.log("not found")
        return {"found": False}
    record = {k: hit.get(k) for k in ("case_name", "court", "date_filed", "docket_number")}
    # The passages come from search snippets: in the reply only, never stored (gotcha 15).
    ref = ctx.add_finding(
        "case_check", hit.get("case_name") or case_name, hit["url"],
        facts={"case_name": case_name, "court": court or None, "year": str(year) if year else None,
               "status": "found", "how": "courtlistener", "link": hit["url"], "court_record": record},
        confidence="high", note=FOUND_NOTE, source_type="courtlistener_opinion")
    ctx.log("found")
    return {"found": True, "ref": ref, **record, "passages": list(hit.get("passages") or [])}
