"""Firecrawl page reader (hosted API v2): one public page or PDF -> main-content markdown.

Verified 2026-10-28 against the official docs:
- Endpoint: `POST https://api.firecrawl.dev/v2/scrape`, header `Authorization: Bearer <key>`.
  Body `url`, `formats`, `onlyMainContent`, `timeout` (ms), `parsers` (PDF), `proxy`, `storeInCache`.
  Answer `{"success": true, "data": {"markdown", "metadata": {"title", "sourceURL", "url", "statusCode"}}}`;
  PDFs also get `metadata.totalPages` (true page count, may be missing) and `numPages` (pages parsed);
  errors 400/402/403/404/409/429/500/503 with `{"success": false, "error"}`.
  https://docs.firecrawl.dev/api-reference/endpoint/scrape
- Rate limits (hosted): Free 10 scrapes/min, Hobby 100/min. https://docs.firecrawl.dev/rate-limits
- Cost: 1 credit per page, +1 credit per PDF page. https://docs.firecrawl.dev/billing

Rules (gotchas 2, 4, 15): only http(s) URLs; gotcha 2 sites are never fetched; no `actions`
(no logins, forms or CAPTCHA solving); `proxy: basic` (no proxy rotation to get past bot checks);
main content only. The markdown stays in memory: it is returned to the caller, never written to the
URL cache or the database (only facts the caller extracts from it are stored).
"""

import asyncio
from urllib.parse import urlsplit

import httpx

from app import http
from app.config import get_settings
from app.sources import SourceSkipped, is_blocked

SCRAPE = "https://api.firecrawl.dev/v2/scrape"
TIMEOUT_MS = 30_000  # Firecrawl's own page timeout; our HTTP timeout is a little longer
RETRY_STATUS = (408, 429, 502, 503, 504)  # Firecrawl timed out, rate limit or busy: one retry
RETRY_WAIT = 2.0  # seconds before that retry
MAX_MARKDOWN = 60_000
MAX_PDF_PAGES = 10  # each PDF page costs one more credit


class PageError(RuntimeError):
    """The page itself answered with an error; `status` is its HTTP status code, `url` the page's
    final URL (after redirects)."""

    def __init__(self, status: int, url: str = ""):
        super().__init__(f"page HTTP {status}")
        self.status, self.url = status, url


def request_body(url: str, timeout_ms: int = TIMEOUT_MS) -> dict:
    return {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "timeout": timeout_ms,
        "parsers": [{"type": "pdf", "maxPages": MAX_PDF_PAGES}],
        "proxy": "basic",
        "blockAds": True,
        "storeInCache": False,  # do not keep third-party page text on Firecrawl's side either
    }


def check_url(url: str) -> None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        parts = None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceSkipped("only http(s) URLs are read")
    if is_blocked(url):
        raise SourceSkipped("site is never automated (gotcha 2)")


def first_text(value: object) -> str:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str)), "")
    return value.strip() if isinstance(value, str) else ""


def scrape_data(body: object) -> dict | None:
    """`data` of a successful answer that holds markdown, else None."""
    data = body.get("data") if isinstance(body, dict) and body.get("success") is True else None
    return data if isinstance(data, dict) and isinstance(data.get("markdown"), str) else None


async def scrape(url: str, timeout_ms: int = TIMEOUT_MS) -> dict:
    """Read one page with Firecrawl: {"url", "title", "markdown", "pages"} (markdown capped at
    MAX_MARKDOWN; pages = a PDF's page count, else None). `timeout_ms` is Firecrawl's own time limit
    (PDFs need more). A timeout, rate limit or busy answer is retried once.
    SourceSkipped: no key, not http(s), or a blocked site (also after redirects).
    RuntimeError: Firecrawl answered with an error, timed out or could not be reached (fixed text);
    PageError: the page did (status code only)."""
    key = get_settings().firecrawl_api_key
    if not key:
        raise SourceSkipped("FIRECRAWL_API_KEY is not set")
    check_url(url)
    timeout = httpx.Timeout(timeout_ms / 1000 + 15, connect=5.0)
    for attempt in (1, 2):
        if limiter := http.limiters.get("firecrawl"):
            await limiter.wait()
        try:
            r = await http.get_client().post(
                SCRAPE, json=request_body(url, timeout_ms), headers={"Authorization": f"Bearer {key}"}, timeout=timeout
            )
        except httpx.TimeoutException:
            r = None
        except httpx.HTTPError as e:
            raise RuntimeError(f"Firecrawl connection failed ({type(e).__name__})") from None
        if r is not None and r.status_code not in RETRY_STATUS:
            break
        if attempt == 1:
            await asyncio.sleep(RETRY_WAIT)
    if r is None:
        raise RuntimeError("Firecrawl timed out")
    if r.status_code != 200:
        raise RuntimeError(f"Firecrawl HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        body = None
    data = scrape_data(body)
    if data is None:
        raise RuntimeError("Firecrawl returned an unexpected response")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    status = meta.get("statusCode")
    final = first_text(meta.get("url")) or url
    if isinstance(status, int) and status >= 400:
        raise PageError(status, final)
    if final != url:
        check_url(final)  # a redirect to a blocked site is dropped
    pages = next((n for n in (meta.get("totalPages"), meta.get("numPages")) if isinstance(n, int)), None)
    return {"url": final, "title": first_text(meta.get("title")), "markdown": data["markdown"][:MAX_MARKDOWN],
            "pages": pages}
