"""Tool 2: sites for a state (DESIGN 4B.9, memory 3). Which public websites hold court, licence or
discipline records for one US state. Saved sites come from memory; a state with none, or whose last
discovery is older than STALE_DAYS, is discovered again and merged in (nothing saved is removed).
Discovery: three web searches (courts, medical board, licensing agency). Domains come only from their
results, never from model text; the model only labels them, and only official, useful sites
are kept. site_guide adds an official site it read for a state (`notes` LEARNED), so the list grows
with every run. Snippets and search notes stay in memory (gotcha 15)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta

from app import llm, memory
from app.evidence import refuse
from app.sources import US_STATES, host_of, is_blocked, state_of
from app.tools import Ctx, tool

PER_SEARCH = 5  # results kept from each search, so one topic cannot crowd out the others
SNIPPET_MAX = 300
NOTES_MAX = 4_000
STATE_NAMES = {code: name.title() for name, code in US_STATES.items()}
UNKNOWN_STATE = "unknown state: give a two-letter code or a full US state name"
NO_SITES = "no record sites found for {}"
RELIABILITY = ["high", "medium", "low", "unknown"]
STALE_DAYS = 90  # a state whose last discovery is older is discovered again
# `notes` of a saved site: where it came from. Only "found:" rows date the last discovery.
FROM_SEARCH, LEARNED = "found: web search", "learned: site_guide"
QUERIES = [  # tuned on live runs for AZ, IL, NC and TX (2026-11-01)
    "{} judicial branch official website: court case search and records",
    "{} medical board official website: physician licence verification and discipline",
    "{} state professional licensing department official website: licence lookup and disciplinary actions",
]

SEARCH_INSTRUCTIONS = (
    "You help a legal research tool find official public record websites. Run a web search for the "
    "query given. Then list each official site found, one line each: the URL and what records it holds. "
    "List only official sites (state courts, county clerks of court, state agencies and licensing boards; "
    "most use .gov, some boards use .org), never articles, guides, directories or record-search companies "
    "that link to them. Skip people-search, data-broker and paid record sites, and LinkedIn, Google "
    "Scholar, Daubert Tracker, TrialSmith, CPAverify, BrokerCheck, ABMS and DocInfo."
)
LABEL_INSTRUCTIONS = (
    "You read numbered web search results (S1, S2, ...) and search notes about public record websites "
    "for one US state. For each result say, in a few words of your own, what records the site holds "
    "(for example \"county trial court dockets\" or \"physician licence lookup\"), and how reliable it "
    "is: high (the official court, agency or board site itself), medium (another government or official "
    "site), low (unofficial: directories, guides, articles, record vendors) or unknown. Set useful to "
    "true if the site is where court cases or professional licences or discipline are looked up or held "
    "(a court's or licensing agency's own home site counts). Each result says whether its address is an "
    "official government address; a site that is not needs clear signs it is the court or board itself "
    "(not a look-alike vendor) to be rated high. Set people_search to true for people-search, data-broker "
    "or paid record sites. Use only the results and notes given."
)
LABEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sites"],
    "properties": {"sites": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ref", "what", "reliability", "useful", "people_search"],
        "properties": {
            "ref": {"type": "string"},
            "what": {"type": "string"},
            "reliability": {"type": "string", "enum": RELIABILITY},
            "useful": {"type": "boolean"},
            "people_search": {"type": "boolean"},
        },
    }}},
}


def state_code(state: str) -> str | None:
    """"OH", "oh", "Ohio" -> "OH"; anything else None."""
    state = state.strip()
    return state_of(state.upper() if len(state) == 2 else state) if state else None


def is_official(domain: str) -> bool:
    """A government address: .gov, or a state .us name like state.tx.us (not look-alikes such as
    arizonacourt.us, which are record vendors)."""
    return domain.endswith(".gov") or bool(re.search(r"\.[a-z]{2}\.us$", domain))


def site_rows(state: str) -> list[dict]:
    """Saved sites for `state` rated high (older rows rated lower are hidden), official ones first,
    with their blocked reason (memory 4)."""
    rows = sorted((r for r in memory.sites_in(state) if r["reliability"] == "high"),
                  key=lambda r: (not is_official(r["domain"]), r["domain"]))
    return [{"domain": r["domain"], "what": r["what"], "reliability": r["reliability"],
             "blocked": memory.blocked(r["domain"])} for r in rows]


def needs_discovery(state: str, sites: list[dict]) -> bool:
    """No sites to show, or no web search discovery in the last STALE_DAYS."""
    found = [r["updated_at"] for r in memory.sites_in(state) if (r["notes"] or "").startswith("found:")]
    cutoff = (datetime.now(UTC) - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return not sites or not found or max(found) < cutoff


def domains(found: llm.ModelResult, seen: set[str]) -> list[tuple[str, dict]]:
    """Up to PER_SEARCH new, allowed (domain, result) pairs from one search, official ones first."""
    hits = []
    for item in found.results or found.citations:
        url = item.get("url") or ""
        domain = memory.domain_of(host_of(url) or "") if url.startswith(("http://", "https://")) else ""
        if domain and not is_blocked(url) and domain not in seen:
            seen.add(domain)
            hits.append((domain, item))
    return sorted(hits, key=lambda h: not is_official(h[0]))[:PER_SEARCH]


async def find_sites(ctx: Ctx, state: str) -> None:
    """Three web searches at once, then one labelling call; saves only official, useful sites
    (merged with the saved ones)."""
    name = STATE_NAMES[state]
    founds = await asyncio.gather(*[
        llm.call_model(SEARCH_INSTRUCTIONS, f"Search query: {q.format(name)}", web_search=True) for q in QUERIES])
    seen: set[str] = set()
    hits = [hit for found in founds for hit in domains(found, seen)]
    if not hits:
        return
    lines = [f"State: {name} ({state})", "Results:"]
    lines += [f"S{i}. {d} ({'official' if is_official(d) else 'not official'} address) | "
              f"{(h.get('title') or '')[:200]} | excerpt: {(h.get('snippet') or '(none)')[:SNIPPET_MAX]}"
              for i, (d, h) in enumerate(hits, 1)]
    notes = "\n".join(f.text.strip() for f in founds if f.text.strip())
    if notes:
        lines += ["Search notes:", notes[:NOTES_MAX]]
    labels = await ctx.ask_model(LABEL_INSTRUCTIONS, "\n".join(lines), LABEL_SCHEMA)
    for label in labels.get("sites", []):
        ref = str(label.get("ref", "")).strip().upper().removeprefix("S")
        # unknown refs, labels dropped by the copy-check and anything but official, useful,
        # non-people-search sites are never saved
        if not ref.isdigit() or not 1 <= int(ref) <= len(hits) or not label.get("what"):
            continue
        if label.get("reliability") != "high" or label.get("useful") is not True or label.get("people_search") is not False:
            continue
        memory.save_state_site(state, hits[int(ref) - 1][0], label["what"], "high", FROM_SEARCH)


@tool(label="Sites for a state", cap=3)
async def sites_for_state(ctx: Ctx, state: str) -> dict:
    """List public websites that hold court, licence or discipline records for one US state, so you
    can search them with the other tools. `state` is a two-letter code ("OH") or a full state name.
    Official (.gov) sites come first; `blocked` says a site must not be read (and why). Sites are
    remembered and the list grows with every run, so asking again for the same state is usually free."""
    code = state_code(state)
    if not code:
        return refuse(UNKNOWN_STATE)
    sites = site_rows(code)
    from_memory = not needs_discovery(code, sites)
    if not from_memory:
        await find_sites(ctx, code)
        sites = site_rows(code)
    if not sites:
        ctx.add_gap(NO_SITES.format(code))
    ctx.log(f"{len(sites)} sites" + (" (from memory)" if from_memory else ""))
    return {"state": code, "sites": sites, "from_memory": from_memory}
