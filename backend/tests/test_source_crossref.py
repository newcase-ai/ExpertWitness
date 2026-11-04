"""Crossref retraction check. Synthetic DOIs and records only (gotcha 8)."""

import asyncio
import time

import httpx
import pytest

from app import http
from app.sources import crossref
from tests.fakes import sources_fixture  # noqa: F401 - fixture

DOI = "10.5555/synthetic.0001"


def record(updates=()):
    return {"status": "ok", "message": {"DOI": DOI, "updated-by": list(updates)}}


RETRACTION = {"type": "retraction", "source": "retraction-watch", "DOI": "10.5555/SYNTHETIC.NOTICE",
              "updated": {"date-parts": [[2020, 3, 7]]}, "label": "Retraction"}


def route(status=200, body=None, headers=None):
    return {"api.crossref.org": lambda request: httpx.Response(status, json=body or record(), headers=headers)}


def test_retracted_work_returns_notice(sources, monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "team@example.org")
    _, seen = sources(routes=route(body=record([{"type": "correction"}, RETRACTION])))
    hit = asyncio.run(crossref.retraction(DOI.upper()))
    assert hit == {"doi": DOI, "date": "2020-03-07", "source": "retraction-watch",
                   "notice_url": "https://doi.org/10.5555/synthetic.notice", "url": f"https://doi.org/{DOI}"}
    assert seen[0].url.path == f"/works/{DOI}"
    assert seen[0].url.params["mailto"] == "team@example.org"


def test_not_retracted_is_none_and_no_mailto_without_email(sources):
    _, seen = sources(routes=route(body=record([{"type": "correction", "DOI": "10.5555/x"}])))
    assert asyncio.run(crossref.retraction(DOI)) is None
    assert "mailto" not in seen[0].url.params


def test_partial_date_is_none(sources):
    notice = RETRACTION | {"updated": {"date-parts": [[2020]]}, "DOI": None}
    sources(routes=route(body=record([notice])))
    hit = asyncio.run(crossref.retraction(DOI))
    assert hit["date"] is None and hit["notice_url"] is None


def test_unknown_doi_is_none(sources):
    sources(routes=route(404, {"message": "not found"}))
    assert asyncio.run(crossref.retraction(DOI)) is None


def test_backs_off_once_on_429(sources):
    replies = [httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json=record([RETRACTION]))]
    _, seen = sources(routes={"api.crossref.org": lambda request: replies.pop(0)})
    assert asyncio.run(crossref.retraction(DOI))["source"] == "retraction-watch"
    assert len(seen) == 2


def test_second_429_and_other_errors_raise(sources):
    sources(routes=route(429, {}, {"retry-after": "0"}))
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(crossref.retraction(DOI))
    sources(routes=route(500, {}))
    with pytest.raises(RuntimeError, match="500"):
        asyncio.run(crossref.retraction(DOI))


def test_calls_are_spaced_by_the_rate_limit(sources, monkeypatch):
    """Crossref calls go out at most 5 per second (gotcha 7)."""
    sources(routes=route())
    monkeypatch.setattr(http, "limiters", {"crossref": http.RateLimiter(http.RATES["crossref"])})

    async def three():
        return await asyncio.gather(*(crossref.retraction(DOI) for _ in range(3)))

    start = time.monotonic()
    asyncio.run(three())
    assert time.monotonic() - start >= 2 / http.RATES["crossref"] - 0.01
