"""Download a file (DESIGN tool 7): download one PDF, save it (data/files/ and the GCS bucket) and
describe it shortly with the model. The description is the file's note (memory.set_file_note) and a
.txt next to the PDF in the bucket. A PDF too big for one model call is described from its first
pages."""

import asyncio
import io

import google.auth
import google.auth.transport.requests
import httpx
from google.auth.exceptions import GoogleAuthError
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app import http, llm, memory
from app.config import get_settings
from app.evidence import refuse
from app.tools import FILE_MAX, Ctx, tool
from app.tools.ask_pdf import cut

MAX_REDIRECTS = 5
DESCRIPTION_MAX = 1_500
FIRST_PAGES = (100, 20)  # described pages when the whole file is over the model's token limit
GCS_UPLOAD = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
GCS_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
UPLOAD_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

INSTRUCTIONS = """You describe one PDF for a researcher vetting an expert witness. In at most 5
sentences and your own words: what kind of document it is (CV, deposition or trial transcript,
court opinion, expert report, article, licence record...), who wrote it or appears in it, its date,
and what it says about the expert's professional work (cases, roles, employers, licences). Say
whether the expert is named in it. If you saw only the first pages, say so. Professional records
only: no home addresses, family or health details."""


async def download(ctx: Ctx, url: str) -> tuple[str, bytes]:
    """(final URL, body). Every redirect hop is checked with ctx.fetch_ok; raises ValueError with a
    fixed reason for a refused URL, a failed download or a file over FILE_MAX."""
    for _ in range(MAX_REDIRECTS + 1):
        if refusal := ctx.fetch_ok(url):
            raise ValueError(refusal)
        async with http.get_client().stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                url = str(response.url.join(response.headers["location"]))
                continue
            if not response.is_success:
                raise ValueError(f"download failed: HTTP {response.status_code}")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data += chunk
                if len(data) > FILE_MAX:
                    raise ValueError("file too large")
            return url, bytes(data)
    raise ValueError("too many redirects")


def gcs_token() -> str:
    credentials, _ = google.auth.default(scopes=[GCS_SCOPE])
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


async def upload(bucket: str, name: str, data: bytes, mime: str) -> str:
    """Upload one object (GCS JSON API, ADC credentials) and return its gs:// URI."""
    token = await asyncio.to_thread(gcs_token)
    response = await http.get_client().post(
        GCS_UPLOAD.format(bucket=bucket), params={"uploadType": "media", "name": name}, content=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": mime}, timeout=UPLOAD_TIMEOUT)
    response.raise_for_status()
    return f"gs://{bucket}/{name}"


async def describe(ctx: Ctx, path: str, data: bytes, count: int) -> tuple[str, str]:
    """(description, described pages). The whole file first; over the token limit (HTTP 400), the
    first FIRST_PAGES pages."""
    tries = [count] + [n for n in FIRST_PAGES if n < count]
    for n, pages in enumerate(tries, 1):
        whole = pages == count
        prompt = (f"The expert: {ctx.expert.display_name}\n\nThe attached PDF is "
                  + (f"the whole file ({count} pages)." if whole else f"pages 1-{pages} of a {count}-page file."))
        try:
            pdf = data if whole else cut(path, 1, pages)[0]
            answer = (await llm.call_model(INSTRUCTIONS, prompt, pdf=pdf)).text.strip()
            return answer[:DESCRIPTION_MAX], "all" if whole else f"1-{pages}"
        except llm.ModelError as e:
            if "HTTP 400" not in str(e) or n == len(tries):
                raise
    raise AssertionError("unreachable")


@tool(label="Download a file", cap=10)
async def download_file(ctx: Ctx, url: str) -> dict:
    """Download one PDF from a URL a tool or a file gave you (never a guessed one): it is saved, put
    in the files bucket and described shortly. Reply: `file_id` and `page_count` for ask_pdf, and
    `description` (what the file is and what it says about the expert; from its first pages only
    when `described_pages` is not "all"). Then read it with ask_pdf. PDFs only."""
    try:
        url, data = await download(ctx, url)
        if not data.startswith(b"%PDF"):
            return refuse("not a PDF: download_file saves PDFs only")
        count = len(PdfReader(io.BytesIO(data)).pages)
        file_id = ctx.save_file(url, data, "application/pdf")
    except ValueError as e:  # fixed reasons from download and save_file
        return refuse(str(e))
    except PyPdfError:
        return refuse("the PDF cannot be read (damaged or password protected)")
    path = memory.file(file_id)["path"]

    description, described = None, None
    try:
        description, described = await describe(ctx, path, data, count)
        memory.set_file_note(file_id, description)
    except llm.ModelError as e:
        ctx.add_gap(f"file not described ({e})")

    gcs_uri, bucket = None, get_settings().gcs_bucket
    if bucket:
        name = f"expert-witness/{ctx.dossier_id}/{file_id}"
        try:
            gcs_uri = await upload(bucket, f"{name}.pdf", data, "application/pdf")
            if description:
                await upload(bucket, f"{name}.txt", description.encode(), "text/plain; charset=utf-8")
        except (httpx.HTTPError, GoogleAuthError) as e:
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else type(e).__name__
            ctx.add_gap(f"file not uploaded to the bucket ({status})")
    ctx.log(f"{count} pages, {len(data):,} bytes" + ("" if gcs_uri else ", not in the bucket"))
    return {"file_id": file_id, "page_count": count, "description": description,
            "described_pages": described, "gcs_uri": gcs_uri}
