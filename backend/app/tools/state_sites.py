"""Tool 2: sites for a state (DESIGN 4B.9, memory 3). Which public websites hold court, licence or
discipline records for one US state. Domains come only from web search results, never from model text;
the model only labels them. Snippets and search notes stay in memory (gotcha 15)."""

from app import llm, memory
from app.evidence import refuse
from app.sources import US_STATES, host_of, is_blocked, state_of
from app.tools import Ctx, tool

MAX_SITES = 10
SNIPPET_MAX = 300
NOTES_MAX = 4_000
STATE_NAMES = {code: name.title() for name, code in US_STATES.items()}
UNKNOWN_STATE = "unknown state: give a two-letter code or a full US state name"
NO_SITES = "no record sites found for {}"
RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3}

SEARCH_INSTRUCTIONS = (
    "You help a legal research tool find official public record websites. Run a web search for the "
    "query given. Then, for each official site found, write one line: the URL and what records it "
    "holds. Prefer state government (.gov) and court sites. Skip people-search, data-broker and paid "
    "record sites, and LinkedIn, Google Scholar, Daubert Tracker, TrialSmith, CPAverify, BrokerCheck, "
    "ABMS and DocInfo."
)
LABEL_INSTRUCTIONS = (
    "You read numbered web search results (S1, S2, ...) and search notes about public record websites "
    "for one US state. For each result, say in your own words, in a few words, what records the site "
    "holds (for example \"county trial court dockets\" or \"medical licence lookup\"), and how reliable "
    "it is as a source of official records: high (the official court, agency or board site), medium "
    "(another government or official site), low (unofficial) or unknown. Set people_search to true for "
    "people-search, data-broker or paid record sites. Use only the results and notes given."
)
LABEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sites"],
    "properties": {"sites": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ref", "what", "reliability", "people_search"],
        "properties": {
            "ref": {"type": "string"},
            "what": {"type": "string"},
            "reliability": {"type": "string", "enum": list(RANK)},
            "people_search": {"type": "boolean"},
        },
    }}},
}


def state_code(state: str) -> str | None:
    """"OH", "oh", "Ohio" -> "OH"; anything else None."""
    state = state.strip()
    return state_of(state.upper() if len(state) == 2 else state) if state else None


def is_official(domain: str) -> bool:
    return domain.endswith((".gov", ".us"))


def site_rows(state: str) -> list[dict]:
    """Saved sites for `state`, official ones first, with their blocked reason (memory 4)."""
    rows = sorted(memory.sites_in(state), key=lambda r: (not is_official(r["domain"]), RANK[r["reliability"]]))
    return [{"domain": r["domain"], "what": r["what"], "reliability": r["reliability"],
             "blocked": memory.blocked(r["domain"])} for r in rows]


async def find_sites(ctx: Ctx, state: str) -> None:
    """One web search, then one labelling call; saves the labelled domains."""
    found = await llm.call_model(
        SEARCH_INSTRUCTIONS,
        f"Search query: {STATE_NAMES[state]} official court records search, professional licence lookup "
        "and licensing board discipline",
        web_search=True)
    hits, seen = [], set()
    for item in found.results or found.citations:
        url = item.get("url") or ""
        domain = memory.domain_of(host_of(url) or "") if url.startswith(("http://", "https://")) else ""
        if not domain or is_blocked(url) or domain in seen:
            continue
        seen.add(domain)
        hits.append((domain, item))
    hits = hits[:MAX_SITES]
    if not hits:
        return
    lines = [f"State: {STATE_NAMES[state]} ({state})", "Results:"]
    lines += [f"S{i}. {d} | {(h.get('title') or '')[:200]} | excerpt: {(h.get('snippet') or '(none)')[:SNIPPET_MAX]}"
              for i, (d, h) in enumerate(hits, 1)]
    if found.text.strip():
        lines += ["Search notes:", found.text.strip()[:NOTES_MAX]]
    labels = await ctx.ask_model(LABEL_INSTRUCTIONS, "\n".join(lines), LABEL_SCHEMA)
    for label in labels.get("sites", []):
        ref = str(label.get("ref", "")).strip().upper().removeprefix("S")
        # unknown refs, people-search sites and labels dropped by the copy-check are never saved
        if not ref.isdigit() or not 1 <= int(ref) <= len(hits) or label.get("people_search") is not False:
            continue
        if not label.get("what") or label.get("reliability") not in RANK:
            continue
        memory.save_state_site(state, hits[int(ref) - 1][0], label["what"], label["reliability"])


@tool(label="Sites for a state", cap=3)
async def sites_for_state(ctx: Ctx, state: str) -> dict:
    """List public websites that hold court, licence or discipline records for one US state, so you
    can search them with the other tools. `state` is a two-letter code ("OH") or a full state name.
    Official (.gov) sites come first; `blocked` says a site must not be read (and why). Sites are
    remembered, so asking again for the same state is free."""
    code = state_code(state)
    if not code:
        return refuse(UNKNOWN_STATE)
    from_memory = bool(memory.sites_in(code))
    if not from_memory:
        await find_sites(ctx, code)
    sites = site_rows(code)
    if not sites:
        ctx.add_gap(NO_SITES.format(code))
    ctx.log(f"{len(sites)} sites" + (" (from memory)" if from_memory else ""))
    return {"state": code, "sites": sites, "from_memory": from_memory}
