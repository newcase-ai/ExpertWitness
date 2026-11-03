"""Tool 9: publications (OpenAlex; DESIGN 4B.9). Gotchas 6 and 12: first pages only and the keyless
budget stay in app/sources/openalex.py; one person is often split over several author ids, so the
first MAX_OPENALEX_IDS are read and their works merged."""

import asyncio
import re
from pathlib import Path

from app import memory
from app.sources import SourceSkipped, openalex
from app.tools import TITLE_MAX, Ctx, strings, tool

MAX_OPENALEX_IDS = 3
MAX_WORKS = 100  # per author id (first page)
MAX_LOOKUP = 3  # author records checked by a name lookup (each costs one works call, gotcha 12)
MIN_TITLE_WORDS = 6  # shorter titles ("Editorial", "Introduction") never count as a match
MIN_TITLES = 2  # titles found in our sources needed to accept a record without an ORCID match
NO_MATCH = "no OpenAlex author matched the collected sources"
NEWEST = 10  # works in the reply
AUTHOR_FACTS = ("id", "display_name", "works_count", "cited_by_count", "h_index", "orcid", "last_institution")
WORK_FACTS = ("id", "year", "venue", "doi", "cited_by_count", "is_retracted")


def work_key(work: dict) -> str:
    return (work.get("doi") or "").lower() or work["id"]


def work_url(work: dict) -> str:
    return work.get("url") or (f"https://doi.org/{work['doi']}" if work.get("doi") else work["id"])


def failure(exc: Exception) -> str:
    """Gap text for one failed OpenAlex call: the skip reason or the exception type only."""
    return str(exc) if isinstance(exc, SourceSkipped) else f"OpenAlex failed ({type(exc).__name__})"


def squash(text: str) -> str:
    """Lowercase words only, so a title matches across line breaks, case and punctuation."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def read_texts(paths: list[str]) -> list[str]:
    return [Path(p).read_text(errors="ignore") for p in paths if Path(p).is_file()]


async def collected_text(ctx: Ctx) -> str:
    """Everything this run has collected: finding titles and facts, and saved files' text."""
    parts = [s for f in ctx.findings() for s in (f.title, *strings(f.facts))]
    paths = [f["text_path"] for f in memory.files(ctx.dossier_id) if f.get("text_path")]
    parts += await asyncio.to_thread(read_texts, paths)  # gotcha 57
    return squash(" ".join(parts))


def match_reason(hit: dict, works: list[dict], text: str) -> str | None:
    """Why this author record is the expert, or None: its ORCID, or MIN_TITLES of its (long enough)
    work titles, appear in what we collected."""
    if hit.get("orcid") and hit["orcid"] in text.replace(" ", "-"):
        return "ORCID found in collected sources"
    titles = {t for w in works if len((t := squash(w.get("title") or "")).split()) >= MIN_TITLE_WORDS}
    found = sum(t in text for t in titles)
    return f"{found} work titles found in collected sources" if found >= MIN_TITLES else None


async def look_up(ctx: Ctx) -> tuple[list[str], dict[str, list[dict]], dict[str, str]]:
    """No id on the candidate: search OpenAlex by name and keep the records our own sources confirm
    (ids, their works already read, why each matched). A name alone is never enough (gotcha 13)."""
    hits = (await openalex.search_authors(ctx.expert.display_name))[:MAX_LOOKUP]
    text = await collected_text(ctx) if hits else ""
    ids, read, why = [], {}, {}
    for hit in hits:
        works = await openalex.works(hit["openalex_id"], limit=MAX_WORKS)
        if reason := match_reason(hit, works, text):
            ids.append(hit["openalex_id"])
            read[hit["openalex_id"]], why[hit["openalex_id"]] = works, reason
    if not ids:
        raise SourceSkipped(NO_MATCH)
    return ids[:MAX_OPENALEX_IDS], read, why


@tool(label="Publications", cap=1)
async def publications(ctx: Ctx) -> dict:
    """Read the expert's publication record from OpenAlex: author records (works count, citations,
    h-index, ORCID, last institution) and works (title, year, venue, DOI). Several OpenAlex author
    ids of the same person are merged. If the expert has no OpenAlex id yet, it searches OpenAlex by
    name and keeps only a record whose ORCID or work titles appear in findings or files already
    collected, so call it after reading the expert's CV or bio. Use it once; call retraction_check
    after it. Takes no arguments. The reply lists the 10 newest works."""
    ids = ctx.expert.identifiers.openalex[:MAX_OPENALEX_IDS]
    read, why = {}, {}
    if not ids:
        ids, read, why = await look_up(ctx)
    authors, works, errors = [], {}, []
    for aid in ids:
        try:
            author = await openalex.author(aid)
            ref = ctx.add_finding(
                "author_stats", f"OpenAlex author record: {author.get('display_name') or aid}",
                author.get("url") or author["id"], facts={k: author.get(k) for k in AUTHOR_FACTS},
                confidence="high", source_type="openalex_author", note=why.get(aid))
            authors.append({"ref": ref, **{k: author.get(k) for k in
                                           ("id", "works_count", "cited_by_count", "h_index", "orcid")},
                            **({"matched_by": why[aid]} if aid in why else {})})
        except Exception as e:  # noqa: BLE001 - one failed id is a gap, the others still run
            errors.append(e)
        try:
            for work in read.get(aid) or await openalex.works(aid, limit=MAX_WORKS):
                works.setdefault(work_key(work), work)
        except Exception as e:  # noqa: BLE001 - as above
            errors.append(e)
    if len(errors) == 2 * len(ids):  # every call failed or was skipped: the wrapper makes the gap
        raise errors[0]
    for reason in dict.fromkeys(failure(e) for e in errors):
        ctx.add_gap(reason)
    saved = []
    for work in sorted(works.values(), key=lambda w: w.get("year") or 0, reverse=True):
        ref = ctx.add_finding("work", work.get("title") or "Untitled work", work_url(work),
                              facts={k: work.get(k) for k in WORK_FACTS}, confidence="high",
                              source_type="openalex_work")
        if ref:
            saved.append({"ref": ref, "title": (work.get("title") or "")[:TITLE_MAX], "year": work.get("year"),
                          "doi": work.get("doi")})
    ctx.log(f"{len(ids)} OpenAlex ids, {len(authors)} author records, {len(saved)} works")
    return {"authors": authors, "works": len(saved), "newest_works": saved[:NEWEST]}
