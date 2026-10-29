"""Agents: the candidate finder (DESIGN 4.1, 4A). Round 1 of source calls is fixed code; then an ADK
agent may follow leads with source tools; then one grouping call. Every guard stays in code."""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable

from google.adk.tools import FunctionTool
from pydantic import BaseModel

from app import llm
from app.evidence import Run, copied, refuse, squash, words
from app.models import Candidate, Evidence, FailedSource, Identifiers
from app.sources import (
    SourceSkipped,
    courtlistener,
    given_family,
    has_full_name,
    host_of,
    is_blocked,
    is_expert_directory,
    openalex,
    same_person_name,
    state_of,
    websearch,
)

MAX_CANDIDATES = 10
SOURCE_TIMEOUT = 45.0  # seconds per source; the whole search should stay well under a minute
# Follow-up loop (DESIGN 4A.3): what is left of TOTAL_SECONDS after round 1, minus GROUP_RESERVE for the
# grouping call; skipped below MIN_LOOP seconds. Starting values, tuned in live runs (4A.9 q7).
TOTAL_SECONDS = 60.0
GROUP_RESERVE = 15.0
MIN_LOOP = 8.0
LOOP_STEPS = 4  # model calls in the loop (RunConfig.max_llm_calls)
LOOP_CAPS = {"web_search": 2, "court_opinions": 1, "openalex_authors": 1}  # extra calls per search
# The round-1 source each tool calls again: a tool is offered only if that source answered in round 1.
TOOL_SOURCE = {"web_search": "web_search", "court_opinions": "courtlistener", "openalex_authors": "openalex"}
# Where a failed tool call is reported in `failed_sources` (same keys as round 1).
TOOL_FAILURE = {"web_search": "web_search_query", "court_opinions": "courtlistener", "openalex_authors": "openalex"}
NO_FULL_NAME = "refused: the query must contain the searched person's full name"
OTHER_NAME = "refused: the name must have the searched given and family name"
BLOCKED_SITE = "refused: the query names a site we must never search (gotcha 2)"
# Hard caps on AI-written text, in characters, enforced in code.
LIMITS = {"display_name": 120, "summary": 300, "field": 120, "location": 120, "affiliation": 200,
          "match_notes": 300, "note": 200, "title": 200}
SIGNAL_ORDER = ("court_opinion_expert", "expert_directory", "web_expert_witness")  # strongest first
# Whole word "expert"/"experts" (also in expert-witness, expert_witness); not expertise, inexpert, expertly.
EXPERT_TERM = re.compile(r"(?<![a-z0-9])experts?(?![a-z0-9])", re.IGNORECASE)
FIELD_ONLY_NOTE = "merged on field only - verify"
SPLIT_NOTE = "Kept apart: the listings name different US states"
APART_NOTE = "Kept apart: the listings share no field or website"
SUMMARY_REMOVED = "Summary removed: the AI text repeated source page text."

OnStep = Callable[..., None]  # on_step(step, status, detail=None)


class FindCandidatesError(Exception):
    """The AI grouping step failed. `failed_sources` lists every failed source plus the AI step."""

    def __init__(self, message: str, failed_sources: list[FailedSource]):
        super().__init__(message)
        self.failed_sources = failed_sources


# What the AI returns when grouping hits into people.


class EvidenceRef(BaseModel):
    ref: str
    note: str | None
    location: str | None = None  # where this result itself places the person


class Person(BaseModel):
    display_name: str
    summary: str
    field: str | None
    affiliations: list[str]
    location: str | None
    evidence: list[EvidenceRef]
    match_notes: str | None


class Grouping(BaseModel):
    people: list[Person]


_TEXT_OR_NULL = {"type": ["string", "null"]}
GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["people"],
    "properties": {"people": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["display_name", "summary", "field", "affiliations", "location", "evidence", "match_notes"],
        "properties": {
            "display_name": {"type": "string"},
            "summary": {"type": "string"},
            "field": _TEXT_OR_NULL,
            "affiliations": {"type": "array", "items": {"type": "string"}},
            "location": _TEXT_OR_NULL,
            "evidence": {"type": "array", "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ref", "note", "location"],
                "properties": {"ref": {"type": "string"}, "note": _TEXT_OR_NULL, "location": _TEXT_OR_NULL},
            }},
            "match_notes": _TEXT_OR_NULL,
        },
    }}},
}

GROUP_INSTRUCTIONS = (
    "You find EXPERT WITNESSES for a legal research tool. Group numbered search results (E1, E2, ...) "
    "into the distinct real people whose full name matches the searched name exactly. Use only the "
    "results given; never add facts. List expert witnesses, litigation consultants and other "
    "professionals with this name; a professional's own practice or profile page counts, because "
    "experts' own sites often never say 'expert witness'. Leave out people who appear only as a party, "
    "judge or lawyer in a case, listing or index pages that are not about one person, and unrelated "
    "news. For each person list the result numbers about them, each with a short note in your own "
    "words on what that result shows (do not copy text) and the location that result itself gives "
    "for the person as \"City, ST\" (null if it gives none). Put results in one person only when they "
    "share a concrete detail (same firm or practice, same CV or bio text, same credentials, same "
    "institution, or the same specialty in the same state), never on the name alone; if "
    "unsure, keep them separate. Expert-directory profiles (e.g. SEAK, IMENet, JurisPro) and practice "
    "pages with such a shared detail are one person. A different city alone does not prove a different "
    "person. User hints may support a merge together with a shared detail, never alone. OpenAlex "
    "records are supporting evidence only: attach one to a person only on such a shared detail, never "
    "list a person backed only by OpenAlex records. Put two OpenAlex records in one person only if they "
    "look like the same person, and say why in match_notes. summary: one sentence about their expert "
    "work (field, typical case types), or their profession if the results show no expert work, from "
    "the results only. Professional facts only: no address, family, health, age or character "
    "judgments. Use null when unknown."
)


FOLLOWUP_INSTRUCTIONS = (
    "You help a legal research tool find EXPERT WITNESSES. Numbered search results (E1, E2, ...) for one "
    "searched name follow, with any user hints. Call a tool only if it would help tell same-name people "
    "apart or find their expert work: a name variant, a hint, an employer or field seen in a result. "
    "web_search(query): one web search; every query must contain the full searched name. "
    "court_opinions(full_name) and openalex_authors(full_name): court opinions naming the person as an expert, and "
    "scholarly author records, for a variant of the searched name (same given and family name). "
    "Never search LinkedIn, Google Scholar, Daubert Tracker, TrialSmith, CPAverify, BrokerCheck, ABMS or "
    "DocInfo. Professional information only: no home address, family, health or age. "
    "Reply DONE when nothing new is likely."
)


# Sources


async def run_source(key: str, fetch: Callable[[], Awaitable[list[dict]]], on_step: OnStep):
    """Run one source; failure or skip becomes a FailedSource, never an exception."""
    on_step(key, "running")
    try:
        async with asyncio.timeout(SOURCE_TIMEOUT):
            hits = await fetch()
    except SourceSkipped as e:
        on_step(key, "skipped", str(e))
        return [], FailedSource(source=key, reason=str(e))
    except Exception as e:  # noqa: BLE001 - any source error is a gap, not a crash (M2 2d)
        reason = "timed out" if isinstance(e, TimeoutError) else (str(e) or type(e).__name__)
        on_step(key, "failed", reason)
        return [], FailedSource(source=key, reason=reason)
    on_step(key, "done", f"{len(hits)} results")
    return hits, None


def describe(ref: str, hit: dict) -> str:
    """One prompt line per hit. Snippets are used here only, in memory."""
    parts = [f"{ref} [{hit['source']}] {hit['title']}", hit["url"]]
    if hit["source"] == "openalex":
        parts += [f"name: {hit['name']}", f"ORCID: {hit['orcid'] or 'none'}", hit["note"],
                  "topics: " + ", ".join(hit["topics"])]
    elif hit.get("note"):
        parts.append(hit["note"])
    if hit["source"] == "courtlistener":
        parts.append("found by: name near expert-role words")
    elif hit["source"] == "web":
        kind = hit["query"]
        parts.append("found by: " + ("expert-witness search" if kind in websearch.EXPERT_QUERIES
                                     else "follow-up search" if kind == "follow-up" else "name search"))
    if hit.get("snippet"):
        parts.append("excerpt: " + hit["snippet"])
    return " | ".join(parts)


def group_input(name: str, hints: dict | None, hits: list[dict]) -> str:
    extra = ", ".join(f"{k}: {v}" for k, v in (hints or {}).items() if v)
    head = f'Searched name: "{name}"' + (f"\nUser hints (may be wrong; never the only reason to merge): {extra}" if extra else "")
    return head + "\n\nResults:\n" + "\n".join(describe(f"E{i}", h) for i, h in enumerate(hits, 1))


# Follow-up loop (ADK agent, DESIGN 4A.1-4A.4): tools add hits to the evidence store; its text is dropped.


def store_hits(run: Run, hits: list[dict]) -> list[str]:
    """Add a tool's new hits to the evidence store under the next E numbers. Gotcha 2 domains, URLs
    already stored and a case already stored are dropped. Returns the lines the model sees."""
    cases = {h["case"] for h in run.evidence.values() if h.get("case") and all(h["case"])}
    lines = []
    for hit in hits:
        case = hit.get("case")
        if is_blocked(hit["url"]) or run.has_url(hit["url"]) or (case and all(case) and case in cases):
            continue
        ref = run.add(hit)
        lines.append(describe(ref, run.get(ref)))
    return lines


def followup_tools(run: Run, searched: str) -> list[FunctionTool]:
    """The loop's tools, closures over `run`; `run.tool` adds the per-tool cap, the deadline and the
    never-raise rule. Only tools named in `run.caps` are offered."""

    async def web_search(query: str) -> dict:
        """One more web search. The query must contain the person's full name. Returns new results."""
        query = query[:websearch.QUERY_MAX]  # cut first: the checks below see exactly what is sent
        if not has_full_name(query, searched):
            return refuse(NO_FULL_NAME)
        if websearch.names_blocked_site(query):
            return refuse(BLOCKED_SITE)
        return {"results": store_hits(run, await websearch.search_query(searched, query))}

    async def court_opinions(full_name: str) -> dict:
        """Court opinions naming this person near an expert-role word. `full_name`: a variant of the
        searched name (same given and family name, e.g. with a middle initial)."""
        if not same_person_name(full_name, searched):
            return refuse(OTHER_NAME)
        return {"results": store_hits(run, await courtlistener.search_opinions(full_name))}

    async def openalex_authors(full_name: str) -> dict:
        """Scholarly author records for a variant of the searched name (same given and family name)."""
        if not same_person_name(full_name, searched):
            return refuse(OTHER_NAME)
        return {"results": store_hits(run, await openalex.search_authors(full_name))}

    tools = (web_search, court_opinions, openalex_authors)
    return [FunctionTool(run.tool(fn)) for fn in tools if fn.__name__ in run.caps]


async def follow_leads(run: Run, name: str, hints: dict | None) -> str | None:
    """Run the candidate_followup agent within its step cap and the run's time budget. Returns a note
    for the step detail (None when it called nothing). A stop or a model failure is never an error:
    the grouping still runs on what the store holds."""
    tools = followup_tools(run, name)
    if not tools:
        return None
    start = run.count
    try:
        agent = llm.make_agent("candidate_followup", FOLLOWUP_INSTRUCTIONS, tools=tools)
        result = await llm.run_agent(agent, group_input(name, hints, list(run.evidence.values())),
                                     max_llm_calls=LOOP_STEPS, timeout=run.seconds)
    except llm.ModelError as e:
        return f"follow-up failed: {e}"
    calls = sum(run.used.values())
    if not calls and not result.stopped:
        return None
    stop = {"step_limit": ", stopped at the step limit", "time_limit": ", stopped at the time limit"}
    return f"follow-up: {calls} source calls, {run.count - start} new results{stop.get(result.stopped, '')}"


def tool_failures(run: Run) -> list[FailedSource]:
    """Failed tool calls, reported like round 1 (fixed text: tool and exception type). A call cut off
    by the time budget is not a source failure."""
    return [FailedSource(source=TOOL_FAILURE[tool], reason=f"follow-up {tool} call failed ({kind})")
            for tool, kind in run.failures if kind != "TimeoutError"]


# Merge rule and candidate building (code, not the AI)


def can_merge(a: dict, b: dict) -> bool:
    """DESIGN 4.1: merge OpenAlex ids only on a shared ORCID (a) or the same given/family name (b).
    Two different ORCIDs never merge."""
    if a["orcid"] and b["orcid"]:
        return a["orcid"] == b["orcid"]
    return same_person_name(a["name"], b["name"])


def cluster_openalex(hits: list[dict]) -> list[list[dict]]:
    """Split the OpenAlex hits the AI put together into allowed groups; every pair must pass can_merge."""
    groups: list[list[dict]] = []
    for hit in hits:
        for members in groups:
            if all(can_merge(m, hit) for m in members):
                members.append(hit)
                break
        else:
            groups.append([hit])
    return groups


def merged_by_orcid(members: list[dict]) -> bool:
    orcids = {m["orcid"] for m in members}
    return len(orcids) == 1 and None not in orcids


# AI-written text: hard length caps, and nothing copied from a source snippet (gotcha 15).


def clean(text: str | None, limit: int, snippets: str) -> str | None:
    if not text or copied(text, snippets):
        return None
    return text[:limit]


def clean_person(person: Person, snippets: str, searched_name: str) -> Person:
    return person.model_copy(update={
        "display_name": clean(person.display_name, LIMITS["display_name"], snippets) or searched_name,
        "summary": clean(person.summary, LIMITS["summary"], snippets) or SUMMARY_REMOVED,
        "field": clean(person.field, LIMITS["field"], snippets),
        "location": clean(person.location, LIMITS["location"], snippets),
        "affiliations": [a for a in (clean(x, LIMITS["affiliation"], snippets) for x in person.affiliations) if a][:5],
        "match_notes": clean(person.match_notes, LIMITS["match_notes"], snippets),
        "evidence": [r.model_copy(update={"note": clean(r.note, LIMITS["note"], snippets),
                                          "location": clean(r.location, LIMITS["location"], snippets)})
                     for r in person.evidence],
    })


def expert_signals(hits: list[dict]) -> list[str]:
    """Expert evidence codes, strongest first, from source data only (title, URL, which source):
    never from AI-written text. OpenAlex is never a signal on its own."""
    found = set()
    for hit in hits:
        if hit["source"] == "courtlistener":  # the search requires expert-role words near the name
            found.add("court_opinion_expert")
        elif hit["source"] == "web":
            if is_expert_directory(hit["url"]):
                found.add("expert_directory")
            host = (host_of(hit["url"]) or "").replace("-", "")
            if EXPERT_TERM.search(f"{hit['title']} {hit['url']}") or "expertwitness" in host:
                found.add("web_expert_witness")
    return [s for s in SIGNAL_ORDER if s in found]


def same_full_name(display_name: str, searched: str) -> bool:
    """Same given and family name, case-insensitive, titles and suffixes ignored; middle names and
    initials may differ. A surname alone never matches."""
    given, family = given_family(searched)
    return bool(given) and given != family and given_family(display_name) == (given, family)


def dedupe_cases(hits: list[dict]) -> list[dict]:
    """One CourtListener hit per case (same case name, court and filing date): the lowest opinion id."""
    def key(h: dict):
        case = h.get("case") if h["source"] == "courtlistener" else None
        return case if case and all(case) else None

    best: dict = {}
    for h in hits:
        if (k := key(h)) and (k not in best or (h.get("opinion_id") or 0) < (best[k].get("opinion_id") or 0)):
            best[k] = h
    return [h for h in hits if not (k := key(h)) or best[k] is h]


def hint_matches(candidate: Candidate, hints: dict | None) -> int:
    """0-2: the field hint shares a specialty word with the candidate's field; the location hint
    names the same US state as the candidate's location."""
    hints = hints or {}
    state = state_of(hints.get("location"))
    return bool(words(hints.get("field")) & words(candidate.field)) + bool(state and state == state_of(candidate.location))


def rank(candidate: Candidate, hints: dict | None = None) -> tuple:
    """Sort key. Candidates matching a field or location hint come first, across tiers. Within that
    group: court beats any mix without it, then directory, then web, then people with no signal;
    then more hint matches rank higher."""
    matches = hint_matches(candidate, hints)
    return (not matches, *(s not in candidate.expert_signals for s in SIGNAL_ORDER), -matches)


def guard_listings(person: Person, kept: list, where: dict) -> tuple[list[list], str | None]:
    """Code merge guard for a person resting on 2+ web listings: (evidence groups, note).
    A merge needs a shared detail code can see: one website for all listings (not a multi-person
    expert directory), else the person's field (FIELD_ONLY_NOTE); with neither, each listing stays
    apart. Listings whose own location (`where`) names different US states are split one group per
    state; listings with no state stay with the court and OpenAlex evidence."""
    web = [p for p in kept if p[0]["source"] == "web"]
    if len(web) < 2:
        return [kept], None
    other = [p for p in kept if p[0]["source"] != "web"]
    if len({(host_of(h["url"]) or "").removeprefix("www.") for h, _ in web}) == 1 and not is_expert_directory(web[0][0]["url"]):
        note = None
    elif words(person.field):
        note = FIELD_ONLY_NOTE
    else:
        return [[p] for p in web] + [other], APART_NOTE
    by_state: dict = {}
    for p in web:
        by_state.setdefault(state_of(where.get(id(p[0]))), []).append(p)
    if len(by_state.keys() - {None}) < 2:
        return [kept], note
    rest = other + by_state.pop(None, [])
    return [*by_state.values(), rest], "; ".join(n for n in (note, SPLIT_NOTE) if n)


def make_candidate(person: Person, picked: list[tuple[dict, str | None]], notes: list[str | None]) -> Candidate:
    oa = [h for h, _ in picked if h["source"] == "openalex"]
    return Candidate(
        display_name=person.display_name,
        summary=person.summary,
        field=person.field or next((h["field"] for h in oa if h["field"]), None),
        affiliations=person.affiliations or next((h["affiliations"] for h in oa if h["affiliations"]), []),
        location=person.location,
        identifiers=Identifiers(openalex=[h["openalex_id"] for h in oa],
                                orcid=next((h["orcid"] for h in oa if h["orcid"]), None)),
        evidence=[Evidence(source=h["source"], url=h["url"], title=h["title"][:LIMITS["title"]], note=note or h["note"])
                  for h, note in picked],
        match_notes="; ".join(n for n in notes if n) or None,
        expert_signals=expert_signals([h for h, _ in picked]),
    )


def build_candidates(grouping: Grouping, hits: list[dict], searched_name: str,
                     hints: dict | None = None) -> list[Candidate]:
    snippets = "\n".join(squash(h["snippet"]) for h in hits if h.get("snippet"))
    candidates = []
    for person in grouping.people:
        person = clean_person(person, snippets, searched_name)
        if not same_full_name(person.display_name, searched_name):
            continue  # code-side exact full-name check
        picked, seen, where = [], set(), {}
        for ref in person.evidence:
            m = re.fullmatch(r"E(\d+)", ref.ref.strip())
            i = int(m.group(1)) if m else 0
            if 1 <= i <= len(hits) and i not in seen:  # unknown numbers are dropped
                seen.add(i)
                picked.append((hits[i - 1], ref.note))
                where[id(hits[i - 1])] = ref.location
        if not picked:
            continue  # a person with no evidence is dropped
        oa_groups = cluster_openalex([h for h, _ in picked if h["source"] == "openalex"])
        # OpenAlex records the merge rule keeps apart are left out: they are never candidates alone.
        dropped = {id(h) for g in oa_groups[1:] for h in g}
        kept = [(h, n) for h, n in picked if id(h) not in dropped]
        code_notes = [person.match_notes]
        if oa_groups and len(oa_groups[0]) > 1:
            code_notes.append("OpenAlex ids merged: same ORCID" if merged_by_orcid(oa_groups[0])
                              else "merged by name only")
        if dropped:
            code_notes.append(f"{len(dropped)} OpenAlex record(s) left out: different ORCIDs or the names differ")
        groups, guard_note = guard_listings(person, kept, where)
        code_notes.append(guard_note)
        for group in groups:
            if all(h["source"] == "openalex" for h, _ in group):
                continue  # OpenAlex alone (or nothing) is never a candidate
            who = person
            if len(groups) > 1:  # kept apart: the location is this group's own listing location
                own = [where.get(id(h)) for h, _ in group]
                who = person.model_copy(update={"location": next((x for x in own if state_of(x)), None)})
            candidates.append(make_candidate(who, group, code_notes))
    # Rank (experts by signal strength first, then people without expert evidence), then cap.
    return sorted(candidates, key=lambda c: rank(c, hints))[:MAX_CANDIDATES]


async def find_candidates(name: str, hints: dict | None, on_step) -> tuple[list[Candidate], list[FailedSource]]:
    """Round 1: OpenAlex, CourtListener and Meta web search in parallel (code). Then, inside the
    `group_candidates` step, the follow-up agent may call them again, and the AI groups people."""
    started = time.monotonic()
    web_failed: list[str] = []  # web queries that failed while others answered
    results = await asyncio.gather(
        run_source("openalex", lambda: openalex.search_authors(name), on_step),
        run_source("courtlistener", lambda: courtlistener.search_opinions(name), on_step),
        run_source("web_search", lambda: websearch.search_people(name, hints, web_failed), on_step),
    )
    hits = dedupe_cases([h for found, _ in results for h in found])
    failed = [f for _, f in results if f]
    failed += [FailedSource(source="web_search_query", reason=r) for r in web_failed]

    on_step("group_candidates", "running")
    if not hits:
        on_step("group_candidates", "done", "no results to group")
        return [], failed
    seconds = TOTAL_SECONDS - (time.monotonic() - started) - GROUP_RESERVE
    answered = set(TOOL_SOURCE.values()) - {f.source for f in failed}
    run = Run(seconds, {t: n for t, n in LOOP_CAPS.items() if TOOL_SOURCE[t] in answered})
    for hit in hits:  # every hit goes through the evidence store: E1..En, in prompt order
        run.add(hit)
    loop_note = await follow_leads(run, name, hints) if seconds >= MIN_LOOP else "follow-up skipped: no time left"
    failed += tool_failures(run)
    hits = list(run.evidence.values())
    try:
        reply = await llm.call_model(GROUP_INSTRUCTIONS, group_input(name, hints, hits),
                                     schema=GROUP_SCHEMA, output_type=Grouping)
    except llm.ModelError as e:
        on_step("group_candidates", "failed", str(e))
        failed.append(FailedSource(source="group_candidates", reason=str(e)))
        raise FindCandidatesError(f"AI grouping step failed: {e}", failed) from e
    candidates = build_candidates(reply.data, hits, name, hints)
    on_step("group_candidates", "done", "; ".join(n for n in (f"{len(candidates)} candidates", loop_note) if n))
    return candidates, failed
