"""Tool 3: site guide (DESIGN 4B.9, memory 2). Learns how another agent can get records from one
website by fetching URLs, and remembers it. Reads up to 3 pages with Firecrawl (after ctx.fetch_ok).
The manual is JSON (in `how_to`): URL templates such as `.../caseinfo/{year}/{number}`. Code builds
each template from a real link we read: the model only names the link (L3) and which parts of it vary,
so no URL in a manual is made up. Searches that need a form go to `not_usable`.
No logins (gotcha 4): a site asking for a login, a payment or a robot check is blocked (memory 4).
Page text stays in memory (gotcha 15); never a credential in login_notes (gotcha 45)."""

import json
import re
from urllib.parse import urljoin, urlsplit

from app import memory
from app.evidence import refuse
from app.sources import firecrawl, host_of, on_domain
from app.tools import Ctx, tool

MAX_PAGES = 3  # the home page + up to 2 search or download pages
MAX_LINKS = 40  # links shown to the model, useful-looking ones first
PAGE_MAX = 20_000  # characters of each page shown to the model
MAX_URLS = 6  # URL templates in a manual
TEXT_MAX = 150  # characters of each short text in a manual
BAD_DOMAIN = "give a website domain, for example courts.example.gov"
DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
LINK = re.compile(r"\[([^\]]{0,200})\]\(\s*<?([^)\s>]+)")
PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,19}$")
# ponytail: keyword guess for which links to read, let the model pick if it misses search pages
USEFUL_LINK = re.compile(r"(?i)search|look-?up|find|case|docket|record|licen[cs]e|verif|download|opinion")
ROBOT_CHECK = re.compile(r"(?i)captcha|verify (?:that )?you are (?:a )?human|are you a robot|just a moment\.\.\.")
BLOCKERS = ("login", "paywall", "captcha")
KINDS = ("search", "record", "document")

INSTRUCTIONS = (
    "You read pages of one website for a legal research tool. Write a manual for another program "
    "that gets records from this site only by fetching URLs: it cannot click, type into forms or run "
    "scripts. Use your own words; never copy text. Give: what records the site holds. Then up to 6 "
    "URL patterns, each based on one numbered link (L1, L2, ...) from the list: kind (search = a page "
    "of results for a query, record = one case, person or licence, document = a file such as a PDF), "
    "what it gets, the link number, and the parts of that link that change from one record or query "
    "to another: the exact text of the part in the link (for example \"2026\"), a short lower-case "
    "name (for example \"year\") and what to put there. Prefer links whose URL holds a query, a case "
    "number, a name or an id. Then what a results page shows and links to, how pages are numbered if "
    "they are, and searches the site offers that only work through a form or a script, so they cannot "
    "be done by URL. Say whether the pages need JavaScript, and whether searching needs a login and "
    "where; never write a username, password or any other credential. Set blocker to login, paywall "
    "or captcha if the records can only be reached by logging in, paying or passing a robot check, "
    "else none. Use only the pages and links given."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["holds", "urls", "results", "paging", "not_usable", "needs_javascript", "login_needed",
                 "login_where", "blocker"],
    "properties": {
        "holds": {"type": "string"},
        "urls": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "what", "link", "parts"],
            "properties": {
                "kind": {"type": "string", "enum": list(KINDS)},
                "what": {"type": "string"},
                "link": {"type": "string"},
                "parts": {"type": "array", "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["value", "name", "meaning"],
                    "properties": {"value": {"type": "string"}, "name": {"type": "string"},
                                   "meaning": {"type": "string"}},
                }},
            },
        }},
        "results": {"type": "string"},
        "paging": {"type": "string"},
        "not_usable": {"type": "array", "items": {"type": "string"}},
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
    """(text, absolute URL) of the page's links on `domain`. Fragments are removed, except app routes
    such as `#/caseinfo/2026/1480`, which are the page itself on script-driven sites."""
    out = []
    for text, href in LINK.findall(page["markdown"]):
        url = urljoin(page["url"], href)
        base, _, fragment = url.partition("#")
        url = url if fragment.startswith(("/", "!/")) else base
        if urlsplit(url).scheme in ("http", "https") and on_domain(host_of(url) or "", (domain,)):
            out.append((" ".join(text.split()), url))
    return out


def is_useful(link: tuple[str, str]) -> bool:
    return bool(USEFUL_LINK.search(f"{link[0]} {link[1]}"))


def pick(ref: object, links: list[tuple[str, str]]) -> str | None:
    """The URL of link `ref` ("L3"), or None for an empty or unknown ref."""
    n = str(ref or "").strip().upper().removeprefix("L")
    return links[int(n) - 1][1] if n.isdigit() and 1 <= int(n) <= len(links) else None


def one_line(text: object) -> str:
    return " ".join(str(text or "").split())[:TEXT_MAX]


def template(url: str, parts: list) -> tuple[str, dict]:
    """`url` with each named part replaced by `{name}`, and {name: meaning} for the parts found.
    Only whole parts after the host are replaced ("2026" in /2026/ or 2026-Ohio, never in 20261)."""
    head = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    rest, params = url[len(head):], {}
    for part in parts:
        value, name = str(part.get("value") or ""), str(part.get("name") or "").strip().lower()
        if not value or not PARAM_NAME.match(name) or name in params:
            continue
        rest, found = re.subn(rf"(?<![A-Za-z0-9{{]){re.escape(value)}(?![A-Za-z0-9}}])", f"{{{name}}}", rest)
        if found:
            params[name] = one_line(part.get("meaning"))
    return head + rest, params


def manual_json(answer: dict, links: list[tuple[str, str]]) -> str:
    """The manual as JSON, built by code: every URL comes from a link we read (see template)."""
    urls, seen = [], set()
    for item in answer.get("urls") or []:
        example = pick(item.get("link"), links)
        if not example or item.get("kind") not in KINDS:
            continue
        url, params = template(example, item.get("parts") or [])
        entry = {"kind": item["kind"], "what": one_line(item.get("what")), "url": url, "params": params,
                 "example": example}
        # a URL that looks like a secret (token=..., 12+ digits) would make the whole save fail
        if url in seen or memory.looks_secret(json.dumps(entry)):
            continue
        seen.add(url)
        urls.append(entry)
    manual = {"holds": one_line(answer.get("holds")), "login": bool(answer.get("login_needed")),
              "javascript": bool(answer.get("needs_javascript")), "urls": urls[:MAX_URLS],
              "results": one_line(answer.get("results")), "paging": one_line(answer.get("paging")),
              "not_usable": [one_line(n) for n in answer.get("not_usable") or []][:5]}
    text = json.dumps(manual, ensure_ascii=False, separators=(",", ":"))
    while len(text) > memory.HOW_TO_MAX and (manual["urls"] or manual["not_usable"]):
        (manual["urls"] or manual["not_usable"]).pop()  # never cut JSON mid-way
        text = json.dumps(manual, ensure_ascii=False, separators=(",", ":"))
    return text


def as_manual(how_to: str) -> dict:
    try:
        return json.loads(how_to)
    except ValueError:  # a manual saved before the JSON format
        return {"text": how_to}


def reply(domain: str, saved: dict | None, from_memory: bool) -> dict:
    return {"domain": domain, "manual": as_manual(saved["how_to"]) if saved else None,
            "login_needed": bool(saved and saved["login_notes"].startswith("login needed: yes")),
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
        queue += [link[1] for link in links_of(page, domain) if is_useful(link)]
    return pages, None


@tool(label="Site guide", cap=4)
async def site_guide(ctx: Ctx, domain: str) -> dict:
    """Learn how to get records from one website (for example a state court or licence board site
    from sites_for_state) by fetching URLs. Returns a manual: URL templates for searches, single
    records and documents, e.g. "https://site/caseinfo/{year}/{number}" with what each {part} means
    and a real example; what results pages show; searches that need a form (not usable by URL); and
    whether a login or JavaScript is needed. Fill a template and read it with your page tools.
    Manuals are remembered, so asking again for the same site is free. `blocked` says the site must
    not be read (login, paywall or robot check)."""
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
    links = list(dict.fromkeys(link for page in pages for link in links_of(page, site)))
    links = sorted(links, key=lambda link: not is_useful(link))[:MAX_LINKS]  # stable: page order kept
    text = "\n\n".join(f"Page {page['url']}:\n{page['markdown'][:PAGE_MAX]}" for page in pages)
    text += "\n\nLinks:\n" + "\n".join(f"L{i}. {t} | {u}" for i, (t, u) in enumerate(links, 1))
    answer = await ctx.ask_model(INSTRUCTIONS, f"Website: {site}\n\n{text}", SCHEMA)
    answer = answer if isinstance(answer, dict) else {}
    login = "login needed: " + ("yes" if answer.get("login_needed") else "no")
    if answer.get("login_needed") and (where := one_line(answer.get("login_where"))):
        login += f", {where}"
    memory.save_manual(site, manual_json(answer, links), login, job_id=ctx.job_id)  # raises on a secret
    if (blocker := answer.get("blocker")) in BLOCKERS:
        memory.block_site(site, blocker, pages[0]["url"])
        ctx.add_gap(f"{site} needs a {blocker}: not read")
    ctx.log(f"{len(pages)} pages read, manual saved" + (f", blocked: {blocker}" if blocker in BLOCKERS else ""))
    return reply(site, memory.manual(site), from_memory=False)
