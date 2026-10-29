"""Tool 12: web search (Meta web search + fact step; DESIGN 4B.9). Gotchas 14, 15, 22: snippets and
search notes stay inside websearch.search_facts; only URL, title and our facts are saved."""

import json
from typing import Literal

from app.evidence import refuse
from app.sources import has_full_name, websearch
from app.tools import RESULT_MAX, Ctx, tool

# topic -> (what the fact step looks for, finding kind, note)
TOPICS = {
    "testimony": ("expert testimony", "web_testimony", "verify"),
    "credentials": ("licences, board certification, discipline", "web_credential",
                    "verify manually at the official board site"),
    "other": ("professional background", "web_fact", "verify"),
}
REPLY_BUDGET = RESULT_MAX - 500  # room for the rest of the reply
# Fixed refusals (never source or model text)
NEED_FULL_NAME = "the query must contain the expert's full name"
BLOCKED_SITE_QUERY = "the query must not name a site that must not be searched"
QUERY_TOO_LONG = f"the query must be at most {websearch.QUERY_MAX} characters"


@tool(label="Web search", cap=6)
async def web_search(ctx: Ctx, query: str, topic: Literal["testimony", "credentials", "other"]) -> dict:
    """Search the web once for pages about the expert and get short facts from each page, in our
    own words. `query` must contain the expert's full name (at most 200 characters) and must not
    name LinkedIn, Google Scholar or other sites we may not use. `topic` picks what the facts are
    about: "testimony" (expert witness work, cases), "credentials" (licences, board certification,
    discipline) or "other". Web results are unconfirmed leads: verify before relying on them."""
    full_name = ctx.expert.display_name
    if len(query) > websearch.QUERY_MAX:
        return refuse(QUERY_TOO_LONG)
    if not has_full_name(query, full_name):
        return refuse(NEED_FULL_NAME)
    if websearch.names_blocked_site(query):  # gotcha 2: never ask the search for those sites
        return refuse(BLOCKED_SITE_QUERY)
    search_topic, kind, note = TOPICS[topic]
    context = ", ".join(x for x in (ctx.expert.field, ctx.expert.location) if x) or None
    results = await websearch.search_facts(full_name, [query], search_topic, context=context)
    pages, not_shown = [], 0
    for r in results:
        facts = [str(f) for f in r.get("facts") or []]
        # add_finding drops gotcha 2 and non-http URLs (None); only URL, title and our facts are kept.
        ref = ctx.add_finding(kind, r.get("title") or r["url"], r["url"], facts={"facts": facts},
                              confidence="low", note=note, source_type="web")
        if not ref:
            continue
        page = {"ref": ref, "title": (r.get("title") or r["url"])[:200], "url": r["url"], "facts": facts}
        if len(json.dumps(pages + [page])) > REPLY_BUDGET:
            not_shown += 1  # saved, but the reply must stay under RESULT_MAX
            continue
        pages.append(page)
    if not results:
        ctx.add_gap("no web results")
    ctx.log(f"{len(results)} found, {len(pages) + not_shown} saved")
    return {"results": pages, "not_shown": not_shown}
