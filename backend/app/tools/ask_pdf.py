"""Ask a PDF (DESIGN tool 4): one question about a page range of one saved PDF. The pages are cut out
with pypdf and sent to the model with the question and the main agent's context, so a PDF too big
for one model call is read one range at a time. Nothing is saved: the answer goes to the main agent
only. Every problem comes back as an error the main agent can act on."""

import io

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from app import llm, memory
from app.evidence import refuse
from app.tools import Ctx, tool

ANSWER_MAX = 2_000
NOT_FOUND = "NOT IN PAGES"

INSTRUCTIONS = f"""You are handed some pages of a PDF about an expert witness, plus a question and
context from the researcher who sent them. Answer the question from these pages only, using the
context to know what matters. Never guess and never use outside knowledge. Professional records
only: no home addresses, family or health details. Reply in your own words, at most 5 sentences,
naming the file's page numbers (the message says which page of the file the attachment starts at).
If these pages do not answer the question, reply exactly: {NOT_FOUND}"""


def cut(path: str, page_start: int, page_end: int) -> tuple[bytes | None, int]:
    """(pages page_start..page_end as a new PDF, the file's page count). No PDF when the range is
    outside the file."""
    reader = PdfReader(path)
    count = len(reader.pages)
    if not 1 <= page_start <= page_end <= count:
        return None, count
    writer = PdfWriter()
    for page in reader.pages[page_start - 1:page_end]:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), count


@tool(label="Ask a file", cap=10)
async def ask_pdf(ctx: Ctx, file_id: str, page_start: int, page_end: int, question: str,
                  context: str = "") -> dict:
    """Ask one question about pages page_start to page_end (1-based, both included) of one saved
    PDF (file_id from download_file). Think of this tool as a person who is handed only those pages,
    your context and your question, and nothing else: they read the pages and answer from them. So
    give good context: who the expert is, what you are looking for and why, and what you already
    found or ruled out (e.g. "pages 1-100 had no list of prior cases"). Keep the range to about
    100 pages: a bigger range can go over the model's token limit and fail. To search a long file, call
    again with the next range. `answer` is null when these pages do not answer; `page_count` is the
    file's number of pages. An `error` says what to fix before calling again."""
    row = memory.file(file_id)
    if not row or row["dossier_id"] != ctx.dossier_id:
        return refuse("unknown file: use a file_id from download_file")
    if row["mime"] != "application/pdf":
        return refuse(f"not a PDF ({row['mime']}): this tool reads PDFs only")
    try:
        pdf, count = cut(row["path"], page_start, page_end)
    except PyPdfError:
        return refuse("the PDF cannot be read (damaged or password protected)")
    if pdf is None:
        return refuse(f"bad page range {page_start}-{page_end}: the file has {count} pages, so use "
                      f"1 <= page_start <= page_end <= {count}")
    prompt = (f"Question: {question}\n\nContext from the researcher: {context or 'none'}\n\n"
              f"The attached PDF is pages {page_start}-{page_end} of a {count}-page file: its first "
              f"page is page {page_start} of the file.")
    try:
        answer = (await llm.call_model(INSTRUCTIONS, prompt, pdf=pdf)).text.strip()
    except llm.ModelError as e:  # fixed text, never the provider's
        too_big = "HTTP 400" in str(e) or "timed out" in str(e)  # token limit or slow: fewer pages may work
        return refuse(f"{e}." + (" If the page range is big, try fewer pages." if too_big else ""))
    ctx.log(f"pages {page_start}-{page_end} of {count}")
    return {"answer": None if not answer or answer.startswith(NOT_FOUND) else answer[:ANSWER_MAX],
            "pages": f"{page_start}-{page_end}", "page_count": count}
