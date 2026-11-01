"""Tool 7: web reader and download (DESIGN 4B.9). Fake HTTP and a fake DNS resolver only, SYNTHETIC
pages and files (gotchas 8, 9)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import memory, store
from app.evidence import TOOL_FAILED
from app.tools import API_SITE, BANNED_SITE, RESULT_MAX, web_reader
from app.tools.web_reader import (
    BAD_URL,
    BLOCKED_NOW,
    FAKE_PDF,
    INTERNAL,
    NO_TEXT,
    NOT_ALLOWED_TYPE,
    PDF_TIMEOUT_MS,
    SECRET_URL,
    TOO_LARGE,
    TOO_MANY_REDIRECTS,
    URL_TOO_LONG,
)
from tests.fakes import run_tool, sources_fixture, tool_ctx_fixture  # noqa: F401 - fixtures

MARK = "SYNTHETIC-PAGE-TEXT"  # marker: page text must never be stored by read_web_page
PAGE = "https://example.org/experts/alex-rivera"
FILE = "https://files.example.org/cv.pdf"
PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> 2 0 obj << /Type /Page >> 3 0 obj << /Type /Pages >>"
MARKDOWN = f"# Alex J. Rivera\n\n{MARK} Testified in Doe v. Example Co. (2019)."
ROBOT = "## Checking your browser\n\nPlease verify you are human to continue."


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every host resolves to a public address unless a test says otherwise (never real DNS)."""
    async def resolve(host):
        return ["8.8.8.8"]
    monkeypatch.setattr(web_reader, "resolve", resolve)


def firecrawl(markdown=MARKDOWN, url=None, status=None, title="Alex J. Rivera - CV", **meta):
    """Fake Firecrawl scrape: echoes the asked URL (or `url`) as the final URL."""
    def answer(request):
        asked = json.loads(request.content)["url"]
        metadata = {"title": title, "url": url or asked, "statusCode": status or 200, **meta}
        body = {"success": True, "data": {"markdown": markdown, "metadata": metadata}}
        return httpx.Response(200, content=json.dumps(body), headers={"content-type": "application/json"})
    return {"api.firecrawl.dev": answer}


def file_host(body=PDF, mime="application/pdf", status=200, headers=None):
    return {"files.example.org": lambda request: httpx.Response(
        status, content=body, headers={"content-type": mime, **(headers or {})})}


def redirect(to):
    return {"files.example.org": lambda request: httpx.Response(302, headers={"location": to})}


def hosts(seen):
    return [r.url.host for r in seen]


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


def stored(ctx, tmp_path) -> str:
    db = (tmp_path / "data" / "test.db").read_bytes().decode(errors="ignore")
    return db + store.get_job(ctx.job_id).model_dump_json() + json.dumps(
        [a.model_dump() for a in store.activity(ctx.job_id)]) + json.dumps(gaps(ctx))


def read(ctx, url):
    return asyncio.run(run_tool(ctx, "read_web_page", url=url))


def download(ctx, url=FILE):
    return asyncio.run(run_tool(ctx, "download_file", url=url))


# read_web_page


def test_page_read_without_storing_text(tool_ctx, sources, with_key, tmp_path):
    _, seen = sources(routes=firecrawl())
    reply = read(tool_ctx, f"  {PAGE}  ")
    assert reply == {"url": PAGE, "title": "Alex J. Rivera - CV", "text": MARKDOWN}
    assert json.loads(seen[0].content)["url"] == PAGE
    assert rows(tool_ctx) == [("read_web_page", "done", f"{len(MARKDOWN)} characters read")]
    assert MARK not in stored(tool_ctx, tmp_path)  # gotchas 10, 15
    assert tool_ctx.findings() == [] and memory.files(tool_ctx.dossier_id) == []
    assert not (tmp_path / "data" / "files").exists()


def test_long_page_reply_fits_result_max(tool_ctx, sources, with_key):
    sources(routes=firecrawl(markdown="é\"x" * 20_000, title="t" * 500))
    reply = read(tool_ctx, PAGE)
    assert RESULT_MAX - 300 < len(json.dumps(reply)) <= RESULT_MAX
    assert reply["text"] and len(reply["title"]) == 200


@pytest.mark.parametrize(("final", "refusal"), [
    ("https://www.linkedin.com/in/alex-rivera", "source not available"),  # Firecrawl's own check
    ("https://www.courtlistener.com/opinion/1/x/", API_SITE),  # gotcha 40
])
def test_read_redirect_to_banned_or_api_site_is_refused(tool_ctx, sources, with_key, final, refusal):
    sources(routes=firecrawl(url=final))
    reply = read(tool_ctx, PAGE)
    assert set(reply) == {"error"} and refusal in reply["error"] and MARK not in json.dumps(reply)


def test_captcha_page_blocks_the_domain(tool_ctx, sources, with_key, tmp_path):
    _, seen = sources(routes=firecrawl(markdown=ROBOT))
    assert read(tool_ctx, PAGE) == {"error": BLOCKED_NOW["captcha"]}
    assert memory.blocked("example.org") == "captcha" and memory.blocked("example.com") is None
    assert rows(tool_ctx) == [("read_web_page", "refused", BLOCKED_NOW["captcha"])]
    assert "verify you are human" not in stored(tool_ctx, tmp_path)
    assert read(tool_ctx, PAGE + "/other") == {"error": "blocked site: captcha"}  # no second fetch
    assert len(seen) == 1


def test_a_long_article_about_captchas_is_read(tool_ctx, sources, with_key):
    sources(routes=firecrawl(markdown="Why every site shows a captcha. " + "x" * 5_000))
    assert "text" in read(tool_ctx, PAGE)
    assert memory.blocked("example.org") is None


@pytest.mark.parametrize(("status", "reason"), [(401, "login"), (402, "paywall")])
def test_login_or_paywall_page_blocks_the_domain(tool_ctx, sources, with_key, status, reason):
    sources(routes=firecrawl(status=status))
    assert read(tool_ctx, PAGE) == {"error": BLOCKED_NOW[reason]}
    assert memory.blocked("example.org") == reason


@pytest.mark.parametrize("markdown", [
    "Our airport security check took two hours. Dr. Alex Rivera, toxicologist.",
    "Alex Rivera CV. Hobbies: building not a robot competitions.",
    "Firm news: unusual traffic on I-95 delayed the hearing.",
])
def test_short_normal_pages_do_not_block(tool_ctx, sources, with_key, markdown):
    sources(routes=firecrawl(markdown=markdown))
    assert read(tool_ctx, PAGE)["text"] == markdown
    assert memory.blocked("example.org") is None


@pytest.mark.parametrize(("final", "refusal"), [
    ("https://example.org/" + "a" * 9_000, URL_TOO_LONG),
    ("https://user@example.org/cv", BAD_URL),
    ("https://www.%6Cinkedin.com/in/x", BAD_URL),
])
def test_firecrawl_final_url_gets_the_full_check(tool_ctx, sources, with_key, final, refusal):
    sources(routes=firecrawl(url=final))
    assert read(tool_ctx, PAGE) == {"error": refusal}


@pytest.mark.parametrize("markdown", [
    "This site is protected by reCAPTCHA and the Google Privacy Policy and Terms of Service apply.",
    "arXiv:2401.00001. We measure how fast people and programs solve CAPTCHA puzzles. Abstract only.",
    "To restart the monitor, press and hold the power button for ten seconds.",
])
def test_pages_that_only_mention_robot_checks_do_not_block(tool_ctx, sources, with_key, markdown):
    sources(routes=firecrawl(markdown=markdown))
    assert read(tool_ctx, PAGE)["text"] == markdown
    assert memory.blocked("example.org") is None


@pytest.mark.parametrize("markdown", [ROBOT, "Complete the security check to access example.org", "cf-chl-bypass"])
def test_real_block_pages_still_block(tool_ctx, sources, with_key, markdown):
    sources(routes=firecrawl(markdown=markdown))
    assert read(tool_ctx, PAGE) == {"error": BLOCKED_NOW["captcha"]}
    assert memory.blocked("example.org") == "captcha"


def test_a_pdf_never_blocks(tool_ctx, sources, with_key):
    sources(routes=firecrawl(markdown=ROBOT, numPages=1))
    assert read(tool_ctx, PAGE + ".pdf")["text"] == ROBOT
    assert memory.blocked("example.org") is None


def test_login_on_an_api_host_after_redirect_is_refused_without_a_block(tool_ctx, sources, with_key):
    sources(routes=firecrawl(status=401, url="https://www.courtlistener.com/sign-in/"))
    assert read(tool_ctx, PAGE) == {"error": API_SITE}
    assert memory.blocked("courtlistener.com") is None and memory.blocked("example.org") is None


@pytest.mark.parametrize("url", ["HTTPS://example.org/experts/alex-rivera", "HtTpS://Example.ORG/experts/alex-rivera"])
def test_upper_case_scheme_and_host_are_read(tool_ctx, sources, with_key, url):
    _, seen = sources(routes=firecrawl())
    assert read(tool_ctx, url)["url"] == PAGE
    assert json.loads(seen[0].content)["url"] == PAGE


def test_login_after_a_firecrawl_redirect_blocks_the_final_host(tool_ctx, sources, with_key):
    sources(routes=firecrawl(status=401, url="https://login.example.net/sso"))
    assert read(tool_ctx, PAGE) == {"error": BLOCKED_NOW["login"]}
    assert memory.blocked("login.example.net") == "login" and memory.blocked("example.org") is None


@pytest.mark.parametrize(("url", "final"), [
    ("https://example.org/" + "é" * 1_900, None),  # long asked URL
    (PAGE, "https://example.org/" + "é" * 1_950),  # long final URL from Firecrawl
])
def test_long_urls_still_fit_result_max(tool_ctx, sources, with_key, url, final):
    sources(routes=firecrawl(markdown="x" * 20_000, url=final, title="\U0001F600" * 500))
    reply = read(tool_ctx, url)
    assert set(reply) == {"url", "title", "text"} and len(json.dumps(reply)) <= RESULT_MAX


def test_403_fails_once_without_a_block(tool_ctx, sources, with_key):
    sources(routes=firecrawl(status=403))
    assert read(tool_ctx, PAGE) == {"error": TOOL_FAILED}
    assert memory.blocked("example.org") is None
    assert rows(tool_ctx) == [("read_web_page", "failed", "PageError")]


# Both tools


@pytest.mark.parametrize(("url", "refusal"), [
    ("https://example.org/a page", BAD_URL),
    ("https://exa\u200bmple.org/", BAD_URL),  # zero-width space
    ("https://example.org/\x00", BAD_URL),
    ("https://user@example.org/cv.pdf", BAD_URL),
    ("https://user:pw@example.org/", BAD_URL),
    ("http://[::1]/", BAD_URL),  # IP literals in brackets
    ("http://[::ffff:127.0.0.1]/", BAD_URL),
    # disguised banned / API hosts: percent-encoded, full-width letters or dot, backslash tricks
    ("https://www.%6Cinkedin.com/in/alex-rivera", BAD_URL),
    ("https://www.\uff4c\uff49\uff4e\uff4b\uff45\uff44\uff49\uff4e.com/in/alex-rivera", BAD_URL),
    ("https://www.linkedin\uff0ecom/in/alex-rivera", BAD_URL),
    ("https://www.courtlistener.com\\.example.org/opinion/1/x/", BAD_URL),
    ("https://dauberttracker.com\\.example.org/", BAD_URL),
    ("https://www.%63ourtlistener.com/opinion/1/x/", BAD_URL),
    ("https://example.org/a\\b", BAD_URL),
    ("ftp://example.org/cv.pdf", BAD_URL),
    ("javascript:alert(1)", BAD_URL),
    ("https://example.org/" + "a" * 2_000, URL_TOO_LONG),
    ("https://www.courtlistener.com/opinion/1/x/", API_SITE),
    ("https://www.linkedin.com/in/alex-rivera", BANNED_SITE),
])
@pytest.mark.parametrize("tool_name", ["read_web_page", "download_file"])
def test_bad_or_refused_urls_never_fetched(tool_ctx, sources, with_key, url, refusal, tool_name):
    _, seen = sources(routes=firecrawl() | file_host())
    assert asyncio.run(run_tool(tool_ctx, tool_name, url=url)) == {"error": refusal}
    assert seen == []


@pytest.mark.parametrize("tool_name", ["read_web_page", "download_file"])
def test_no_firecrawl_key_is_skipped(tool_ctx, sources, monkeypatch, tool_name):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    _, seen = sources(routes=firecrawl() | file_host())
    reply = asyncio.run(run_tool(tool_ctx, tool_name, url=FILE))
    assert "FIRECRAWL_API_KEY" in reply["error"] and seen == []
    assert rows(tool_ctx)[0][:2] == (tool_name, "skipped")
    assert [step for step, _ in gaps(tool_ctx)] == [tool_name]


# download_file


def test_file_and_text_saved_under_data_files(tool_ctx, sources, with_key, tmp_path):
    _, seen = sources(routes=file_host() | firecrawl(numPages=2, totalPages=3))
    reply = download(tool_ctx)
    file_id = reply["file_id"]
    assert reply == {"file_id": file_id, "mime": "application/pdf", "pages": 3, "has_text": True}
    assert hosts(seen) == ["files.example.org", "api.firecrawl.dev"]
    assert json.loads(seen[1].content)["url"] == FILE
    [row] = memory.files(tool_ctx.dossier_id)
    folder = tmp_path / "data" / "files" / tool_ctx.dossier_id
    assert Path(row["path"]) == folder / f"{file_id}.pdf" and Path(row["path"]).read_bytes() == PDF
    assert row["source_url"] == FILE and row["bytes"] == len(PDF)
    assert tool_ctx.file_text(file_id) == MARKDOWN
    assert rows(tool_ctx)[0][:2] == ("download_file", "done")


def test_pdf_page_count_without_firecrawl_metadata(tool_ctx, sources, with_key):
    sources(routes=file_host() | firecrawl())
    assert download(tool_ctx)["pages"] == 2  # two /Type /Page objects, /Pages not counted


def test_same_url_twice_after_a_redirect(tool_ctx, sources, with_key):
    def files(request):
        if request.url.path == "/cv.pdf":
            return httpx.Response(302, headers={"location": "/files/cv-v2.pdf"})
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    _, seen = sources(routes={"files.example.org": files} | firecrawl())
    first = download(tool_ctx)
    assert download(tool_ctx) == {"file_id": first["file_id"], "mime": "application/pdf", "has_text": True,
                                  "already_saved": True}
    assert download(tool_ctx, "https://files.example.org/files/cv-v2.pdf")["file_id"] == first["file_id"]
    assert hosts(seen) == ["files.example.org", "files.example.org", "api.firecrawl.dev"]
    [row] = memory.files(tool_ctx.dossier_id)
    assert row["source_url"] == "https://files.example.org/files/cv-v2.pdf"


def test_same_final_url_from_another_link_is_not_saved_twice(tool_ctx, sources, with_key):
    def files(request):
        if request.url.path == "/latest":
            return httpx.Response(302, headers={"location": "/cv.pdf"})
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    sources(routes={"files.example.org": files} | firecrawl())
    first = download(tool_ctx)
    web_reader.ASKED.clear()  # as after a restart: only the saved final URL is known
    assert download(tool_ctx, "https://files.example.org/latest")["file_id"] == first["file_id"]
    assert len(memory.files(tool_ctx.dossier_id)) == 1


def test_same_url_twice_gives_the_same_file_with_one_fetch(tool_ctx, sources, with_key):
    _, seen = sources(routes=file_host() | firecrawl())
    first = download(tool_ctx)
    second = download(tool_ctx)
    assert second["file_id"] == first["file_id"] and second["already_saved"] is True
    assert hosts(seen) == ["files.example.org", "api.firecrawl.dev"]
    assert len(memory.files(tool_ctx.dossier_id)) == 1


def test_plain_text_needs_no_firecrawl(tool_ctx, sources, with_key):
    _, seen = sources(routes=file_host(body=b"Licence record: active", mime="Text/Plain; charset=utf-8"))
    reply = download(tool_ctx, "https://files.example.org/licence.txt")
    assert reply["mime"] == "text/plain" and reply["has_text"] is True and reply["pages"] is None
    assert tool_ctx.file_text(reply["file_id"]) == "Licence record: active"
    assert hosts(seen) == ["files.example.org"]


def test_firecrawl_failure_still_saves_the_file_with_a_gap(tool_ctx, sources, with_key):
    sources(routes=file_host() | {"api.firecrawl.dev": (500, {"success": False})})
    reply = download(tool_ctx)
    assert reply["has_text"] is False and tool_ctx.file_text(reply["file_id"]) == ""
    assert gaps(tool_ctx) == [("download_file", f"{NO_TEXT}: Firecrawl HTTP 500")]


def test_pdf_gets_the_longer_firecrawl_time_limit(tool_ctx, sources, with_key):
    _, seen = sources(routes=file_host() | firecrawl())
    download(tool_ctx)
    body = json.loads(next(r for r in seen if r.url.host == "api.firecrawl.dev").content)
    assert body["timeout"] == PDF_TIMEOUT_MS


@pytest.mark.parametrize(("to", "refusal"), [
    ("https://www.linkedin.com/in/alex-rivera", BANNED_SITE),
    ("https://www.courtlistener.com/opinion/1/x/", API_SITE),
    ("http://127.0.0.1/admin", INTERNAL),
    ("/../../user@evil.example.com", None),  # relative Location: joined, stays on files.example.org
])
def test_download_redirects_are_checked(tool_ctx, sources, with_key, to, refusal):
    hops = []

    def files(request):
        hops.append(str(request.url))
        if len(hops) == 1:
            return httpx.Response(302, headers={"location": to})
        return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    _, seen = sources(routes={"files.example.org": files} | firecrawl())
    reply = download(tool_ctx)
    if refusal:
        assert reply == {"error": refusal} and hosts(seen) == ["files.example.org"]
        assert memory.files(tool_ctx.dossier_id) == []
    else:
        assert hops == [FILE, "https://files.example.org/user@evil.example.com"] and "file_id" in reply


def test_download_redirect_to_a_banned_firecrawl_url_saves_no_text(tool_ctx, sources, with_key):
    sources(routes=file_host() | firecrawl(url="https://www.courtlistener.com/opinion/1/x/"))
    assert download(tool_ctx)["has_text"] is False
    assert gaps(tool_ctx) == [("download_file", f"{NO_TEXT}: Firecrawl ended on a refused address")]


def test_redirect_to_a_host_resolving_internally_is_refused(tool_ctx, sources, with_key, monkeypatch):
    async def resolve(host):
        return ["10.0.0.5"] if host == "intranet.example.org" else ["8.8.8.8"]
    monkeypatch.setattr(web_reader, "resolve", resolve)
    _, seen = sources(routes=redirect("https://intranet.example.org/x.pdf"))
    assert download(tool_ctx) == {"error": INTERNAL}
    assert hosts(seen) == ["files.example.org"]


def test_too_many_redirects(tool_ctx, sources, with_key):
    _, seen = sources(routes=redirect(FILE))
    assert download(tool_ctx) == {"error": TOO_MANY_REDIRECTS}
    assert len(seen) == web_reader.MAX_REDIRECTS + 1


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://10.0.0.1/cv.pdf",
    "http://2130706433/", "http://0x7f.1/", "http://017700000001/", "http://127.1/", "http://localhost:8000/",
    "http://LOCALHOST./", "http://api.localhost/", "http://printer.local/", "http://db.internal/", "http://intranet/",
])
def test_internal_addresses_are_refused(tool_ctx, sources, with_key, url):
    _, seen = sources(routes=file_host() | firecrawl())
    assert download(tool_ctx, url) == {"error": INTERNAL}
    assert seen == []


@pytest.mark.parametrize("address", ["10.1.2.3", "127.0.0.1", "192.168.1.1", "169.254.169.254", "::1",
                                     "::ffff:10.0.0.1", "fe80::1%en0", "0.0.0.0", "224.0.0.1",
                                     "100.64.0.1", "100.100.100.200", "64:ff9b::7f00:1", "::127.0.0.1"])  # shared address space is not global
def test_dns_to_an_internal_address_is_refused(tool_ctx, sources, with_key, monkeypatch, address):
    async def resolve(host):
        return ["8.8.8.8", address]  # any internal address is enough
    monkeypatch.setattr(web_reader, "resolve", resolve)
    _, seen = sources(routes=file_host() | firecrawl())
    assert download(tool_ctx) == {"error": INTERNAL}
    assert seen == []


def test_download_401_blocks_and_403_does_not(tool_ctx, sources, with_key):
    sources(routes=file_host(status=403))
    assert download(tool_ctx) == {"error": TOOL_FAILED}
    assert memory.blocked("files.example.org") is None
    sources(routes=file_host(status=401))
    assert download(tool_ctx) == {"error": BLOCKED_NOW["login"]}
    assert memory.blocked("files.example.org") == "login"


def test_captcha_web_page_download_blocks_and_saves_nothing(tool_ctx, sources, with_key):
    sources(routes=file_host(body=b"<html>robot check</html>", mime="text/html") | firecrawl(markdown=ROBOT))
    assert download(tool_ctx, "https://files.example.org/cv") == {"error": BLOCKED_NOW["captcha"]}
    assert memory.blocked("files.example.org") == "captcha" and memory.files(tool_ctx.dossier_id) == []


def test_over_size_by_content_length(tool_ctx, sources, with_key, monkeypatch):
    monkeypatch.setattr(web_reader, "FILE_MAX", 50)
    _, seen = sources(routes=file_host() | firecrawl())
    assert download(tool_ctx) == {"error": TOO_LARGE}
    assert hosts(seen) == ["files.example.org"] and memory.files(tool_ctx.dossier_id) == []


def test_over_size_while_streaming(tool_ctx, sources, with_key, monkeypatch):
    monkeypatch.setattr(web_reader, "FILE_MAX", 50)
    sent = []

    async def body():
        for _ in range(100):
            sent.append(1)
            yield b"%PDF-1.4 " + b"x" * 20

    def files(request):
        response = httpx.Response(200, content=body(), headers={"content-type": "application/pdf"})
        assert "content-length" not in response.headers
        return response
    sources(routes={"files.example.org": files} | firecrawl())
    assert download(tool_ctx) == {"error": TOO_LARGE}
    assert len(sent) < 10  # stopped reading early


def test_disallowed_mime_is_refused(tool_ctx, sources, with_key):
    _, seen = sources(routes=file_host(body=b"\x89PNG", mime="image/png") | firecrawl())
    assert download(tool_ctx) == {"error": NOT_ALLOWED_TYPE}
    assert hosts(seen) == ["files.example.org"]


@pytest.mark.parametrize("url", [
    FILE + "?token=abc123",
    FILE + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Security-Token=FwoGZXIvYXdzE&X-Amz-Signature=ab12",
    FILE + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE&X-Amz-Signature=ab12",  # S3
    FILE + "?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Signature=ab12",  # GCS
    FILE + "?sv=2022-11-02&sr=b&sig=abc%3D",  # Azure SAS
    FILE + "?Expires=1700000000&Signature=abc&Key-Pair-Id=K2EXAMPLE",  # CloudFront
    FILE + "?access_token=abc", FILE + "?accessToken=abc", FILE + "?id_token=abc", FILE + "?auth=abc",
    FILE + "?sessionid=abc",
])
def test_secret_looking_url_is_refused_before_anything_is_written(tool_ctx, sources, with_key, tmp_path, url):
    _, seen = sources(routes=file_host() | firecrawl())
    assert download(tool_ctx, url) == {"error": SECRET_URL}
    assert seen == [] and memory.files(tool_ctx.dossier_id) == []
    assert not (tmp_path / "data" / "files").exists()


def test_redirect_to_a_secret_looking_url_is_refused(tool_ctx, sources, with_key, tmp_path):
    _, seen = sources(routes=redirect("/cv.pdf?token=abc123"))
    assert download(tool_ctx) == {"error": SECRET_URL}
    assert len(seen) == 1 and not (tmp_path / "data" / "files").exists()


def test_lone_surrogate_in_text_is_cleaned_not_orphaned(tool_ctx, sources, with_key, tmp_path):
    sources(routes=file_host() | firecrawl(markdown="Page one \ud800 end"))
    reply = download(tool_ctx)
    assert tool_ctx.file_text(reply["file_id"]) == "Page one ? end"
    [row] = memory.files(tool_ctx.dossier_id)
    on_disk = sorted(p.name for p in (tmp_path / "data" / "files" / tool_ctx.dossier_id).iterdir())
    assert on_disk == sorted([Path(row["path"]).name, Path(row["text_path"]).name])


def test_bad_content_length_counts_as_unknown(tool_ctx, sources, with_key, monkeypatch):
    def files(request):
        response = httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        response.headers["content-length"] = "abc"
        return response
    sources(routes={"files.example.org": files} | firecrawl())
    assert "file_id" in download(tool_ctx)
    monkeypatch.setattr(web_reader, "FILE_MAX", 50)
    assert download(tool_ctx, FILE + "?v=2") == {"error": TOO_LARGE}  # the streamed limit still applies


@pytest.mark.parametrize("url", [FILE + "?id=123", FILE + "?docid=88", "https://files.example.org/download.aspx?file=a",
                                 FILE + "?session=2023", FILE + "?author=rivera"])
def test_normal_query_links_are_downloaded(tool_ctx, sources, with_key, url):
    sources(routes=file_host() | firecrawl())
    assert "file_id" in download(tool_ctx, url)


def test_host_case_does_not_make_a_second_file(tool_ctx, sources, with_key):
    _, seen = sources(routes=file_host() | firecrawl())
    first = download(tool_ctx, "HTTPS://FILES.example.org/cv.pdf")
    assert download(tool_ctx, FILE)["file_id"] == first["file_id"]
    assert hosts(seen) == ["files.example.org", "api.firecrawl.dev"]
    assert [r["source_url"] for r in memory.files(tool_ctx.dossier_id)] == [FILE]


def test_fake_pdf_is_refused(tool_ctx, sources, with_key):
    _, seen = sources(routes=file_host(body=b"<html>not a pdf</html>") | firecrawl())
    assert download(tool_ctx) == {"error": FAKE_PDF}
    assert hosts(seen) == ["files.example.org"] and memory.files(tool_ctx.dossier_id) == []
