"""Meta built-in web search for the candidate finder (DESIGN 4.1) and the research collectors
(DESIGN 4.2, 4.4); gotchas 2, 14, 15.

Snippets are third-party page text: they stay in memory for the grouping prompt and are never
stored or returned. Only URL, title and our own extracted facts leave this step."""

import asyncio
import re

from app import llm
from app.evidence import copied, squash
from app.sources import has_full_name, host_of, is_blocked, is_listing

MAX_HITS = 10  # per query
TITLE_MAX = 200  # web titles are third-party text: keep them short
EXPERT_QUERIES = frozenset({"expert", "hint"})  # query kinds that ask for expert witnesses
QUERY_MAX = 200  # characters of a follow-up query the model writes

INSTRUCTIONS = (
    "You help a legal research tool find public pages about expert witnesses. Run a web search "
    "for the query given and briefly list the people with this exact name that the results show "
    "(expert profiles, firm or faculty pages, court or news mentions as an expert). "
    "Do not use LinkedIn, Google Scholar, Daubert Tracker, TrialSmith, CPAverify, BrokerCheck, "
    "ABMS or DocInfo. Professional information only: no home address, family, health or age."
)


def build_queries(name: str, hints: dict | None) -> list[tuple[str, str]]:
    """(kind, query) pairs: "expert" and "hint" ask for expert witnesses; "bare" (the name alone) and
    "field-location" are there because experts' own sites often never say "expert witness"."""
    quoted = '"' + name.replace('"', "") + '"'
    queries = [("expert", f"{quoted} expert witness"), ("bare", quoted)]
    hints = {k: str(v).replace('"', "") for k, v in (hints or {}).items() if v}
    if hints:
        queries.append(("hint", f"{quoted} expert witness " + " ".join(hints.values())))
    if place := " ".join(hints[k] for k in ("field", "location") if k in hints):
        queries.append(("field-location", f"{quoted} {place}"))
    return queries


def to_hits(items: list[dict], query: str = "bare", name: str | None = None) -> list[dict]:
    """Drop blocked domains, listing pages and duplicate URLs (before the per-query cap); keep URL,
    title and the in-memory snippet."""
    hits, seen = [], set()
    for item in items:
        url = item.get("url") or ""
        title = (item.get("title") or url)[:TITLE_MAX]
        if not url.startswith(("http://", "https://")) or is_blocked(url) or url in seen:
            continue
        if is_listing(url, title, name):
            continue
        seen.add(url)
        hits.append({
            "source": "web",
            "url": url,
            "title": title,
            "note": None,
            "query": query,  # the query kind that returned it (see build_queries)
            "snippet": (item.get("snippet") or "")[:600],  # prompt only, never stored or returned
        })
    return hits[:MAX_HITS]


def names_blocked_site(query: str) -> bool:
    """True if the query names a gotcha 2 site, with `site:` or as a bare domain (same list as
    `is_blocked`), so a model-written query never asks the search for those sites."""
    for token in query.split():
        domain = re.sub(r"(?i)^[-+(\"']*(site:)?", "", token).strip("\"')")
        url = "https://" + domain.split("/")[0]
        if "." in domain and host_of(url) and is_blocked(url):  # an unreadable host is not a site
            return True
    return False


async def search_query(name: str, query: str, kind: str = "follow-up") -> list[dict]:
    """One web search query (DESIGN 4A.4: the candidate loop's `web_search` tool, and each query of
    `search_people`). Returns hits with blocked domains, listings and repeats already dropped."""
    reply = await llm.call_model(INSTRUCTIONS, f"Search query: {query[:QUERY_MAX]}", web_search=True)
    # `results` covers every page the model saw; citations are a subset, used if results are missing.
    return to_hits(reply.results or reply.citations, kind, name)


async def search_people(name: str, hints: dict | None, failed_queries: list[str] | None = None) -> list[dict]:
    """Run every query in parallel. All failing raises the first error. If only some fail, the rest
    is used and one reason per failed query is added to `failed_queries`."""
    queries = build_queries(name, hints)
    replies = await asyncio.gather(*(search_query(name, q, kind) for kind, q in queries), return_exceptions=True)
    errors = [r for r in replies if isinstance(r, BaseException)]
    if len(errors) == len(replies):
        raise errors[0]
    if failed_queries is not None:
        failed_queries += [f"{kind} query failed: {r or type(r).__name__}"
                           for (kind, _), r in zip(queries, replies) if isinstance(r, BaseException)]
    hits, by_url = [], {}
    for (kind, _), found in zip(queries, replies):
        if isinstance(found, BaseException):
            continue
        for hit in found:
            if hit["url"] in by_url:
                if kind in EXPERT_QUERIES:  # the same page found by an expert query counts as such
                    by_url[hit["url"]]["query"] = kind
                continue
            by_url[hit["url"]] = hit
            hits.append(hit)
    return hits


# Research collectors: short facts about one person for a topic, from web results (DESIGN 4.2, 4.4).
FACT_MAX = 200  # characters per fact
FACTS_PER_RESULT = 3
NOTES_MAX = 4000  # characters of the search model's notes per query (prompt only, never stored)
# Quoted text in the notes is page text: a fact repeating it is a copy too (gotcha 15).
QUOTED = re.compile(r'["\u201c]([^"\u201c\u201d]{20,})["\u201d]')

FACT_SEARCH_INSTRUCTIONS = (
    "You help a legal research tool collect public professional records about one person. Run a web "
    "search for the query given. Then, for each page about this person, write one line: the page URL, "
    "then in your own words what it shows about the person's {topic}. Skip pages about someone else "
    "with the same name and pages listing many people. "
    "Do not use LinkedIn, Google Scholar, Daubert Tracker, TrialSmith, CPAverify, BrokerCheck, ABMS "
    "or DocInfo. Professional information only: no home address, family, health or age."
)

FACT_INSTRUCTIONS = (
    "You read numbered web search results (W1, W2, ...) for a legal research tool, plus search notes "
    "that describe pages by URL. For each result that is about the named person (the same person, "
    "also in field and place when given; a page may call them by title and surname, such as "
    "\"Dr. Smith\"), list up to 3 short facts about that person's {topic} that the result's excerpt or "
    "the notes for that result's URL show. Write each fact in your own words in one short sentence; "
    "do not copy text. Use only the results and notes given; never add facts. Set about_person to "
    "false and give no facts for results about someone else with the same name or about many people."
)

FACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ref", "about_person", "facts"],
        "properties": {
            "ref": {"type": "string"},
            "about_person": {"type": "boolean"},
            "facts": {"type": "array", "items": {"type": "string"}},
        },
    }}},
}


def person_line(name: str, context: str | None) -> str:
    return f"Person: {name}" + (f" ({context})" if context else "")


def fact_input(name: str, topic: str, hits: list[dict], context: str | None = None, notes: str = "") -> str:
    lines = [person_line(name, context), f"Topic: {topic}", "Results:"]
    for i, hit in enumerate(hits, 1):
        lines.append(f"W{i}. {hit['title']} | {hit['url']} | excerpt: {hit['snippet'] or '(none)'}")
    if notes:
        lines += ["Search notes:", notes]
    return "\n".join(lines)


async def search_facts(name: str, queries: list[str], topic: str, context: str | None = None) -> list[dict]:
    """Run `queries` with web search in parallel, then one JSON call that pulls short facts about
    `name` for `topic` from the results. `context` (field, location) tells both calls which same-name
    person is meant. Returns [{url, title, facts}]; snippets and search notes never leave here.
    All queries failing raises the first error; a failed fact call raises."""
    # Meta web search results usually carry no snippet: the search model's own notes are what the fact
    # call reads (live check 2026-10-28), so the search call is asked to write them per page URL.
    who = f"\n{person_line(name, context)}. Report only on this person." if context else ""
    replies = await asyncio.gather(
        *(llm.call_model(FACT_SEARCH_INSTRUCTIONS.format(topic=topic), f"Search query: {q}{who}", web_search=True)
          for q in queries),
        return_exceptions=True,
    )
    errors = [r for r in replies if isinstance(r, BaseException)]
    if errors and len(errors) == len(replies):
        raise errors[0]
    hits, seen, notes = [], set(), []
    for reply in replies:
        if isinstance(reply, BaseException):
            continue
        if reply.text.strip():
            notes.append(reply.text.strip()[:NOTES_MAX])
        for hit in to_hits(reply.results or reply.citations, "facts", name):
            if hit["url"] not in seen:
                seen.add(hit["url"])
                hits.append(hit)
    if not hits:
        return []
    notes_text = "\n\n".join(notes)
    reply = await llm.call_model(FACT_INSTRUCTIONS.format(topic=topic),
                                 fact_input(name, topic, hits, context, notes_text), schema=FACT_SCHEMA)
    snippets = "\n".join([squash(h["snippet"]) for h in hits if h["snippet"]]
                         + [squash(q) for q in QUOTED.findall(notes_text)])
    facts: dict[int, list[str]] = {}
    for item in (reply.data or {}).get("results") or []:
        ref = str(item.get("ref") or "").strip().upper().removeprefix("W")
        if not ref.isdigit() or not 1 <= int(ref) <= len(hits) or not item.get("about_person"):
            continue
        hit = hits[int(ref) - 1]
        if not has_full_name(f"{hit['title']} {hit['url']} {hit['snippet']}", name):
            continue  # the page must name the person in full somewhere (gotcha 13); facts may say "Dr. X"
        kept = facts.setdefault(int(ref), [])
        for fact in item.get("facts") or []:
            fact = " ".join(str(fact).split())[:FACT_MAX]
            if fact and not copied(fact, snippets) and fact not in kept and len(kept) < FACTS_PER_RESULT:
                kept.append(fact)
    return [{"url": hits[i - 1]["url"], "title": hits[i - 1]["title"], "facts": f}
            for i, f in sorted(facts.items()) if f]
