"""OIG LEIE exclusion list (DESIGN 4.4): the monthly UPDATED.csv, matched by exact name.

A hit is only a possible match. Only the columns in KEEP leave this module: never DOB, address,
city, ZIP, NPI or UPIN."""

import asyncio
import csv
import time
from pathlib import Path

from app import http
from app.config import get_settings
from app.sources import fold

CSV_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
CSV_NAME = "leie_updated.csv"
MAX_AGE_DAYS = 35  # the file is replaced monthly
SEARCH_URL = "https://exclusions.oig.hhs.gov/"  # official Online Search, linked for the user only
KEEP = ("GENERAL", "SPECIALTY", "EXCLTYPE", "EXCLDATE", "REINDATE", "STATE")


def csv_path() -> Path:
    """`data/` next to the SQLite file (gitignored)."""
    return Path(get_settings().database_path).parent / CSV_NAME


def is_fresh(path: Path) -> bool:
    return path.is_file() and time.time() - path.stat().st_mtime < MAX_AGE_DAYS * 86400


async def download(path: Path) -> None:
    r = await http.fetch("oig_leie", CSV_URL)
    if r.status_code != 200:
        raise RuntimeError(f"OIG LEIE download HTTP {r.status_code}")
    if b"LASTNAME" not in r.content[:2000]:  # an error page must not be kept for a month
        raise RuntimeError("OIG LEIE download is not the expected CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(path)


async def ensure_csv() -> Path:
    """The local CSV, downloaded when missing or older than MAX_AGE_DAYS. If the download fails an
    older copy is used; with no copy at all the error is raised."""
    path = csv_path()
    if is_fresh(path):
        return path
    try:
        await download(path)
    except Exception:  # any download failure falls back to the older copy
        if not path.is_file():
            raise
    return path


def scan(path: Path, first: str, last: str, middle_initial: str | None) -> list[dict]:
    first, last = fold(first).strip(), fold(last).strip()
    initial = fold(middle_initial or "").strip(" .")[:1]
    matches = []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if fold(row.get("LASTNAME") or "").strip() != last or fold(row.get("FIRSTNAME") or "").strip() != first:
                continue
            mid = fold(row.get("MIDNAME") or "").strip()
            if initial and mid and not mid.startswith(initial):
                continue
            matches.append({k.lower(): (row.get(k) or "").strip() for k in KEEP} | {"url": SEARCH_URL})
    return matches


async def possible_matches(first: str, last: str, middle_initial: str | None = None) -> list[dict]:
    """LEIE rows with exactly this first and last name (case and accents ignored). With a middle
    initial, MIDNAME must be empty or start with it."""
    if not fold(first).strip() or not fold(last).strip():
        return []  # business rows have empty names; never match on one name part
    path = await ensure_csv()
    # ponytail: re-reads the ~80k-row CSV per call; cache the rows if many dossiers run at once
    return await asyncio.to_thread(scan, path, first, last, middle_initial)
