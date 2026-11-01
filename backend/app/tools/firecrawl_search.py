"""Tool 6: Firecrawl search (DESIGN 4B.9). One search engine query over the open web or up to
MAX_SITES chosen sites; result pages are never fetched. Gotchas 1-3, 10, 15, 24, 25: snippets are
checked for the expert's full name in memory only; a finding holds the hit's URL, title and code facts.

More than MAX_SITES sites -> refused (fixed text), so the model asks again with fewer sites.

A site with a saved manual (tool 3, site_guide) is searched through its own search page instead: one
model call picks a `search` URL template and its values, code fills it (values URL-encoded) and reads
the results page with read_web_page (tool 7). Links on a results line naming the expert in full become
hits. Any problem on that path -> the site falls back to the search engine."""

import json
import re
from urllib.parse import quote, urljoin

from app import memory
from app.evidence import refuse
from app.memory import domain_of
from app.sources import firecrawl_search as source
from app.sources import has_full_name, host_of, is_listing, on_domain, websearch
from app.tools import RESULT_MAX, Ctx, tool

MAX_SITES = 5
MANUAL_LINKS_MAX = 20  # hits taken from one results page
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
LINK = re.compile(r"\[([^\]]{0,1500})\]\(\s*<?([^)\s>]+)")  # markdown link; its text may run over lines
PICK_INSTRUCTIONS = (
    "A research tool searches one website through its own search URLs. Given the search query, the "
    "expert's name and the site's search URLs (T1, T2, ...) with what each {part} means, pick the one "
    "search URL that fits the query best and give a value for every part of it: for example the "
    "expert's last name for a party-name part, or 1 for a page number. Use only words from the query "
    "or the expert's name, or a page number. Set template to 0 if none fits."
)
PICK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["template", "values"],
    "properties": {
        "template": {"type": "integer"},
        "values": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "value"],
            "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
        }},
    },
}
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


def search_templates(host: str) -> list[dict]:
    """The `search` URL templates of the site's saved manual (tool 3), or [] if it has none."""
    saved = memory.manual(host)
    try:
        urls = json.loads(saved["how_to"]).get("urls") if saved else None
    except (ValueError, AttributeError):  # an old text manual
        return []
    # a search URL with no {part} is a fixed listing: it cannot search for the query
    return [u for u in urls or [] if isinstance(u, dict) and u.get("kind") == "search"
            and PLACEHOLDER.search(u.get("url") or "")]


def fill(template: str, values: dict[str, str]) -> str | None:
    """`template` with every {part} replaced by its URL-encoded value, or None if one is missing."""
    names = PLACEHOLDER.findall(template)
    if any(not values.get(n, "").strip() for n in names):
        return None
    return PLACEHOLDER.sub(lambda m: quote(values[m.group(1)].strip(), safe=""), template)


async def manual_search(ctx: Ctx, host: str, templates: list[dict], query: str) -> list[dict] | None:
    """Search `host` through its manual: results as [{url, title, description, via}] (description =
    the results line, in memory only), or None to fall back to the search engine."""
    lines = [f"T{i}. {t.get('what', '')} | parts: "
             + "; ".join(f"{name} = {meaning}" for name, meaning in (t.get("params") or {}).items())
             for i, t in enumerate(templates, 1)]
    answer = await ctx.ask_model(
        PICK_INSTRUCTIONS,
        f"Query: {query}\nExpert: {ctx.expert.display_name}\nSearch URLs:\n" + "\n".join(lines), PICK_SCHEMA)
    n = answer.get("template") if isinstance(answer, dict) else 0
    if not isinstance(n, int) or not 1 <= n <= len(templates):
        return None
    values = {str(v.get("name", "")): str(v.get("value", "")) for v in answer.get("values") or []
              if isinstance(v, dict)}
    url = fill(templates[n - 1]["url"], values)
    if not url or not on_domain(host_of(url) or "", (host,)):
        return None
    page = await ctx.call_tool("read_web_page", url=url)  # tool 7: its checks, blocks and activity row
    if "error" in page:
        return None
    return named_links(page.get("text", ""), page["url"], ctx.expert.display_name)[:MANUAL_LINKS_MAX]


def named_links(text: str, page_url: str, full_name: str) -> list[dict]:
    """Results-page links that name the expert in full: in the link's own text (which may run over
    several lines) or next to it on its row, up to the neighbouring links (a table row). The context
    is kept in memory only, as `description`."""
    matches, out = list(LINK.finditer(text)), []
    for i, m in enumerate(matches):
        row_start = max(text.rfind("\n", 0, m.start()) + 1, matches[i - 1].end() if i else 0)
        row_end = text.find("\n", m.end())
        row_end = min(len(text) if row_end < 0 else row_end, matches[i + 1].start() if i + 1 < len(matches) else len(text))
        context = f"{text[row_start:m.start()]} {m.group(1)} {text[m.end():row_end]}"
        url = urljoin(page_url, m.group(2))
        if url.startswith(("http://", "https://")) and has_full_name(context, full_name):
            # the heading only: the rest of a multi-line link is the page's summary text (gotcha 15)
            title = " ".join(re.sub(r"[*\\]+", " ", m.group(1).split("\n")[0]).split())[:200]
            out.append({"url": url, "title": title, "description": context, "via": "manual"})
    return out


@tool(label="Search the web or chosen sites", cap=8)
async def firecrawl_search(ctx: Ctx, query: str, sites: list[str] = []) -> dict:  # noqa: B006 - never mutated
    """Search the web with a search engine and get result links (pages are not opened). Use it for
    the expert, a case number, a state or another clue. `query`: at most 200 characters, must not
    name LinkedIn, Google Scholar or other sites we may not use. `sites`: optional, up to 5 site
    names (e.g. "courts.state.example.gov") to search only those sites; leave empty to search the
    whole web. A site with a manual from site_guide is searched through its own search page instead
    (`searched` in the reply says which way each site was searched), so call site_guide first for a
    site you will search often. Only hits whose title or snippet (or results line) names the expert in
    full are kept and saved; they are unconfirmed leads, so read the page before relying on them."""
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

    results, searched = [], {}
    for host in hosts:  # sites with a manual: their own search page first
        if templates := search_templates(host):
            found = await manual_search(ctx, host, templates, query)
            searched[host] = "search engine" if found is None else "manual"
            results += found or []
    engine_hosts = [h for h in hosts if searched.get(h) != "manual"]
    if engine_hosts or not sites:
        results += await source.search(query, engine_hosts)
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
        facts = {"query": query, "site": site, "matched": matched} | ({"via": "manual"} if r.get("via") else {})
        ref = ctx.add_finding("site_hit", title, url, facts=facts,
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
    by_manual = sum(how == "manual" for how in searched.values())
    ctx.log(f"{len(results)} found, {saved} saved, {len(skipped)} sites skipped"
            + (f", {by_manual} sites by manual" if by_manual else ""))
    reply = {"hits": hits, "not_shown": not_shown}
    if skipped:
        reply["skipped_sites"] = skipped
    if by_manual:  # only when a manual was used: engine-only replies stay as they were
        reply["searched"] = {h: searched.get(h, "search engine") for h in hosts}
    return reply
