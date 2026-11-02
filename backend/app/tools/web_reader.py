"""Tool 7: web reader and download (DESIGN 4B.9). `read_web_page` returns a page's text to the model
and saves nothing (gotchas 10, 15); `download_file` saves a PDF, web page or plain text file with a
text version (Firecrawl markdown for pages and PDFs, at most MAX_PDF_PAGES pages).

Legal (gotchas 1-4, 24, 25, 40): no logins, forms, CAPTCHA solving or proxy rotation; banned sites
and API hosts are refused before every fetch and after every redirect. A login (401), paywall (402)
or robot-check page blocks its site for all future jobs; any other error just fails this call."""

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlsplit

from app import http, memory
from app.config import get_settings
from app.evidence import refuse
from app.sources import SourceSkipped, firecrawl, host_of
from app.tools import FILE_MAX, RESULT_MAX, Ctx, secret, tool
from app.tools.firecrawl_search import HOSTNAME

URL_MAX = 2_000
REPLY_MAX = RESULT_MAX - 200  # margin under the framework's limit
MAX_REDIRECTS = 5
TITLE_MAX = 200
ROBOT_PAGE_MAX = 3_000  # a robot check is a short page; a long article about robot checks is not
# Block-page phrases only: "captcha" alone ("protected by reCAPTCHA", CAPTCHA research) and everyday
# words ("security check", "press and hold the power button") must not block a site. Never from a PDF.
ROBOT_WORDS = re.compile(r"(?i)verify (that )?you are (a )?human|checking your browser|cf-chl|are you a robot\?"
                         r"|complete the security check to access")
NETLOC = re.compile(r"(?i)[a-z0-9.-]+(:\d{1,5})?")  # plain ASCII host (no %, \, @, [ ] or look-alikes)
BLOCK_REASONS = {401: "login", 402: "paywall"}
ALLOWED_MIME = ("application/pdf", "text/html", "application/xhtml+xml", "text/plain")
INTERNAL_SUFFIXES = (".localhost", ".local", ".internal")
# Fixed refusals (never page or model text).
BAD_URL = "not a valid link: give one full http(s) link without spaces, hidden characters or user names"
URL_TOO_LONG = f"the link must be at most {URL_MAX} characters"
INTERNAL = "this link points to a private or internal address"
SECRET_URL = "this link looks like it holds a password or token; it is not downloaded"
NO_HOST = "the site's address could not be found"
TOO_MANY_REDIRECTS = f"more than {MAX_REDIRECTS} redirects"
TOO_LARGE = "the file is too large"
NOT_ALLOWED_TYPE = "only PDF, web page and plain text files can be downloaded"
FAKE_PDF = "the file says it is a PDF but is not one"
BLOCKED_NOW = {"login": "this site needs a login; it is now blocked for future jobs",
               "paywall": "this site is behind a paywall; it is now blocked for future jobs",
               "captcha": "this site shows a robot check; it is now blocked for future jobs"}
NO_TEXT = "Download a file: no text version (Firecrawl could not read the file)"
PDF_TIMEOUT_MS = 60_000  # turning a PDF into text is Firecrawl's slowest job; 30 s was too short live


def clean(url: str) -> str:
    """`url` stripped, with scheme and host part lower-case (HTTPS://Files.Example.org -> https://files...)."""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url  # bad_url refuses it
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower()).geturl()


def bad_url(url: str) -> str | None:
    """None if `url` (already cleaned) is one plain http(s) link; otherwise a fixed refusal."""
    if len(url) > URL_MAX:
        return URL_TOO_LONG
    if "\\" in url or any(c.isspace() or not c.isprintable() for c in url):  # also zero-width, control
        return BAD_URL
    try:
        parts = urlsplit(url)
    except ValueError:
        return BAD_URL
    if parts.scheme not in ("http", "https") or not parts.hostname or not NETLOC.fullmatch(parts.netloc):
        return BAD_URL
    return None


def url_refusal(ctx: Ctx, url: str) -> str | None:
    return bad_url(url) or ctx.fetch_ok(url)


def robot_check(text: str) -> bool:
    return len(text) <= ROBOT_PAGE_MAX and bool(ROBOT_WORDS.search(text))


def block(ctx: Ctx, url: str, reason: str) -> dict:
    """Block the site of `url` for all future jobs and refuse with a fixed text."""
    memory.block_site(host_of(url), reason, None if memory.looks_secret(url, url=True) else url)
    return refuse(BLOCKED_NOW[reason])


def fit(reply: dict) -> dict:
    """The reply's text, then title, then url cut so it fits REPLY_MAX as JSON (escaped characters take
    up to 12 JSON characters, so cut in proportion and repeat)."""
    for key in ("text", "title", "url"):
        while (over := len(json.dumps(reply)) - REPLY_MAX) > 0 and reply[key]:
            value = reply[key]
            size = len(json.dumps(value))
            reply[key] = value[:max(0, len(value) * (size - over) // size - 1)]
    return reply


@tool(label="Read a web page", cap=8)
async def read_web_page(ctx: Ctx, url: str) -> dict:
    """Read one public web page or PDF and get its main text (about 7,000 characters at most; nothing
    is saved). Use it to check a lead before relying on it. `url`: one full http(s) link. Sites that
    need a login, a payment or a robot check are refused and not read again."""
    url = clean(url)
    if refusal := url_refusal(ctx, url):
        return refuse(refusal)
    try:
        page = await firecrawl.scrape(url)
    except firecrawl.PageError as e:
        if e.status in BLOCK_REASONS:  # block the host that answered (after Firecrawl's redirects)
            final = clean(e.url or url)
            if refusal := url_refusal(ctx, final):  # a banned or API host is refused, never noted
                return refuse(refusal)
            return block(ctx, final, BLOCK_REASONS[e.status])
        raise
    if refusal := url_refusal(ctx, page["url"]):  # after redirects (gotcha 40)
        return refuse(refusal)
    if page["pages"] is None and robot_check(page["markdown"]):  # never block from a PDF
        return block(ctx, page["url"], "captcha")
    ctx.log(f"{len(page['markdown'])} characters read")  # a count only, never page text (gotcha 15)
    return fit({"url": page["url"], "title": page["title"][:TITLE_MAX], "text": page["markdown"]})


async def resolve(host: str) -> list[str]:
    """The IP addresses `host` resolves to (tests replace this)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def internal(url: str) -> str | None:
    """INTERNAL if `url` may reach a private or internal address (SSRF), NO_HOST if its host does
    not resolve, else None.
    ponytail: DNS rebinding is not covered (the host is resolved again for the request); upgrade path:
    connect to the address checked here."""
    host = host_of(url) or ""
    # HOSTNAME needs a dot and a last label starting with a letter, so IP literals in any form
    # (dotted, IPv6, 2130706433, 0x7f.1, 017700000001) and dot-less hosts never pass.
    if not HOSTNAME.fullmatch(host) or host == "localhost" or host.endswith(INTERNAL_SUFFIXES):
        return INTERNAL
    try:
        addresses = await resolve(host)
    except OSError:
        return NO_HOST
    if not addresses:
        return NO_HOST
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if not ip.is_global or ip.is_multicast or ip.is_reserved:  # also shared 100.64/10, NAT64, ::a.b.c.d
            return INTERNAL
    return None


async def fetch(ctx: Ctx, url: str) -> tuple[str, str, bytes] | dict:
    """GET `url` with redirects followed by hand, each hop checked: (final URL, mime, bytes), or a
    refusal dict. Never reads more than FILE_MAX bytes."""
    for _ in range(MAX_REDIRECTS + 1):
        if refusal := url_refusal(ctx, url) or (SECRET_URL if secret(url) else None) or await internal(url):
            return refuse(refusal)
        async with http.get_client().stream("GET", url, follow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
                url = clean(urljoin(url, r.headers["location"].strip()))
                continue
            if r.status_code in BLOCK_REASONS:
                return block(ctx, url, BLOCK_REASONS[r.status_code])
            if not r.is_success:
                raise RuntimeError(f"HTTP {r.status_code}")
            mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if mime not in ALLOWED_MIME:
                return refuse(NOT_ALLOWED_TYPE)
            size = r.headers.get("content-length", "")
            if size.isdigit() and int(size) > FILE_MAX:  # a bad header counts as unknown
                return refuse(TOO_LARGE)
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data += chunk
                if len(data) > FILE_MAX:
                    return refuse(TOO_LARGE)
            return url, mime, bytes(data)
    return refuse(TOO_MANY_REDIRECTS)


def pdf_pages(data: bytes) -> int | None:
    """A rough page count of a PDF ("/Type /Page" objects), None if none are found."""
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data)) or None


ASKED: dict[tuple[str, str], str] = {}  # (dossier_id, asked URL) -> file_id, for links that redirected


def already_saved(row: dict) -> dict:
    return {"file_id": row["file_id"], "mime": row["mime"], "has_text": bool(row["text_path"]), "already_saved": True}


@tool(label="Download a file", cap=10)
async def download_file(ctx: Ctx, url: str) -> dict:
    """Download one public PDF, web page or plain text file and save it (with a text version) to this
    expert's files, e.g. a CV, a court document or a licence record. `url`: one full http(s) link.
    Reply: the saved file's `file_id`; other file types, files over 20 MB and private addresses are
    refused. The same link twice gives the same file_id."""
    url = clean(url)
    if refusal := url_refusal(ctx, url):
        return refuse(refusal)
    saved = memory.files(ctx.dossier_id)
    asked = ASKED.get((ctx.dossier_id, url))
    if old := next((f for f in saved if f["source_url"] == url or f["file_id"] == asked), None):
        return already_saved(old)
    if not get_settings().firecrawl_api_key:  # the text version of pages and PDFs needs Firecrawl
        raise SourceSkipped("FIRECRAWL_API_KEY is not set")
    got = await fetch(ctx, url)
    if isinstance(got, dict):
        return got
    final, mime, data = got
    if old := next((f for f in saved if f["source_url"] == final), None):
        ASKED[(ctx.dossier_id, url)] = old["file_id"]
        return already_saved(old)
    if mime == "application/pdf" and not data.startswith(b"%PDF"):
        return refuse(FAKE_PDF)
    text, pages = None, pdf_pages(data) if mime == "application/pdf" else None
    if mime == "text/plain":
        text = data.decode("utf-8", errors="replace")
    else:
        why = "no text"
        try:
            page = await firecrawl.scrape(final, PDF_TIMEOUT_MS if mime == "application/pdf" else firecrawl.TIMEOUT_MS)
            if url_refusal(ctx, page["url"]):  # Firecrawl's own redirects (gotcha 40)
                why = "Firecrawl ended on a refused address"
            else:
                text, pages = page["markdown"], page["pages"] or pages
        except (SourceSkipped, RuntimeError) as e:  # fixed texts only (see firecrawl.scrape)
            why = str(e)  # the file is still saved, without text (gap below)
        if text is None:
            ctx.add_gap(f"{NO_TEXT}: {why}")
    if mime in ALLOWED_MIME[1:3] and text is not None and robot_check(text):  # web pages
        return block(ctx, final, "captcha")
    if text is not None:  # no lone surrogates: the text file must encode as UTF-8
        text = text.encode("utf-8", errors="replace").decode("utf-8")
    file_id = ctx.save_file(final, data, mime, text)
    ASKED[(ctx.dossier_id, url)] = file_id
    ctx.log(f"{len(data)} bytes saved, {mime}, text: {'yes' if text is not None else 'no'}")
    return {"file_id": file_id, "mime": mime, "pages": pages, "has_text": text is not None}
