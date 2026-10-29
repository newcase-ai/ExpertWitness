"""Crossref retraction check by DOI (DESIGN 4.3, gotcha 7).

A work is retracted when its record's `updated-by[]` holds an item with `type == "retraction"`."""

import asyncio
from urllib.parse import quote

from app import http
from app.config import get_settings

API = "https://api.crossref.org/works/"
BACKOFF_MAX = 10.0  # seconds; longest Retry-After we honour before the single retry
# Public pool: 5 requests/s (app/http.py RATES) and 1 at a time. Only this limiter's per-event-loop
# lock is used, to hold one request open at a time.
_one_at_a_time = http.RateLimiter(1.0)


def doi_url(doi: str) -> str:
    return "https://doi.org/" + doi


def date_of(update: dict) -> str | None:
    """YYYY-MM-DD from Crossref `date-parts`, None if the day is not given."""
    parts = ((update.get("updated") or {}).get("date-parts") or [[]])[0]
    if len(parts) < 3 or not all(isinstance(p, int) for p in parts[:3]):
        return None
    return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"


def retry_after(response) -> float:
    try:
        return min(float(response.headers.get("retry-after", 1)), BACKOFF_MAX)
    except ValueError:
        return 1.0


async def get_record(doi: str):
    email = get_settings().contact_email
    params = {"mailto": email} if email else None
    url = API + quote(doi, safe="/")
    async with _one_at_a_time.lock():
        r = await http.fetch("crossref", url, params=params)
        if r.status_code == 429:  # back off once, then give up
            await asyncio.sleep(retry_after(r))
            r = await http.fetch("crossref", url, params=params)
    return r


async def retraction(doi: str) -> dict | None:
    """The retraction notice for `doi`, or None if Crossref shows none (or does not know the DOI)."""
    doi = doi.strip().lower()
    r = await get_record(doi)
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"Crossref HTTP {r.status_code}")
    message = r.json().get("message") or {}
    for update in message.get("updated-by") or []:
        if isinstance(update, dict) and update.get("type") == "retraction":
            notice = update.get("DOI")
            return {
                "doi": doi,
                "date": date_of(update),
                "source": update.get("source"),
                "notice_url": doi_url(notice.lower()) if isinstance(notice, str) and notice else None,
                "url": doi_url(doi),
            }
    return None
