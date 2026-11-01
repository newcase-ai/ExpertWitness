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


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(firecrawl, "RETRY_WAIT", 0)


def test_timeout_or_busy_is_retried_once(sources, with_key, no_wait):
    answers = [httpx.Response(408, json={"success": False}),
               httpx.Response(200, json={"success": True, "data": {"markdown": MARKDOWN, "metadata": {}}})]
    _, seen = sources(routes={"api.firecrawl.dev": lambda request: answers.pop(0)})
    assert asyncio.run(firecrawl.scrape(PAGE))["markdown"] == MARKDOWN
    assert len(seen) == 2
    _, seen = sources(routes={"api.firecrawl.dev": (429, {"success": False})})
    with pytest.raises(RuntimeError, match="^Firecrawl HTTP 429$"):
        asyncio.run(firecrawl.scrape(PAGE))
    assert len(seen) == 2


def test_server_error_is_not_retried(sources, with_key, no_wait):
    _, seen = sources(routes={"api.firecrawl.dev": (500, {"success": False})})
    with pytest.raises(RuntimeError, match="^Firecrawl HTTP 500$"):
        asyncio.run(firecrawl.scrape(PAGE))
    assert len(seen) == 1


def test_network_errors_become_fixed_text(sources, with_key, no_wait):
    def timeout(request):
        raise httpx.ReadTimeout("SYNTHETIC detail", request=request)
    _, seen = sources(routes={"api.firecrawl.dev": timeout})
    with pytest.raises(RuntimeError, match="^Firecrawl timed out$"):
        asyncio.run(firecrawl.scrape(PAGE))
    assert len(seen) == 2

    def refused(request):
        raise httpx.ConnectError("SYNTHETIC detail", request=request)
    sources(routes={"api.firecrawl.dev": refused})
    with pytest.raises(RuntimeError, match=r"^Firecrawl connection failed \(ConnectError\)$"):
        asyncio.run(firecrawl.scrape(PAGE))


def test_timeout_is_sent_to_firecrawl(sources, with_key):
    _, seen = sources(routes=answer({"markdown": MARKDOWN, "metadata": {}}))
    asyncio.run(firecrawl.scrape(PAGE, timeout_ms=60_000))
    assert json.loads(seen[0].content)["timeout"] == 60_000
