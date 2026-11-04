"""Long-term memories (DESIGN 4B.4): site manuals, sites per state, blocked sites, files; restart and
the no-secret rule (gotcha 45). Synthetic data only."""

import pytest

from app import memory, store

SECRETS = [
    "password: hunter2", "Password=abc", "pwd: x", "token=abc123", "API key: abc", "api_key=abc",
    "client secret: x", "Authorization: Bearer abc.def", "card 4111 1111 1111 1111", "acct 411111111111",
    "card 4111-1111-1111-1111", "card 5500-0000-0000-0004",
]
NOT_SECRETS = [
    "login needed: yes, at /signin", "search by case number; results load with JavaScript",
    "filed 2019-04-02, docket 1:19-cv-0001", "phone 555 123 4567", "ORCID 0000-0002-1825-0097",
]


def test_memories_survive_a_restart():
    """7d: memories are rows in the SQLite file; a new process re-creates nothing and reads them back."""
    memory.save_manual("WWW.Courts.Example.gov", "Search URL: /search?case=<number>", "login needed: no")
    memory.save_state_site("oh", "courts.example.gov", "county trial court dockets", "high")
    memory.block_site("paywall.example.com", "paywall", "https://paywall.example.com/doc/1")
    memory.add_file("file_1", "dos_1", "https://example.edu/cv.pdf", "/tmp/x.pdf", None, "application/pdf", 3)
    memory.set_file_note("file_1", "## CV\nLists two cases.")
    store._ready.clear()  # as in a new process: tables are re-checked on the same file
    store.init_db()
    manual = memory.manual("courts.example.gov")
    assert manual["how_to"] == "Search URL: /search?case=<number>" and manual["login_notes"] == "login needed: no"
    assert [(s["state"], s["domain"], s["reliability"]) for s in memory.sites_in("OH")] == [
        ("OH", "courts.example.gov", "high")]
    assert memory.blocked("paywall.example.com") == "paywall"
    assert memory.blocked("docs.paywall.example.com") == "paywall"  # a subdomain of a blocked site
    assert memory.blocked("example.com") is None
    assert [f["note_md"] for f in memory.files("dos_1")] == ["## CV\nLists two cases."]
    assert memory.file("file_1")["bytes"] == 3 and memory.file("nope") is None


def test_writes_replace_and_cut():
    memory.save_manual("example.gov", "old")
    memory.save_manual("example.gov", "x" * 3_000, "y" * 900)
    manual = memory.manual("example.gov")
    assert len(manual["how_to"]) == 2_000 and len(manual["login_notes"]) == 500
    assert memory.manual("missing.example") is None and memory.sites_in("TX") == []


@pytest.mark.parametrize("text", SECRETS)
def test_no_secret_refuses_on_every_memory_write(text):
    with pytest.raises(ValueError, match="secret"):
        memory.save_manual("example.gov", text)
    with pytest.raises(ValueError, match="secret"):
        memory.save_manual("example.gov", "how to", login_notes=text)
    with pytest.raises(ValueError, match="secret"):
        memory.save_state_site("OH", "example.gov", text, "low")
    memory.add_file("file_s", "dos_s", "https://example.edu/a.pdf", "/tmp/a.pdf", None, "application/pdf", 1)
    with pytest.raises(ValueError, match="secret"):
        memory.set_file_note("file_s", text)
    assert memory.manual("example.gov") is None and memory.sites_in("OH") == []
    assert memory.file("file_s")["note_md"] is None


def test_secret_in_a_url_is_refused_but_long_ids_are_not():
    with pytest.raises(ValueError, match="secret"):
        memory.block_site("example.gov", "login", "https://example.gov/x?token=abc")
    memory.add_file("file_n", "dos_n", "https://example.gov/doc/202310290001234.pdf", "/tmp/n.pdf", None,
                    "application/pdf", 1)


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_no_secret_lets_plain_notes_through(text):
    memory.save_manual("example.gov", text, login_notes=text)
    assert memory.manual("example.gov")["how_to"] == text


def test_fixed_values_are_checked():
    with pytest.raises(ValueError):
        memory.block_site("example.gov", "rate_limit")
    with pytest.raises(ValueError):
        memory.save_state_site("Ohio", "example.gov", "dockets", "high")
    with pytest.raises(ValueError):
        memory.save_state_site("OH", "example.gov", "dockets", "great")


# Real-format ORCID ids (valid ISO 7064 check character) whose 16 digits also pass the card checksum.
LUHN_ORCIDS = ["0000-4114-4269-4203", "0009-4104-0901-6104", "0000-3303-6148-1105", "0000-8344-9472-857X"]


@pytest.mark.parametrize("text", [*(f"ORCID {o}" for o in LUHN_ORCIDS), f"https://orcid.org/{LUHN_ORCIDS[0]}",
                                  "ISBN 9780306406157", "ISBN 979-1-0000-0000-9", "isbn:9781000000006",
                                  "doi 10.5555/s12345-019-00009-x"])
def test_no_secret_lets_orcid_ids_isbn13_and_dois_through(text):
    assert memory.no_secret(text) == text


@pytest.mark.parametrize("text", ["4111-1111-1111-1111", "4111111111111111", "card 4111 1111 1111 1111",
                                  f"ORCID {LUHN_ORCIDS[0]}, card 4111-1111-1111-1111"])
def test_no_secret_still_refuses_card_numbers(text):
    with pytest.raises(ValueError, match="secret"):
        memory.no_secret(text)


# Signed or token links (synthetic values): flagged by the parameter NAME, so every tool, finding and
# activity row hides or refuses them.
SIGNED_URLS = [
    "https://bucket.s3.amazonaws.com/cv.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE",
    "https://storage.googleapis.com/b/cv.pdf?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Signature=ab12",
    "https://acct.blob.core.windows.net/c/cv.pdf?sv=2022-11-02&sig=abc%3D",
    "https://d1.cloudfront.net/cv.pdf?Expires=1700000000&Signature=abc&Key-Pair-Id=K2EXAMPLE",
    "https://example.org/cv.pdf?access_token=abc", "https://example.org/cv.pdf?accessToken=abc",
    "https://example.org/cb#id_token=abc", "https://example.org/cv.pdf?auth=abc",
    "https://example.org/cv.pdf?sessionid=abc", "https://example.org/a;jsessionid=ABC123",
    "https://example.org/a.php?PHPSESSID=abc", "https://example.org/a?credential=abc",
]


@pytest.mark.parametrize("url", SIGNED_URLS)
def test_signed_or_token_links_look_secret(url):
    assert memory.looks_secret(url, url=True)


@pytest.mark.parametrize("url", [
    "https://example.gov/case?id=123", "https://example.gov/view?docid=88", "https://example.gov/download.aspx?file=a.pdf",
    "https://legislature.example.gov/bills?session=2023", "https://example.org/search?author=rivera",
    "https://example.org/map?key=abc", "https://example.org/a?signed=yes&designation=md",
])
def test_normal_links_do_not_look_secret(url):
    assert not memory.looks_secret(url, url=True)
