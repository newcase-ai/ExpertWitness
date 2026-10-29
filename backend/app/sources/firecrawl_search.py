"""Firecrawl search (hosted API v2): one search engine query -> result URLs, titles and snippets.

Verified 2026-10-29 against the official docs:
- Endpoint: `POST https://api.firecrawl.dev/v2/search`, header `Authorization: Bearer <key>`.
  Body `query` (max 500 chars), `limit` (default 10), `includeDomains` / `excludeDomains` (hostnames,
  mutually exclusive; Firecrawl turns them into `site:` / `-site:` operators in one query),
  `highlights` (false = the plain snippet), `timeout` (ms), `scrapeOptions` (omitted here).
  Answer `{"success": true, "data": {"web": [{"url", "title", "description"}]}, "creditsUsed"}`;
  errors `{"success": false, "error"}` (408, 500, ...).
  https://docs.firecrawl.dev/api-reference/endpoint/search
- Cost: 2 credits per 10 results, rounded up (limit 10 = 2 credits); scraping results costs extra.
  https://docs.firecrawl.dev/features/search

Rules (PLAN gotchas 2, 4, 15, 24, 25): search results only, never `scrapeOptions` (no pages are
fetched, so no proxy is used at all: `proxy` exists only inside `scrapeOptions`); open-web searches
exclude gotcha 2 sites; a 200 answer without `success: true` is a failure. Snippets are returned to
the caller for an in-memory name check only and never stored.
"""

import httpx

from app import http
from app.config import get_settings
from app.sources import BLOCKED_DOMAINS, SourceSkipped

SEARCH = "https://api.firecrawl.dev/v2/search"
LIMIT = 10  # one 2-credit block
TIMEOUT_MS = 30_000
HTTP_TIMEOUT = httpx.Timeout(TIMEOUT_MS / 1000 + 15, connect=5.0)


def text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def request_body(query: str, domains: list[str]) -> dict:
    body = {"query": query, "limit": LIMIT, "sources": [{"type": "web"}], "highlights": False,
            "timeout": TIMEOUT_MS}
    if domains:
        body["includeDomains"] = list(domains)
    else:
        body["excludeDomains"] = list(BLOCKED_DOMAINS)  # gotcha 2: do not even search them
    return body


async def search(query: str, domains: list[str]) -> list[dict]:
    """One Firecrawl search, limited to `domains` (hosts) if given: [{"url", "title", "description"}].
    SourceSkipped: no key. RuntimeError: Firecrawl answered with an error (status code only)."""
    key = get_settings().firecrawl_api_key
    if not key:
        raise SourceSkipped("FIRECRAWL_API_KEY is not set")
    if limiter := http.limiters.get("firecrawl"):
        await limiter.wait()
    r = await http.get_client().post(SEARCH, json=request_body(query, domains),
                                     headers={"Authorization": f"Bearer {key}"}, timeout=HTTP_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Firecrawl HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        body = None
    data = body.get("data") if isinstance(body, dict) and body.get("success") is True else None
    web = data.get("web") if isinstance(data, dict) and isinstance(data.get("web"), list) else None
    if web is None:
        raise RuntimeError("Firecrawl returned an unexpected response")  # gotcha 25: 200 is not success
    return [{"url": text(h.get("url")), "title": text(h.get("title")), "description": text(h.get("description"))}
            for h in web if isinstance(h, dict) and text(h.get("url"))]
