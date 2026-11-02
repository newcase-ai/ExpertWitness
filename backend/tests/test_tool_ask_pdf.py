"""Ask a PDF (one question about a page range of one saved PDF) with the FakeAdkModel. Synthetic
PDFs only: one line of text per page, "SYNTHETIC page N"."""

import asyncio
import io
import json

from pypdf import PdfWriter

from app import store
from app.tools import RESULT_MAX
from app.tools.ask_pdf import ANSWER_MAX, NO_TEXT, NOT_FOUND
from tests.fakes import TEST_FILE_URL, run_tool, tool_ctx_fixture  # noqa: F401 - fixture


def pdf_of(pages: int) -> bytes:
    """A PDF whose page N has the text "SYNTHETIC page N" (pypdf writes no text, so it is built by hand)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(pages))}] /Count {pages} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i in range(pages):
        stream = f"BT /F1 12 Tf 72 720 Td (SYNTHETIC page {i + 1}) Tj ET"
        objs += [f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >>"
                 f" /Contents {5 + 2 * i} 0 R >>".encode(),
                 f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode()]
    out, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    return out + f"{xref}trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{len(out)}\n%%EOF".encode()


def blank_pdf(pages: int) -> bytes:
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
    [part] = adk_model.seen[0].contents[-1].parts
    assert "Question: Which cases?" in part.text and "SYNTHETIC pages 1-20 had none" in part.text
    assert "Pages 21-25 of a 30-page file" in part.text
    assert "[Page 21]\nSYNTHETIC page 21" in part.text and "[Page 25]\nSYNTHETIC page 25" in part.text
    assert "page 20" not in part.text and "page 26" not in part.text
    assert rows(tool_ctx) == [("ask_pdf", "done", "pages 21-25 of 30")]
    assert tool_ctx.findings() == []  # nothing saved


def test_bad_calls_come_back_as_errors_the_agent_can_fix(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx)
    for start, end in ((0, 5), (5, 4), (31, 40)):
        reply = ask(tool_ctx, file_id=file_id, page_start=start, page_end=end)
        assert reply == {"error": f"bad page range {start}-{end}: the file has 30 pages, so use "
                                  "1 <= page_start <= 30 and page_start <= page_end"}
    assert ask(tool_ctx, file_id="file_nope", page_start=1, page_end=2) == {
        "error": f"unknown file: use one of this expert's file_ids: {file_id} ({TEST_FILE_URL})"}
    page = add_pdf(tool_ctx, data=b"<html>SYNTHETIC</html>", mime="text/html")
    assert ask(tool_ctx, file_id=page, page_start=1, page_end=2) == {
        "error": "not a PDF (text/html): this tool reads PDFs only"}
    broken = add_pdf(tool_ctx, data=b"SYNTHETIC not a pdf")
    assert ask(tool_ctx, file_id=broken, page_start=1, page_end=2) == {
        "error": "the PDF cannot be read (damaged or password protected)"}
    scanned = add_pdf(tool_ctx, data=blank_pdf(3))
    assert ask(tool_ctx, file_id=scanned, page_start=1, page_end=3) == {"error": NO_TEXT}
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


def test_page_end_past_the_last_page_is_clamped(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx, pages=30)
    adk_model.script = ["SYNTHETIC answer (page 30)."]
    reply = ask(tool_ctx, file_id=file_id, page_start=21, page_end=500)
    assert reply == {"answer": "SYNTHETIC answer (page 30).", "pages": "21-30", "page_count": 30}
    assert "Pages 21-30 of a 30-page file" in adk_model.seen[0].contents[-1].parts[0].text


def test_scan_is_read_from_its_saved_ocr_text(tool_ctx, adk_model):
    """A court filing without a text layer: the [Page N] OCR text saved with it (court_file)."""
    ocr = "\n".join(f"[Page {n}]\nSYNTHETIC ocr page {n}" for n in (1, 2, 3))
    file_id = tool_ctx.save_file(TEST_FILE_URL, blank_pdf(3), "application/pdf", text=ocr)
    adk_model.script = ["SYNTHETIC answer (page 2)."]
    assert ask(tool_ctx, file_id=file_id, page_start=2, page_end=9)["pages"] == "2-3"
    [part] = adk_model.seen[0].contents[-1].parts
    assert "[Page 2]\nSYNTHETIC ocr page 2\n\n[Page 3]\nSYNTHETIC ocr page 3" in part.text
    assert "page 1" not in part.text


def test_unknown_file_lists_court_file_titles(tool_ctx, adk_model):
    file_id = add_pdf(tool_ctx)
    tool_ctx.add_finding("court_docket", "SYNTHETIC Doe v. Example", "https://www.courtlistener.com/docket/1/x/",
                         facts={"files": [{"file_id": file_id, "title": "SYNTHETIC Expert report of Alex Rivera"}]})
    reply = ask(tool_ctx, file_id="file_made_up", page_start=1, page_end=2)
    assert reply == {"error": f"unknown file: use one of this expert's file_ids: {file_id} "
                              "(SYNTHETIC Expert report of Alex Rivera)"}
