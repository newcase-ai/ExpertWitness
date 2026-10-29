"""Tool 6: Firecrawl search (DESIGN 4B.9). One search engine query over the open web or up to
MAX_SITES chosen sites; result pages are never fetched. Gotchas 1-3, 10, 15, 24, 25: snippets are
checked for the expert's full name in memory only; a finding holds the hit's URL, title and code facts.

More than MAX_SITES sites -> refused (fixed text), so the model asks again with fewer sites."""

import json
import re

from app.evidence import refuse
from app.memory import domain_of
from app.sources import firecrawl_search as source
from app.sources import has_full_name, host_of, is_listing, on_domain, websearch
from app.tools import RESULT_MAX, Ctx, tool

MAX_SITES = 5
QUERY_MAX = websearch.QUERY_MAX
REPLY_BUDGET = RESULT_MAX - 1500  # room for skipped sites and the rest of the reply
# Fixed refusals (never source or model text)
EMPTY_QUERY = "the query must not be empty"
QUERY_TOO_LONG = f"the query must be at most {QUERY_MAX} characters"
BLOCKED_SITE_QUERY = "the query must not name a site that must not be searched"
TOO_MANY_SITES = f"at most {MAX_SITES} sites per search: split them over several searches"
NOT_A_SITE = "not a site name: give one host name such as courts.example.gov"
ALL_SITES_REFUSED = "none of the sites may be searched"
NO_HITS = "no search hits naming the expert"


# A DNS host name with a dot: letters, digits, hyphens; the last label (TLD) starts with a letter, so
# IPv4 in any form (dotted, hex "1.0x1", octal) fails. Spaces, operators ("a.com OR b.com"), IPv6 and
# other characters never match, so they never reach the search query.
HOSTNAME = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?")


def site_host(site: str) -> str | None:
    """`site` ("example.gov", "https://www.example.gov:443/path") as a host without `www.`, or None
    if it is not one valid host name."""
    site = site.strip()
    if not site or any(c.isspace() for c in site):
        return None
    host = host_of(site if "://" in site else "https://" + site)
    host = domain_of(host) if host else None
    return host if host and HOSTNAME.fullmatch(host) else None


@tool(label="Search the web or chosen sites", cap=8)
async def firecrawl_search(ctx: Ctx, query: str, sites: list[str] = []) -> dict:  # noqa: B006 - never mutated
    """Search the web with a search engine and get result links (pages are not opened). Use it for
    the expert, a case number, a state or another clue. `query`: at most 200 characters, must not
    name LinkedIn, Google Scholar or other sites we may not use. `sites`: optional, up to 5 site
    names (e.g. "courts.state.example.gov") to search only those sites; leave empty to search the
    whole web. Only hits whose title or snippet names the expert in full are kept and saved; they
    are unconfirmed leads, so read the page before relying on them."""
    query = query.strip()
    if not query:
        return refuse(EMPTY_QUERY)
    if len(query) > QUERY_MAX:
        return refuse(QUERY_TOO_LONG)
    if websearch.names_blocked_site(query):  # gotcha 2: never ask the search for those sites
        return refuse(BLOCKED_SITE_QUERY)
    unique = {}  # duplicates (also "www." or URL forms of one host) count once
    for site in sites:
        unique.setdefault(site_host(site) or site.strip().lower(), site)
    if len(unique) > MAX_SITES:
        return refuse(TOO_MANY_SITES)
    hosts, skipped = [], []
    for site in unique.values():
        host = site_host(site)
        reason = ctx.fetch_ok("https://" + host) if host else NOT_A_SITE
        if reason:
            skipped.append({"site": site[:100], "reason": reason})
        else:
            hosts.append(host)
    if sites and not hosts:
        return refuse(ALL_SITES_REFUSED)

    results = await source.search(query, hosts)
    full_name = ctx.expert.display_name
    hits, seen, not_shown = [], set(), 0
    for r in results:
        url, title = r["url"], r["title"] or r["url"]
        host = host_of(url) or ""
        site = next((h for h in hosts if on_domain(host, (h,))), None)
        if url in seen or ctx.fetch_ok(url) or (hosts and not site) or is_listing(url, title, full_name):
            continue  # banned / API / blocked site, off the asked sites, or a listing page
        seen.add(url)
        # the snippet is read here only, never stored or returned (gotcha 15)
        matched = ("title" if has_full_name(r["title"], full_name)
                   else "snippet" if has_full_name(r["description"], full_name) else None)
        if not matched:
            continue
        ref = ctx.add_finding("site_hit", title, url, facts={"query": query, "site": site, "matched": matched},
                              confidence="low", note="search hit: read the page to verify", source_type="web")
        if not ref:
            continue
        hit = {"ref": ref, "title": title[:200], "url": url}
        if len(json.dumps(hits + [hit])) > REPLY_BUDGET:
            not_shown += 1  # saved, but the reply must stay under RESULT_MAX
            continue
        hits.append(hit)
    saved = len(hits) + not_shown
    if not saved:
        ctx.add_gap(NO_HITS)
    ctx.log(f"{len(results)} found, {saved} saved, {len(skipped)} sites skipped")
    reply = {"hits": hits, "not_shown": not_shown}
    if skipped:
        reply["skipped_sites"] = skipped
    return reply
