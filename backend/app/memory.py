"""Long-term memories of the main agent (DESIGN 4B.4): site manuals, sites per state, blocked sites,
and the files saved for a dossier. Tables are created by `store.init_db()`. Every text write runs
`no_secret` first (gotcha 45): a write that looks like a secret raises, so the tool call fails."""

import re
from typing import Literal

from app import store

HOW_TO_MAX = 2_000
LOGIN_NOTES_MAX = 500
NOTE_MAX = 3_000
TEXT_MAX = 500  # state site `what` and `notes`
Reliability = Literal["high", "medium", "low", "unknown"]
BlockReason = Literal["paywall", "captcha", "login", "terms"]
SECRET_REFUSED = "text looks like a secret (password, token, key or card number); not stored"
# Labelled secrets ("password: x", "token=x", "api key: x") and bearer tokens.
SECRET = re.compile(
    r"(?i)\b(pass(word|wd|code)?|pwd|pin|token|secret|api[ _-]?key|access[ _-]?key|client[ _-]?secret)"
    r"\s*[:=]|\bbearer\s+\S"
)
LONG_NUMBER = re.compile(r"\d(?: ?\d){11,}")  # 12+ digits in a row (card numbers), spaces allowed
DASHED_NUMBER = re.compile(r"\d(?:[ -]?\d){11,18}")  # 4111-1111-1111-1111: a card only if Luhn-valid


def luhn(digits: str) -> bool:
    """The card number checksum (ORCID-style dashed ids usually fail it)."""
    total = sum(int(d) if i % 2 == 0 else (2 * int(d)) // 10 + (2 * int(d)) % 10
                for i, d in enumerate(reversed(digits)))
    return total % 10 == 0


def looks_secret(text: str | None, url: bool = False) -> bool:
    """True if `text` looks like a password, token, API key or card number. A URL is checked for
    labelled secrets only (long numeric ids are common in links)."""
    if not text:
        return False
    if SECRET.search(text):
        return True
    if url:
        return False
    return bool(LONG_NUMBER.search(text)) or any(
        luhn(re.sub(r"\D", "", m.group())) for m in DASHED_NUMBER.finditer(text))


def no_secret(text: str | None, url: bool = False) -> str:
    """`text` as is, or ValueError if it looks like a secret (see looks_secret)."""
    if looks_secret(text, url):
        raise ValueError(SECRET_REFUSED)
    return text or ""


def domain_of(domain: str) -> str:
    """Stored form of a domain: lower-case, no `www.`, no trailing dot."""
    domain = domain.strip().lower().rstrip(".")
    return domain.removeprefix("www.")


def _cut(text: str | None, limit: int) -> str:
    return no_secret(text)[:limit]


# Site manuals (memory 2): how to get data from a site. Never a credential.


def manual(domain: str) -> dict | None:
    rows = store.execute("select * from site_manuals where domain = ?", (domain_of(domain),))
    return dict(rows[0]) if rows else None


def save_manual(domain: str, how_to: str, login_notes: str = "", job_id: str | None = None) -> None:
    store.execute(
        "insert or replace into site_manuals (domain, how_to, login_notes, updated_at, job_id)"
        " values (?, ?, ?, ?, ?)",
        (domain_of(domain), _cut(how_to, HOW_TO_MAX), _cut(login_notes, LOGIN_NOTES_MAX), store.now(),
         job_id),
    )


# Sites per state (memory 3)


def sites_in(state: str) -> list[dict]:
    rows = store.execute("select * from state_sites where state = ? order by domain", (state.upper(),))
    return [dict(r) for r in rows]


def save_state_site(state: str, domain: str, what: str, reliability: Reliability, notes: str = "") -> None:
    if len(state) != 2 or not state.isalpha():
        raise ValueError("state must be a two-letter code")
    if reliability not in ("high", "medium", "low", "unknown"):
        raise ValueError("reliability must be high, medium, low or unknown")
    store.execute(
        "insert or replace into state_sites (state, domain, what, reliability, notes, updated_at)"
        " values (?, ?, ?, ?, ?, ?)",
        (state.upper(), domain_of(domain), _cut(what, TEXT_MAX), reliability, _cut(notes, TEXT_MAX),
         store.now()),
    )


# Blocked sites (memory 4): checked by ctx.fetch_ok before every fetch. Gotcha 2 sites are in code.


def blocked(domain: str) -> str | None:
    """The reason `domain` (or a parent domain of it) is blocked, else None."""
    labels = domain_of(domain).split(".")
    parents = [".".join(labels[i:]) for i in range(len(labels) - 1)] or labels
    marks = ",".join("?" * len(parents))
    rows = store.execute(f"select reason from blocked_sites where domain in ({marks})", tuple(parents))
    return rows[0]["reason"] if rows else None


def block_site(domain: str, reason: BlockReason, url: str | None = None) -> None:
    if reason not in ("paywall", "captcha", "login", "terms"):
        raise ValueError("reason must be paywall, captcha, login or terms")
    store.execute(
        "insert or replace into blocked_sites (domain, reason, url, noted_at) values (?, ?, ?, ?)",
        (domain_of(domain), reason, no_secret(url, url=True) or None, store.now()),
    )


# Files (memory 5): metadata and our note; the bytes are on disk (written by ctx.save_file).


def add_file(file_id: str, dossier_id: str, source_url: str, path: str, text_path: str | None, mime: str,
             size: int) -> None:
    store.execute(
        "insert into files (file_id, dossier_id, source_url, path, text_path, mime, bytes, retrieved_at)"
        " values (?, ?, ?, ?, ?, ?, ?, ?)",
        (file_id, dossier_id, no_secret(source_url, url=True), path, text_path, mime, size, store.now()),
    )


def files(dossier_id: str) -> list[dict]:
    rows = store.execute("select * from files where dossier_id = ? order by retrieved_at, file_id",
                         (dossier_id,))
    return [dict(r) for r in rows]


def file(file_id: str) -> dict | None:
    rows = store.execute("select * from files where file_id = ?", (file_id,))
    return dict(rows[0]) if rows else None


def set_file_note(file_id: str, note_md: str) -> None:
    store.execute("update files set note_md = ? where file_id = ?", (_cut(note_md, NOTE_MAX), file_id))
