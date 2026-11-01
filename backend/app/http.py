"""Shared HTTP client: timeouts, a User-Agent naming the project, per-source rate limits."""

import asyncio
import json
import time
from collections.abc import Callable

import httpx

from app import store
from app.config import get_settings

TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Requests per second per source (PLAN gotchas 5-7). Unknown sources are not limited.
RATES = {
    "openalex": 10.0,
    "courtlistener": 5 / 60,  # hour/day caps are counted with store.record_call/calls_since
    "crossref": 5.0,  # public pool, single records; app/sources/crossref.py also sends 1 at a time
    "oig_leie": 1.0,  # one CSV download a month at most
    "firecrawl": 8 / 60,  # hosted Free plan allows 10 scrapes/min (docs.firecrawl.dev/rate-limits)
}


def user_agent() -> str:
    email = get_settings().contact_email
    return "expert-witness-api/0.1" + (f" (mailto:{email})" if email else "")


class RateLimiter:
    """Spaces calls at least 1/rate seconds apart. Callers queue on a lock and a slot is claimed
    only when `wait()` returns, so a caller cancelled while waiting (timeout) holds no slot."""

    def __init__(self, per_second: float):
        self.interval = 1 / per_second
        self.last = float("-inf")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None

    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()  # a lock belongs to one event loop (tests run several)
        if self._loop is not loop:
            self._loop, self._lock = loop, asyncio.Lock()
        return self._lock

    async def wait(self) -> None:
        async with self.lock():
            delay = self.last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.last = time.monotonic()


limiters = {source: RateLimiter(rate) for source, rate in RATES.items()}
_client: httpx.AsyncClient | None = None


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """A configured client; tests pass an `httpx.MockTransport`."""
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        headers={"User-Agent": user_agent()},
        follow_redirects=True,
        transport=transport,
    )


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = make_client()
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """Swap the shared client (tests)."""
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def non_empty_dict(data: object) -> bool:
    return isinstance(data, dict) and bool(data)


async def fetch(
    source: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    cache: bool = False,
    cache_if: Callable[[object], bool] = non_empty_dict,
    timeout: httpx.Timeout | None = None,
) -> httpx.Response:
    """GET with the source's rate limit. `cache=True` serves/stores bodies in SQLite by URL; a
    response is stored only if it is 2xx, valid JSON and `cache_if(parsed_json)` is True.
    The response gets `from_cache` (True when no request was sent, so no quota was used)."""
    client = get_client()
    key = str(client.build_request("GET", url, params=params).url)
    if cache and (body := store.cache_get(key)) is not None:
        response = httpx.Response(200, content=body, request=httpx.Request("GET", key))
        response.from_cache = True
        return response
    if source in limiters:
        await limiters[source].wait()
    response = await client.get(url, params=params, headers=headers, **({"timeout": timeout} if timeout else {}))
    response.from_cache = False
    if cache and response.is_success and should_cache(response.content, cache_if):
        store.cache_put(key, response.content)
    return response


def should_cache(body: bytes, cache_if: Callable[[object], bool]) -> bool:
    try:
        return bool(cache_if(json.loads(body)))
    except ValueError:
        return False
