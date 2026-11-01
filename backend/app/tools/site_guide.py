"""Tool 3: site guide (DESIGN 4B.9, memory 2). Learns how to search and download records on one
website and remembers it. Reads up to 3 pages with Firecrawl (after ctx.fetch_ok); the model describes
them in its own words and picks links only by number, so every URL in a manual comes from a page we read.
No logins (gotcha 4): a site asking for a login, a payment or a robot check is blocked (memory 4).
Page text stays in memory (gotcha 15); never a credential in login_notes (gotcha 45)."""

import re
from urllib.parse import urljoin, urlsplit

from app import memory
from app.evidence import refuse
from app.sources import firecrawl, host_of, on_domain
from app.tools import Ctx, tool

MAX_PAGES = 3  # the home page + up to 2 search or download pages
MAX_LINKS = 40  # links shown to the model
PAGE_MAX = 20_000  # characters of each page shown to the model
BAD_DOMAIN = "give a website domain, for example courts.example.gov"
DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
LINK = re.compile(r"\[([^\]]{0,200})\]\(\s*<?([^)\s>]+)")
# ponytail: keyword guess for which links to read, let the model pick if it misses search pages
USEFUL_LINK = re.compile(r"(?i)search|look-?up|find|case|docket|record|licen[cs]e|verif|download|opinion")
ROBOT_CHECK = re.compile(r"(?i)captcha|verify (?:that )?you are (?:a )?human|are you a robot|just a moment\.\.\.")
BLOCKERS = ("login", "paywall", "captcha")

INSTRUCTIONS = (
    "You read pages of one website for a legal research tool and write a short manual on how to find "
    "and download public records on it. Use your own words; never copy text. Give: what records the "
    "site holds; the link number (L1, L2, ...) of its search page and of its download page, or an "
    "empty string; how to search (which fields or URL query parameters to fill, e.g. last name, case "
    "number); the search fields; what a result looks like; how records or documents can be "
    "downloaded; whether the pages need JavaScript. Say whether searching needs a login and where; "
    "never write a username, password or any other credential. Set blocker to login, paywall or "
    "captcha if the records can only be reached by logging in, paying or passing a robot check, "
    "else none. Use only the pages given."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["holds", "search_link", "download_link", "how_to_search", "fields", "results",
                 "downloads", "needs_javascript", "login_needed", "login_where", "blocker"],
    "properties": {
        "holds": {"type": "string"},
        "search_link": {"type": "string"},
        "download_link": {"type": "string"},
        "how_to_search": {"type": "string"},
        "fields": {"type": "array", "items": {"type": "string"}},
        "results": {"type": "string"},
        "downloads": {"type": "string"},
        "needs_javascript": {"type": "boolean"},
        "login_needed": {"type": "boolean"},
        "login_where": {"type": "string"},
        "blocker": {"type": "string", "enum": ["none", *BLOCKERS]},
    },
}


def clean_domain(domain: str) -> str | None:
    """"https://www.Courts.example.gov/x" or "courts.example.gov" -> "courts.example.gov"; else None."""
    domain = domain.strip()
    if "://" in domain:
        domain = host_of(domain) or ""
    domain = memory.domain_of(domain.split("/")[0])
    return domain if DOMAIN.match(domain) else None


def links_of(page: dict, domain: str) -> list[tuple[str, str]]:
    """(text, absolute URL) of the page's links on `domain`, fragments removed."""
    out = []
    for text, href in LINK.findall(page["markdown"]):
        url = urljoin(page["url"], href).split("#")[0]
        if urlsplit(url).scheme in ("http", "https") and on_domain(host_of(url) or "", (domain,)):
            out.append((" ".join(text.split()), url))
    return out


def pick(ref: object, links: list[tuple[str, str]]) -> str | None:
    """The URL of link `ref` ("L3"), or None for an empty or unknown ref."""
    n = str(ref or "").strip().upper().removeprefix("L")
    return links[int(n) - 1][1] if n.isdigit() and 1 <= int(n) <= len(links) else None


def one_line(text: object) -> str:
    return " ".join(str(text or "").split())


def manual_text(answer: dict, links: list[tuple[str, str]]) -> str:
    """The manual, written by code from the model's items; URLs only from the links we read."""
    lines = [("What it holds", one_line(answer.get("holds"))),
             ("Search page", pick(answer.get("search_link"), links)),
             ("How to search", one_line(answer.get("how_to_search"))),
             ("Fields", "; ".join(one_line(f) for f in answer.get("fields") or [])),
             ("Results", one_line(answer.get("results"))),
             ("Download page", pick(answer.get("download_link"), links)),
             ("Downloads", one_line(answer.get("downloads"))),
             ("JavaScript needed", "yes" if answer.get("needs_javascript") else "no")]
    return "\n".join(f"{label}: {value}" for label, value in lines if value)[:memory.HOW_TO_MAX]


def reply(domain: str, manual: dict | None, from_memory: bool) -> dict:
    return {"domain": domain, "how_to": manual["how_to"] if manual else "",
            "login_needed": bool(manual and manual["login_notes"].startswith("login needed: yes")),
            "blocked": memory.blocked(domain), "from_memory": from_memory}


async def read_pages(ctx: Ctx, domain: str) -> tuple[list[dict], str | None]:
    """The home page and up to 2 useful links on the site, and the URL of a robot check, if any."""
    pages, queue, seen = [], [f"https://{domain}/"], set()
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen or ctx.fetch_ok(url):
            continue
        seen.add(url)
        try:
            page = await firecrawl.scrape(url)
        except Exception:  # only the home page must be readable
            if not pages:
                raise
            continue
        if ctx.fetch_ok(page["url"]):  # redirected to a site we may not read
            continue
        if ROBOT_CHECK.search(page["markdown"]):
            return pages, page["url"]
        pages.append(page)
        queue += [u for text, u in links_of(page, domain) if USEFUL_LINK.search(f"{text} {u}")]
    return pages, None


@tool(label="Site guide", cap=4)
async def site_guide(ctx: Ctx, domain: str) -> dict:
    """Learn how to search and download records on one website (for example a state court or licence
    board site from sites_for_state): its search page, search fields, what results look like, how to
    download documents, and whether a login is needed. Manuals are remembered, so asking again for the
    same site is free. `blocked` says the site must not be read (login, paywall or robot check)."""
    site = clean_domain(domain)
    if not site:
        return refuse(BAD_DOMAIN)
    if saved := memory.manual(site):
        ctx.log("manual from memory")
        return reply(site, saved, from_memory=True)
    if refusal := ctx.fetch_ok(f"https://{site}/"):
        return refuse(refusal)
    pages, robot_check = await read_pages(ctx, site)
    if robot_check:
        memory.block_site(site, "captcha", robot_check)
        ctx.add_gap(f"{site} asks for a robot check: not read")
        ctx.log("robot check: site blocked")
        return reply(site, None, from_memory=False)
    if not pages:
        return refuse(f"{site} could not be read")
    links = list(dict.fromkeys(link for page in pages for link in links_of(page, site)))[:MAX_LINKS]
    text = "\n\n".join(f"Page {page['url']}:\n{page['markdown'][:PAGE_MAX]}" for page in pages)
    text += "\n\nLinks:\n" + "\n".join(f"L{i}. {t} | {u}" for i, (t, u) in enumerate(links, 1))
    answer = await ctx.ask_model(INSTRUCTIONS, f"Website: {site}\n\n{text}", SCHEMA)
    answer = answer if isinstance(answer, dict) else {}
    login = "login needed: " + ("yes" if answer.get("login_needed") else "no")
    if answer.get("login_needed") and (where := one_line(answer.get("login_where"))):
        login += f", {where}"
    memory.save_manual(site, manual_text(answer, links), login, job_id=ctx.job_id)  # raises on a secret
    if (blocker := answer.get("blocker")) in BLOCKERS:
        memory.block_site(site, blocker, pages[0]["url"])
        ctx.add_gap(f"{site} needs a {blocker}: not read")
    ctx.log(f"{len(pages)} pages read, manual saved" + (f", blocked: {blocker}" if blocker in BLOCKERS else ""))
    return reply(site, memory.manual(site), from_memory=False)
