"""Tool 3: site guide (DESIGN 4B.9, memory 2). Learns how another agent can get records from one
website by fetching URLs, and remembers it. Reads up to 3 pages with Firecrawl (after ctx.fetch_ok),
plus their HTML (tool 7's checked fetch) to find search forms that work by URL (method GET).
The manual is JSON (in `how_to`): URL templates such as `.../caseinfo/{year}/{number}` or
`search.example.gov/search?query={query}`. Code builds every template from a real link or form we read:
the model only names the link (L3) or form (F1) and explains its parts, so no URL is made up.
Searches that need a POST form or a script go to `not_usable`.
Every call updates the manual: it reads pages not read before (`pages_read`) and merges what it finds
with the saved manual (new templates added, ones seen again refreshed, nothing lost).
Given a `state`, an official site that was read is added to that state's sites (tool 2's memory), so
sites the agent finds on its own join the list for later runs.
No logins (gotcha 4): a site asking for a login, a payment or a robot check is blocked (memory 4).
Page text stays in memory (gotcha 15); never a credential in login_notes (gotcha 45)."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from app import memory
from app.evidence import refuse
from app.sources import firecrawl, host_of, on_domain
from app.tools import Ctx, tool, web_reader  # web_reader (tool 7): robot check, checked HTML fetch
from app.tools.state_sites import LEARNED, is_official, state_code

MAX_PAGES = 3  # the home page + up to 2 search or download pages
MAX_LINKS = 40  # links shown to the model, useful-looking ones first
MAX_FORMS = 6  # search forms shown to the model
PAGE_MAX = 20_000  # characters of each page shown to the model
MAX_URLS = 8  # URL templates in a manual
MAX_PAGES_KEPT = 9  # pages_read remembered, so the next run reads other pages
KEEP_PAGES = 3  # pages_read kept even when templates must be dropped to fit
TEXT_MAX = 150  # characters of each short text in a manual
BAD_DOMAIN = "give a website domain, for example courts.example.gov"
DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
LINK = re.compile(r"\[([^\]]{0,200})\]\(\s*<?([^)\s>]+)")
PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,19}$")
PLACEHOLDER = re.compile(r"\{[a-z][a-z0-9_]*\}")
# ponytail: keyword guess for which links to read, let the model pick if it misses search pages
USEFUL_LINK = re.compile(r"(?i)search|look-?up|find|case|docket|record|licen[cs]e|verif|download|opinion")
FILL_IN = frozenset({"text", "search", "number", "date", "email", "tel", "select", "textarea"})  # a {part}
BLOCKERS = ("login", "paywall", "captcha")
KINDS = ("search", "record", "document")

INSTRUCTIONS = (
    "You read pages of one website for a legal research tool. Write a manual for another program "
    "that gets records from this site only by fetching URLs: it cannot click, type into forms or run "
    "scripts. Use your own words; never copy text. Give: what records the site holds. Then the "
    "search forms that are useful for finding court records, cases, people or licences: from the "
    "numbered search forms (F1, F2, ...), which already work by URL, give the form number, what it "
    "finds and what to put in each of its fields; skip site-wide news or newsletter forms unless they "
    "are the site's only search. Then up to 6 URL patterns, each based on one numbered link (L1, L2, "
    "...): kind (search = a page of results for a query, record = one case, person or licence, "
    "document = a file such as a PDF), what it gets, the link number, and every part of that link "
    "that changes from one record or query to another: the exact text of the part in the link (for "
    "example \"2026\"), a short lower-case name (for example \"year\") and what to put there. Name "
    "every changing part, for example both the year and the number in 2026-Ohio-0000. Prefer links "
    "whose URL holds a query, a case number, a name or an id; skip links to one fixed page. Then what "
    "a results page shows and links to, how pages are numbered if they are, and searches the site "
    "offers that cannot be done by URL (not in the forms list). Never give a URL pattern for a page "
    "that needs a login. Say whether the pages need JavaScript, and whether any part of the site "
    "needs a login and where; never write a username, password or any other credential. Set blocker "
    "to login, paywall or captcha only if the whole site, its public pages too, can be reached only "
    "by logging in, paying or passing a robot check; if only some parts need it (for example a "
    "filing system), say so and set blocker to none. Use only the pages, links and forms given."
)
FIELDS = {"type": "array", "items": {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "meaning"],
    "properties": {"name": {"type": "string"}, "meaning": {"type": "string"}},
}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["holds", "forms", "urls", "results", "paging", "not_usable", "needs_javascript",
                 "login_needed", "login_where", "blocker"],
    "properties": {
        "holds": {"type": "string"},
        "forms": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["form", "what", "fields"],
            "properties": {"form": {"type": "string"}, "what": {"type": "string"}, "fields": FIELDS},
        }},
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
    such as `#/caseinfo/2026/0000`, which are the page itself on script-driven sites."""
    out = []
    for text, href in LINK.findall(page["markdown"]):
        url = urljoin(page["url"], href)
        base, _, fragment = url.partition("#")
        url = url if fragment.startswith(("/", "!/")) else base
        if urlsplit(url).scheme in ("http", "https") and on_domain(host_of(url) or "", (domain,)):
            out.append((" ".join(text.split()), url))
    return out


class FormParser(HTMLParser):
    """Every <form> with its method, action and named fields: (name, type, value)."""

    def __init__(self):
        super().__init__()
        self.forms, self.open = [], False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self.forms.append({"action": a.get("action", "").strip(), "method": (a.get("method") or "get").lower(),
                               "fields": []})
            self.open = True
        elif tag in ("input", "select", "textarea") and self.open and a.get("name"):
            kind = (a.get("type") or "text").lower() if tag == "input" else tag
            self.forms[-1]["fields"].append((a["name"], kind, a.get("value", "")))

    def handle_endtag(self, tag):
        if tag == "form":
            self.open = False


def param_name(field: str) -> str | None:
    """A form field name as a {part} name: "search_api_fulltext" stays, "q[]" -> "q"."""
    name = re.sub(r"[^a-z0-9_]+", "_", field.lower()).strip("_")[:20]
    return name if PARAM_NAME.match(name) else None


def form_templates(html: str, page_url: str, domain: str) -> list[dict]:
    """URL templates of the page's GET forms on `domain`: hidden fields keep their value, fill-in fields
    become {parts}. POST forms, login forms (a password field) and forms with nothing to fill are skipped."""
    parser = FormParser()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - a broken page has no usable forms
        return []
    out = []
    for form in parser.forms:
        action = urljoin(page_url, form["action"] or page_url).partition("#")[0]
        kinds = {kind for _, kind, _ in form["fields"]}
        if form["method"] != "get" or "password" in kinds or urlsplit(action).scheme not in ("http", "https"):
            continue
        if not on_domain(host_of(action) or "", (domain,)):  # ponytail: shared search hosts (search.usa.gov) skipped
            continue
        query, params = [], []
        for field, kind, value in form["fields"]:
            if kind == "hidden":
                query.append(f"{quote(field)}={quote(value)}")
            elif kind in FILL_IN and (name := param_name(field)) and name not in params:
                query.append(f"{quote(field)}={{{name}}}")
                params.append(name)
        if params:
            sep = "&" if "?" in action else "?"
            out.append({"url": action + sep + "&".join(query), "params": params, "found_on": page_url})
    return out


def is_useful(link: tuple[str, str]) -> bool:
    return bool(USEFUL_LINK.search(f"{link[0]} {link[1]}"))


def pick(ref: object, items: list, prefix: str = "L"):
    """Item `ref` ("L3", "F1") of `items`, or None for an empty or unknown ref."""
    n = str(ref or "").strip().upper().removeprefix(prefix)
    return items[int(n) - 1] if n.isdigit() and 1 <= int(n) <= len(items) else None


def short_url(url: str) -> str:
    """A page as kept in pages_read: no scheme, no www. (the manual has 2,000 characters in all)."""
    return re.sub(r"^https?://(www\.)?", "", url)


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


def form_entries(answer: dict, forms: list[dict]) -> list[dict]:
    """Search entries for the forms the model described; URL and parts are the form's own."""
    out = []
    for item in answer.get("forms") or []:
        form = pick(item.get("form"), forms, "F")
        if not form:
            continue
        meanings = {param_name(str(f.get("name") or "")): one_line(f.get("meaning"))
                    for f in item.get("fields") or [] if isinstance(f, dict)}
        out.append({"kind": "search", "what": one_line(item.get("what")), "url": form["url"],
                    "params": {p: meanings.get(p, "") for p in form["params"]}})
    return out


def link_entries(answer: dict, links: list[tuple[str, str]]) -> list[dict]:
    """Entries for the links the model described, templated by code (see template)."""
    out = []
    for item in answer.get("urls") or []:
        link = pick(item.get("link"), links)
        if not link or item.get("kind") not in KINDS:
            continue
        url, params = template(link[1], item.get("parts") or [])
        if item["kind"] != "search" and not params:  # one fixed page, not a pattern
            continue
        out.append({"kind": item["kind"], "what": one_line(item.get("what")), "url": url, "params": params,
                    "example": link[1]})
    return out


def rank(entry: dict, new: set[str]) -> tuple[int, bool]:
    """Searches with parts first, then records and documents with parts, then fixed pages; new first."""
    usable = 0 if entry["kind"] == "search" and entry["params"] else 1 if entry["params"] else 2
    return usable, entry["url"] not in new


def manual_json(answer: dict, links: list[tuple[str, str]], forms: list[dict] = (), old: dict | None = None,
                pages: list[str] = ()) -> str:
    """The manual as JSON, built by code: every URL comes from a form or link we read. Merged with the
    `old` manual: templates seen again are refreshed, new ones added, text fields kept when the new
    answer has none. The most useful templates are kept when the manual is full."""
    old = old or {}
    # a URL that looks like a secret (token=..., 12+ digits) would make the whole save fail
    found = [e for e in form_entries(answer, list(forms)) + link_entries(answer, links)
             if not memory.looks_secret(json.dumps(e))]
    new = {e["url"] for e in found}
    by_url = {e["url"]: e for e in old.get("urls") or [] if isinstance(e, dict) and e.get("url")}
    fresh = {}
    for e in found:
        fresh.setdefault(e["url"], e)  # the first answer for a URL wins
    by_url.update(fresh)  # seen again: refreshed in place
    urls = sorted(by_url.values(), key=lambda e: rank(e, new))[:MAX_URLS]
    manual = {"holds": one_line(answer.get("holds")) or old.get("holds", ""),
              "login": bool(answer.get("login_needed")), "javascript": bool(answer.get("needs_javascript")),
              "urls": urls,
              "results": one_line(answer.get("results")) or old.get("results", ""),
              "paging": one_line(answer.get("paging")) or old.get("paging", ""),
              "not_usable": list(dict.fromkeys([one_line(n) for n in answer.get("not_usable") or []]
                                               + list(old.get("not_usable") or [])))[:5],
              "pages_read": [u for u in dict.fromkeys([*map(short_url, pages), *(old.get("pages_read") or [])])
                             if not memory.looks_secret(u)][:MAX_PAGES_KEPT]}
    text = json.dumps(manual, ensure_ascii=False, separators=(",", ":"))
    # too long: notes go first, then older pages read (the newest 3 guide the next run), then the least
    # useful templates, then the last pages read; never cut JSON mid-way
    while len(text) > memory.HOW_TO_MAX and (manual["not_usable"] or manual["pages_read"] or manual["urls"]):
        if manual["not_usable"]:
            manual["not_usable"].pop()
        elif len(manual["pages_read"]) > KEEP_PAGES or not manual["urls"]:
            manual["pages_read"].pop()
        else:
            manual["urls"].pop()
        text = json.dumps(manual, ensure_ascii=False, separators=(",", ":"))
    return text


def as_manual(how_to: str) -> dict:
    try:
        return json.loads(how_to)
    except ValueError:  # a manual saved before the JSON format
        return {"text": how_to}


def reply(domain: str, saved: dict | None, from_memory: bool, added: int = 0) -> dict:
    return {"domain": domain, "manual": as_manual(saved["how_to"]) if saved else None,
            "login_needed": bool(saved and saved["login_notes"].startswith("login needed: yes")),
            "blocked": memory.blocked(domain), "from_memory": from_memory, "added": added}


async def page_html(ctx: Ctx, url: str) -> str | None:
    """The page's HTML through tool 7's fetch (URL, redirect and private-address checks), or None."""
    try:
        got = await web_reader.fetch(ctx, url)
    except Exception:  # noqa: BLE001 - no HTML just means no forms
        return None
    if isinstance(got, dict) or got[1] not in ("text/html", "application/xhtml+xml"):
        return None
    return got[2].decode("utf-8", errors="replace")


async def read_pages(ctx: Ctx, domain: str, read_before: set[str] = frozenset()) -> tuple[list[dict], str | None, str]:
    """The home page and up to 2 useful links on the site (links not read in earlier runs first), the
    URL of a robot check, if any, and the site's domain: the one the home page redirected to when that
    is another site (apps.azcourts.gov -> azcourts.gov), so its links are followed, not dropped."""
    pages, queue, seen = [], [f"https://{domain}/"], set()
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in seen or ctx.fetch_ok(url):
            continue
        seen.add(url)
        try:
            page = await firecrawl.scrape(url)
        except Exception:  # only the home page must be readable
            if not pages and url == f"https://{domain}/":  # domains are stored without www.: try it
                queue.insert(0, f"https://www.{domain}/")
            elif not pages:
                raise
            continue
        if ctx.fetch_ok(page["url"]):  # redirected to a site we may not read
            continue
        landed = memory.domain_of(host_of(page["url"]) or "")
        if not pages and landed and not on_domain(landed, (domain,)):
            domain = landed
        if web_reader.robot_check(page["markdown"]):
            return pages, page["url"], domain
        pages.append(page)
        useful = [link[1] for link in links_of(page, domain) if is_useful(link)]
        queue = sorted(queue + useful, key=lambda u: short_url(u) in read_before)  # stable: unread first
    return pages, None, domain


async def forms_of(ctx: Ctx, pages: list[dict], domain: str) -> list[dict]:
    """GET search forms on the pages read, one entry per distinct URL template."""
    forms = {}
    for page in pages:
        if html := await page_html(ctx, page["url"]):
            for form in form_templates(html, page["url"], domain):
                forms.setdefault(form["url"], form)
    return list(forms.values())[:MAX_FORMS]


@tool(label="Site guide", cap=4)
async def site_guide(ctx: Ctx, domain: str, state: str = "") -> dict:
    """Learn how to get records from one website (for example a state court or licence board site
    from sites_for_state) by fetching URLs. Returns a manual: URL templates for searches, single
    records and documents, e.g. "https://site/search?query={query}" or ".../caseinfo/{year}/{number}"
    with what each {part} means; what results pages show; searches that need a form or script (not
    usable by URL); and whether a login or JavaScript is needed. firecrawl_search uses a site's search
    templates by itself. Every call reads more of the site and adds to its saved manual (`added` =
    new templates), so call it again for a site you need more from. `blocked` says the site must not
    be read (login, paywall or robot check); its saved manual, if any, is still returned. When the
    site redirects to another one, the reply's `domain` is that site: search it by that name. `state`:
    the US state whose records the site holds (two-letter code or name), if any; an official site is
    then added to that state's sites for later runs."""
    site = clean_domain(domain)
    if not site:
        return refuse(BAD_DOMAIN)
    saved = memory.manual(site)
    old = as_manual(saved["how_to"]) if saved else {}
    old = {} if "text" in old else old  # a manual saved before the JSON format is rebuilt
    if refusal := ctx.fetch_ok(f"https://{site}/"):
        if saved:
            ctx.log(f"not read ({refusal}): manual from memory")
            return reply(site, saved, from_memory=True)
        return refuse(refusal)
    try:
        pages, challenge, moved = await read_pages(ctx, site, set(old.get("pages_read") or []))
    except Exception:
        if not saved:
            raise
        ctx.log("site not readable now: manual from memory")
        return reply(site, saved, from_memory=True)
    if moved != site:  # the site lives elsewhere now: learn and save that site instead
        ctx.log(f"{site} redirects to {moved}")
        site, saved = moved, memory.manual(moved)
        old = as_manual(saved["how_to"]) if saved else {}
        old = {} if "text" in old else old
    if challenge:
        memory.block_site(site, "captcha", challenge)
        ctx.add_gap(f"{site} asks for a robot check: not read")
        ctx.log("robot check: site blocked")
        return reply(site, saved, from_memory=bool(saved))
    if not pages:
        if saved:
            return reply(site, saved, from_memory=True)
        return refuse(f"{site} could not be read")
    forms = await forms_of(ctx, pages, site)
    links = list(dict.fromkeys(link for page in pages for link in links_of(page, site)))
    links = sorted(links, key=lambda link: not is_useful(link))[:MAX_LINKS]  # stable: page order kept
    text = "\n\n".join(f"Page {page['url']}:\n{page['markdown'][:PAGE_MAX]}" for page in pages)
    text += "\n\nSearch forms (they work by URL):\n" + ("\n".join(
        f"F{i}. on {f['found_on']} | {f['url']} | fields: {', '.join(f['params'])}" for i, f in enumerate(forms, 1))
        or "(none)")
    text += "\n\nLinks:\n" + "\n".join(f"L{i}. {t} | {u}" for i, (t, u) in enumerate(links, 1))
    answer = await ctx.ask_model(INSTRUCTIONS, f"Website: {site}\n\n{text}", SCHEMA)
    answer = answer if isinstance(answer, dict) else {}
    login = "login needed: " + ("yes" if answer.get("login_needed") else "no")
    if answer.get("login_needed") and (where := one_line(answer.get("login_where"))):
        login += f", {where}"
    before = {e.get("url") for e in old.get("urls") or [] if isinstance(e, dict)}
    text = manual_json(answer, links, forms, old, [p["url"] for p in pages])
    memory.save_manual(site, text, login, job_id=ctx.job_id)  # raises on a secret
    added = sum(e["url"] not in before for e in json.loads(text)["urls"])
    if (blocker := answer.get("blocker")) in BLOCKERS:
        memory.block_site(site, blocker, pages[0]["url"])
        ctx.add_gap(f"{site} needs a {blocker}: not read")
    elif (code := state_code(state)) and is_official(site) and (holds := one_line(answer.get("holds"))):
        memory.save_state_site(code, site, holds, "high", LEARNED)
    ctx.log(f"{len(pages)} pages read, {len(forms)} search forms, {added} new templates, manual "
            + ("updated" if saved else "saved") + (f", blocked: {blocker}" if blocker in BLOCKERS else ""))
    return reply(site, memory.manual(site), from_memory=False, added=added)
