"""PDF text app/pdf_text.py: text layer, scans, batching. Tiny SYNTHETIC PDFs built by hand; the OCR
engine is faked (the real model never runs in tests, gotcha 9)."""

import io
import time

import pypdf
import pytest

from app import pdf_text


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF: one Helvetica line (Tj) per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def write(tmp_path, body: bytes, name="file.pdf"):
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_page_texts_one_string_per_page(tmp_path):
    path = write(tmp_path, make_pdf(["SYNTHETIC expert report of Jordan Avery", "", "Page (three)"]))
    pages = pdf_text.page_texts(path)
    assert [p.strip() for p in pages] == ["SYNTHETIC expert report of Jordan Avery", "", "Page (three)"]


def test_page_texts_caps_each_page_and_survives_a_bad_page(tmp_path, monkeypatch):
    path = write(tmp_path, make_pdf(["A" * 50, "second page text", "third page text"]))
    monkeypatch.setattr(pdf_text, "PAGE_CHARS", 10)
    real = pypdf.PageObject.extract_text
    calls = []

    def flaky(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("broken content stream")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pypdf.PageObject, "extract_text", flaky)
    pages = pdf_text.page_texts(path)
    assert pages[0] == "A" * 10 and pages[1] == "" and pages[2].startswith("third page")


@pytest.mark.parametrize("body", [b"", b"hello, not a pdf", b"%PDF-1.4\nnot really a pdf at all",
                                  make_pdf(["SYNTHETIC page"])[:200]])
def test_not_a_pdf_or_damaged_is_unreadable(tmp_path, body):
    with pytest.raises(pdf_text.PdfUnreadable, match="cannot be read as a PDF"):
        pdf_text.page_texts(write(tmp_path, body))


def encrypted(password: str) -> bytes:
    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(make_pdf(["SYNTHETIC sealed page"]))))
    writer.encrypt(user_password=password, owner_password="owner-secret", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_encrypted_empty_password_reads_other_password_is_unreadable(tmp_path):
    assert pdf_text.page_texts(write(tmp_path, encrypted(""), "open.pdf"))[0].strip() == "SYNTHETIC sealed page"
    with pytest.raises(pdf_text.PdfUnreadable):
        pdf_text.page_texts(write(tmp_path, encrypted("secret"), "locked.pdf"))


def test_mostly_empty():
    text = "SYNTHETIC words on this page, well over twenty characters"
    assert pdf_text.mostly_empty([])
    assert pdf_text.mostly_empty(["", "  \n ", text])
    assert pdf_text.mostly_empty(["short page 19 chars", text]) is False  # half empty is not "most"
    assert pdf_text.mostly_empty(["a b c d e f g h i j k l m n o p q r s", "", text])  # 19 non-space
    assert not pdf_text.mostly_empty([text, text, ""])


def test_split_text_form_feeds_or_chunks():
    assert pdf_text.split_text("page one\fpage two\f") == ["page one", "page two"]
    assert pdf_text.split_text("") == [] and pdf_text.split_text("  \n") == []
    words = " ".join(f"word{n}" for n in range(2000))  # ~12,000 characters, no form feed
    chunks = pdf_text.split_text(words)
    assert all(len(c) <= pdf_text.CHUNK_CHARS for c in chunks) and len(chunks) >= 4
    assert " ".join(chunks) == words  # cut at spaces only
    assert pdf_text.split_text("x" * 7000) == ["x" * 3000, "x" * 3000, "x" * 1000]


class FakeOcr:
    def __init__(self, fail_on=()):
        self.calls, self.fail_on = 0, fail_on

    def __call__(self, image):
        self.calls += 1
        if self.calls in self.fail_on:
            raise RuntimeError("bad page image")
        assert image.size[0] > 1000  # rendered at OCR_SCALE (612 pt wide page)
        return type("Result", (), {"txts": (f"SYNTHETIC OCR line {self.calls}", "second line")})()


def test_ocr_pages_reads_every_page_with_one_engine(tmp_path, monkeypatch):
    fake = FakeOcr(fail_on=(2,))
    monkeypatch.setattr(pdf_text, "_engine", fake)  # never the real model in tests
    path = write(tmp_path, make_pdf(["", "", ""]))
    texts, finished = pdf_text.ocr_pages(path)
    assert finished and texts == ["SYNTHETIC OCR line 1\nsecond line", "", "SYNTHETIC OCR line 3\nsecond line"]
    assert pdf_text.ocr_engine() is fake


def test_ocr_pages_stops_at_the_time_limit(tmp_path, monkeypatch):
    fake = FakeOcr()
    monkeypatch.setattr(pdf_text, "_engine", fake)
    path = write(tmp_path, make_pdf(["", ""]))
    assert pdf_text.ocr_pages(path, stop_at=time.monotonic() - 1) == ([], False)
    assert fake.calls == 0


def test_ocr_pages_unreadable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_text, "_engine", FakeOcr())
    with pytest.raises(pdf_text.PdfUnreadable):
        pdf_text.ocr_pages(write(tmp_path, b"hello, not a pdf"))


def test_ocr_scale_caps_huge_pages():
    assert pdf_text.ocr_scale(612, 792) == pdf_text.OCR_SCALE  # letter page: full scale
    scale = pdf_text.ocr_scale(14_400, 14_400)  # 200 x 200 inch page
    assert scale < 1 and (14_400 * scale) ** 2 <= pdf_text.MAX_PIXELS * 1.0001
    assert pdf_text.ocr_scale(0, 0) == pdf_text.OCR_SCALE


def test_page_texts_reads_from_an_open_file(tmp_path, monkeypatch):
    seen = []
    real = pdf_text.PdfReader

    def spy(stream, **kwargs):
        seen.append(stream)
        return real(stream, **kwargs)

    monkeypatch.setattr(pdf_text, "PdfReader", spy)
    assert pdf_text.page_texts(write(tmp_path, make_pdf(["SYNTHETIC page"])))[0].strip() == "SYNTHETIC page"
    assert hasattr(seen[0], "read") and seen[0].closed  # a file object, closed afterwards
