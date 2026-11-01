"""Ask a PDF (one question about a page range of one saved PDF) with the FakeAdkModel. Synthetic
blank-page PDFs only: the fake model never reads them."""

import asyncio
import io
import json

from pypdf import PdfReader, PdfWriter

from app import store
from app.tools import RESULT_MAX
from app.tools.ask_pdf import ANSWER_MAX, NOT_FOUND
from tests.fakes import TEST_FILE_URL, run_tool, tool_ctx_fixture  # noqa: F401 - fixture


def pdf_of(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def add_pdf(ctx, pages=30, data=None, mime="application/pdf"):
    return ctx.save_file(TEST_FILE_URL, data or pdf_of(pages), mime)


def ask(ctx, **args):
    return asyncio.run(run_tool(ctx, "ask_pdf", **{"question": "Which cases?", **args}))


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


class ProviderError(Exception):
    status_code = 400


def test_sends_only_the_asked_pages_with_question_and_context(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx)
    adk_model.script = ["Testified in Doe v. Example Co. (page 22)."]
    reply = ask(tool_ctx, file_id=file_id, page_start=21, page_end=25, context="SYNTHETIC pages 1-20 had none")
    assert reply == {"answer": "Testified in Doe v. Example Co. (page 22).", "pages": "21-25", "page_count": 30}
    text, attached = adk_model.seen[0].contents[-1].parts
    assert "Question: Which cases?" in text.text and "SYNTHETIC pages 1-20 had none" in text.text
    assert "pages 21-25 of a 30-page file" in text.text
    assert attached.inline_data.mime_type == "application/pdf"
    assert len(PdfReader(io.BytesIO(attached.inline_data.data)).pages) == 5
    assert rows(tool_ctx) == [("ask_pdf", "done", "pages 21-25 of 30")]
    assert tool_ctx.findings() == []  # nothing saved


def test_bad_calls_come_back_as_errors_the_agent_can_fix(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx)
    for start, end in ((0, 5), (5, 4), (25, 31)):
        reply = ask(tool_ctx, file_id=file_id, page_start=start, page_end=end)
        assert reply == {"error": f"bad page range {start}-{end}: the file has 30 pages, so use "
                                  "1 <= page_start <= page_end <= 30"}
    assert ask(tool_ctx, file_id="file_nope", page_start=1, page_end=2)["error"].startswith("unknown file")
    page = add_pdf(tool_ctx, data=b"<html>SYNTHETIC</html>", mime="text/html")
    assert ask(tool_ctx, file_id=page, page_start=1, page_end=2) == {
        "error": "not a PDF (text/html): this tool reads PDFs only"}
    broken = add_pdf(tool_ctx, data=b"SYNTHETIC not a pdf")
    assert ask(tool_ctx, file_id=broken, page_start=1, page_end=2) == {
        "error": "the PDF cannot be read (damaged or password protected)"}
    assert adk_model.seen == []  # the model is never called for a bad call
    assert {status for _, status, _ in rows(tool_ctx)} == {"refused"}


def test_not_in_pages_and_long_answers_cut(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx, pages=3)
    adk_model.script = [NOT_FOUND + "."]
    assert ask(tool_ctx, file_id=file_id, page_start=1, page_end=3)["answer"] is None
    adk_model.script = ["y" * 9_000]
    reply = ask(tool_ctx, file_id=file_id, page_start=1, page_end=3)
    assert len(reply["answer"]) == ANSWER_MAX and len(json.dumps(reply)) < RESULT_MAX


def test_model_failure_tells_the_agent_to_try_fewer_pages(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx)
    adk_model.script = [ProviderError("SYNTHETIC provider text 12 Elm St")]
    assert ask(tool_ctx, file_id=file_id, page_start=1, page_end=30) == {
        "error": "model call failed (ProviderError, HTTP 400). If the page range is big, try fewer pages."}
    assert rows(tool_ctx)[-1][1] == "refused"
    adk_model.script = [RuntimeError("SYNTHETIC login expired")]  # fewer pages would not help
    assert ask(tool_ctx, file_id=file_id, page_start=1, page_end=30) == {"error": "model call failed (RuntimeError)."}
