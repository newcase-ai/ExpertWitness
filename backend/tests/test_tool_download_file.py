"""Download a file with fake HTTP (MockTransport routes by host, GCS included), a fake GCS token and
the FakeAdkModel. Synthetic PDFs only ("SYNTHETIC page N" on each page)."""

import asyncio

import httpx
import pytest

from app import http, memory, store
from app.tools import API_SITE
from app.tools import download_file as module
from tests.fakes import router, run_tool, tool_ctx_fixture  # noqa: F401 - fixture
from tests.test_tool_ask_pdf import ProviderError, blank_pdf, pdf_of

PDF_URL = "https://example.edu/files/rivera-depo.pdf"
BUCKET = "test-bucket"


@pytest.fixture
def web(monkeypatch):
    """Fake HTTP: `routes` (host -> handler, filled by each test) and `seen` requests."""
    routes = {"storage.googleapis.com": lambda r: httpx.Response(200, json={})}
    transport, seen = router(routes)
    http.set_client(http.make_client(transport))
    monkeypatch.setattr(module, "gcs_token", lambda: "test-gcs-token")
    yield routes, seen
    http.set_client(None)


def serve_pdf(routes, pages):
    routes["example.edu"] = lambda r: httpx.Response(200, content=pdf_of(pages))


def download(ctx, url=PDF_URL):
    return asyncio.run(run_tool(ctx, "download_file", url=url))


def gaps(ctx):
    return [g.reason for g in store.get_research(ctx.dossier_id).gaps]


def test_downloads_saves_uploads_and_describes(tool_ctx, adk_model, web, monkeypatch):
    routes, seen = web
    serve_pdf(routes, 30)
    monkeypatch.setenv("GCS_BUCKET_NAME", BUCKET)
    adk_model.script = ["SYNTHETIC: a 30-page deposition of the expert."]
    reply = download(tool_ctx)
    file_id, name = reply["file_id"], f"expert-witness/{tool_ctx.dossier_id}/{reply['file_id']}"
    assert reply == {"file_id": file_id, "page_count": 30, "description": "SYNTHETIC: a 30-page deposition of the expert.",
                     "described_pages": "all", "gcs_uri": f"gs://{BUCKET}/{name}.pdf"}
    row = memory.file(file_id)
    assert row["source_url"] == PDF_URL and row["note_md"] == reply["description"]
    uploads = [r for r in seen if r.url.host == "storage.googleapis.com"]
    assert [r.url.params["name"] for r in uploads] == [f"{name}.pdf", f"{name}.txt"]
    assert uploads[0].headers["authorization"] == "Bearer test-gcs-token"
    assert uploads[0].content.startswith(b"%PDF") and uploads[1].content == reply["description"].encode()
    [part] = adk_model.seen[0].contents[-1].parts
    assert tool_ctx.expert.display_name in part.text and "The whole file (30 pages)" in part.text
    assert "[Page 1]\nSYNTHETIC page 1\n" in part.text and part.text.endswith("[Page 30]\nSYNTHETIC page 30")
    [(status, detail)] = [(a.status, a.detail) for a in store.activity(tool_ctx.job_id)]
    assert status == "done" and detail.startswith("30 pages, ") and "bucket" not in detail


def test_over_the_token_limit_describes_the_first_pages(tool_ctx, adk_model, web):
    routes, _ = web
    serve_pdf(routes, 150)
    adk_model.script = [ProviderError("SYNTHETIC too many tokens"), "SYNTHETIC first pages only."]
    reply = download(tool_ctx)
    assert (reply["description"], reply["described_pages"], reply["gcs_uri"]) == ("SYNTHETIC first pages only.", "1-100", None)
    [part] = adk_model.seen[1].contents[-1].parts
    assert "Pages 1-100 of a 150-page file" in part.text
    assert part.text.endswith("[Page 100]\nSYNTHETIC page 100")
    assert store.activity(tool_ctx.job_id)[0].detail.endswith(", not in the bucket")
    # still over the limit at 20 pages: the file is kept without a description
    adk_model.script = [ProviderError("SYNTHETIC")] * 3
    reply = download(tool_ctx)
    assert reply["file_id"] and reply["description"] is None and len(adk_model.seen) == 5
    assert gaps(tool_ctx) == ["file not described (model call failed (ProviderError, HTTP 400))"]


def test_refused_downloads_save_nothing(tool_ctx, adk_model, web, monkeypatch):
    routes, seen = web
    replies = {
        "/missing.pdf": lambda: httpx.Response(404),
        "/page.html": lambda: httpx.Response(200, content=b"<html>SYNTHETIC</html>"),
        "/broken.pdf": lambda: httpx.Response(200, content=b"%PDF-1.4 SYNTHETIC broken"),
        "/moved.pdf": lambda: httpx.Response(302, headers={"location": "https://api.openalex.org/x.pdf"}),
        "/big.pdf": lambda: httpx.Response(200, content=b"%PDF" + b"x" * 50),
    }
    routes["example.edu"] = lambda r: replies[r.url.path]()
    monkeypatch.setattr(module, "FILE_MAX", 40)
    for path, error in (("/missing.pdf", "download failed: HTTP 404"),
                        ("/page.html", "not a PDF: download_file saves PDFs only"),
                        ("/broken.pdf", "the PDF cannot be read (damaged or password protected)"),
                        ("/moved.pdf", API_SITE), ("/big.pdf", "file too large")):
        assert download(tool_ctx, f"https://example.edu{path}") == {"error": error}
    assert download(tool_ctx, "https://www.courtlistener.com/x.pdf") == {"error": API_SITE}
    assert not any(r.url.host == "api.openalex.org" for r in seen)  # the redirect is checked before it is followed
    assert memory.files(tool_ctx.dossier_id) == [] and adk_model.seen == []


def test_upload_failure_keeps_the_file_and_says_so(tool_ctx, adk_model, web, monkeypatch):
    routes, _ = web
    serve_pdf(routes, 2)
    routes["storage.googleapis.com"] = lambda r: httpx.Response(403, json={})
    monkeypatch.setenv("GCS_BUCKET_NAME", BUCKET)
    adk_model.script = ["SYNTHETIC two pages."]
    reply = download(tool_ctx)
    assert reply["file_id"] and reply["description"] == "SYNTHETIC two pages." and reply["gcs_uri"] is None
    assert gaps(tool_ctx) == ["file not uploaded to the bucket (403)"]


def test_a_pdf_without_text_is_kept_but_not_described(tool_ctx, adk_model, web):
    routes, _ = web
    routes["example.edu"] = lambda r: httpx.Response(200, content=blank_pdf(4))
    reply = download(tool_ctx)
    assert reply["file_id"] and reply["page_count"] == 4 and reply["description"] is None
    assert gaps(tool_ctx) == ["file not described (no text in the PDF: scanned?)"] and adk_model.seen == []
