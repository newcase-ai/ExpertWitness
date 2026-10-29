"""Firecrawl page reader. Fake HTTP only, SYNTHETIC pages (gotchas 8, 9)."""

import asyncio
import json

import httpx
import pytest

from app import store
from app.sources import SourceSkipped, firecrawl
from tests.fakes import sources_fixture  # noqa: F401 - fixture

PAGE = "https://example.org/experts/alex-rivera"
MARKDOWN = "# Alex J. Rivera, MD\n\nSYNTHETIC-PAGE-TEXT Testified in Doe v. Example Co. (2019)."


def answer(data, status=200, success=True):
    return {"api.firecrawl.dev": lambda request: httpx.Response(status, json={"success": success, "data": data})}


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")


def test_no_key_is_skipped(sources, monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    _, seen = sources()
    with pytest.raises(SourceSkipped, match="FIRECRAWL_API_KEY"):
        asyncio.run(firecrawl.scrape(PAGE))
    assert seen == []


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/in/alex-rivera", "https://scholar.google.com/citations?user=x",
    "https://www.dauberttracker.com/expert/1", "ftp://example.org/cv.pdf", "file:///etc/passwd", "not a url",
])
def test_blocked_or_non_http_urls_are_refused(sources, with_key, url):
    _, seen = sources()
    with pytest.raises(SourceSkipped):
        asyncio.run(firecrawl.scrape(url))
    assert seen == []


def test_scrape_parses_response_and_sends_safe_request(sources, with_key):
    data = {"markdown": MARKDOWN + "x" * 70_000,
            "metadata": {"title": ["Alex J. Rivera - Example Experts"], "sourceURL": PAGE,
                         "url": PAGE + "/", "statusCode": 200}}
    _, seen = sources(routes=answer(data))
    out = asyncio.run(firecrawl.scrape(PAGE))
    assert out["url"] == PAGE + "/" and out["title"] == "Alex J. Rivera - Example Experts"
    assert out["markdown"].startswith(MARKDOWN) and len(out["markdown"]) == firecrawl.MAX_MARKDOWN
    (request,) = seen
    assert request.method == "POST" and str(request.url) == "https://api.firecrawl.dev/v2/scrape"
    assert request.headers["Authorization"] == "Bearer fc-test-key"
    body = json.loads(request.content)
    assert body["url"] == PAGE and body["formats"] == ["markdown"] and body["onlyMainContent"] is True
    assert body["proxy"] == "basic" and body["storeInCache"] is False and "actions" not in body
    assert body["parsers"] == [{"type": "pdf", "maxPages": firecrawl.MAX_PDF_PAGES}]
    # page text is never stored in the URL cache
    assert store.cache_get("https://api.firecrawl.dev/v2/scrape") is None
    assert store.cache_get(PAGE) is None


def test_errors_give_status_only(sources, with_key):
    sources(routes={"api.firecrawl.dev": (402, {"success": False, "error": "SYNTHETIC secret detail"})})
    with pytest.raises(RuntimeError, match="^Firecrawl HTTP 402$"):
        asyncio.run(firecrawl.scrape(PAGE))
    sources(routes=answer({"markdown": "Not found", "metadata": {"statusCode": 404}}))
    with pytest.raises(RuntimeError, match="page HTTP 404"):
        asyncio.run(firecrawl.scrape(PAGE))
    sources(routes=answer(None, success=False))
    with pytest.raises(RuntimeError, match="unexpected"):
        asyncio.run(firecrawl.scrape(PAGE))


def test_redirect_to_blocked_site_is_dropped(sources, with_key):
    sources(routes=answer({"markdown": MARKDOWN, "metadata": {"url": "https://www.linkedin.com/in/x"}}))
    with pytest.raises(SourceSkipped):
        asyncio.run(firecrawl.scrape(PAGE))
