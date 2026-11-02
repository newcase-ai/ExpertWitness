"""Text of a downloaded PDF, page by page, for the court-file check (app/tools/court_files.py):
pypdf text layer first; scanned files get CourtListener's OCR text or local OCR (RapidOCR on pages
rendered by pypdfium2). No network: RapidOCR's models ship inside its wheel."""

import math
import threading
import time
from pathlib import Path

from pypdf import PdfReader

PAGE_CHARS = 20_000  # characters kept per page (bloated or broken PDFs can spew garbage)
BATCH_PAGES = 128
BATCH_CHARS = 120_000  # ponytail: a guess at the model's input limit; tune after real runs
MIN_PAGE_CHARS = 20  # a page with fewer non-space characters counts as empty (a scan)
CHUNK_CHARS = 3_000  # pseudo-page size for text without form feeds
OCR_SCALE = 2.0  # render at 144 dpi: small print is still readable for OCR
MAX_PIXELS = 25_000_000  # a page with a huge page size is rendered smaller, not as a giant image
UNREADABLE = "The file cannot be read as a PDF (not a PDF, damaged or password protected)"


class PdfUnreadable(ValueError):
    """The file cannot be opened as a PDF (not a PDF, damaged, password protected). Fixed text."""


def page_texts(path: str | Path) -> list[str]:
    """The text layer of every page (one string per page, "" for a page without text or that fails).
    Raises PdfUnreadable when the file cannot be opened at all."""
    with Path(path).open("rb") as f:  # pypdf reads from the open file: big PDFs stay on disk
        try:
            if f.read(5) != b"%PDF-":
                raise PdfUnreadable(UNREADABLE)
            f.seek(0)
            reader = PdfReader(f, strict=False)
            if reader.is_encrypted and not reader.decrypt(""):  # many court PDFs use an empty password
                raise PdfUnreadable(UNREADABLE)
            count = len(reader.pages)
        except PdfUnreadable:
            raise
        except Exception:  # noqa: BLE001 - pypdf raises many types on a damaged file; all mean "unreadable"
            raise PdfUnreadable(UNREADABLE) from None
        out = []
        for i in range(count):
            try:
                out.append((reader.pages[i].extract_text() or "")[:PAGE_CHARS])
            except Exception:  # noqa: BLE001 - one bad page must not lose the rest of the file
                out.append("")
    return out


def mostly_empty(pages: list[str]) -> bool:
    """True when the file has no pages or most pages have (almost) no text: a scan."""
    empty = sum(len("".join(p.split())) < MIN_PAGE_CHARS for p in pages)
    return not pages or empty * 2 > len(pages)


def split_text(text: str) -> list[str]:
    """CourtListener's plain_text as pseudo-pages: split on form feeds, else ~3,000-character chunks
    cut at whitespace. "" gives []."""
    if "\f" in text:
        pages = [p[:PAGE_CHARS] for p in text.split("\f")]
        return pages[:-1] if pages and not pages[-1].strip() else pages  # text often ends with "\f"
    out, rest = [], text.strip()
    while rest:
        cut = len(rest) if len(rest) <= CHUNK_CHARS else max(rest.rfind(c, 0, CHUNK_CHARS) for c in " \n\t")
        cut = cut if cut > 0 else CHUNK_CHARS  # one long word: cut it
        out.append(rest[:cut])
        rest = rest[cut:].lstrip()
    return out


_engine = None
_engine_lock = threading.Lock()


def ocr_engine():
    """The RapidOCR engine, created once (loading the models takes a few seconds)."""
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR  # heavy import, only when a scan needs OCR

            _engine = RapidOCR(params={"Global.log_level": "error"})
    return _engine


def ocr_scale(width: float, height: float) -> float:
    """OCR_SCALE, lowered so the rendered page stays within MAX_PIXELS (sizes in PDF points)."""
    area = width * height
    return min(OCR_SCALE, math.sqrt(MAX_PIXELS / area)) if area > 0 else OCR_SCALE


def ocr_pages(path: str | Path, stop_at: float | None = None) -> tuple[list[str], bool]:
    """(OCR text per page, finished). Stops early when time.monotonic() passes `stop_at`. Blocking: run
    it in a thread. Raises PdfUnreadable when pdfium cannot open the file."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError:
        raise PdfUnreadable(UNREADABLE) from None
    texts = []
    try:
        for i in range(len(pdf)):
            if stop_at is not None and time.monotonic() > stop_at:
                return texts, False
            try:
                page = pdf[i]
                image = page.render(scale=ocr_scale(*page.get_size())).to_pil()
                result = ocr_engine()(image)
                texts.append("\n".join(result.txts or ())[:PAGE_CHARS])
            except Exception:  # noqa: BLE001 - one bad page must not lose the rest of the file
                texts.append("")
        return texts, True
    finally:
        pdf.close()


def batches(pages: list[str], max_pages: int = BATCH_PAGES, max_chars: int = BATCH_CHARS) -> list[tuple[int, int, str]]:
    """(first page, last page, text with a "[Page N]" line before each page), 1-based, in order.
    A batch holds at most `max_pages` pages and `max_chars` characters; a page longer than that on
    its own is cut to `max_chars`."""
    out, parts, first, size = [], [], 1, 0
    for n, page in enumerate(pages, 1):
        part = f"[Page {n}]\n{page}"[:max_chars]
        if parts and (len(parts) == max_pages or size + 1 + len(part) > max_chars):
            out.append((first, n - 1, "\n".join(parts)))
            parts, size = [], 0
        if not parts:
            first, size = n, len(part)
        else:
            size += 1 + len(part)
        parts.append(part)
    if parts:
        out.append((first, len(pages), "\n".join(parts)))
    return out
