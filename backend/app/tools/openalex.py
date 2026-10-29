"""Tool 9: publications (OpenAlex; DESIGN 4B.9). Gotchas 6 and 12: first pages only and the keyless
budget stay in app/sources/openalex.py; one person is often split over several author ids, so the
first MAX_OPENALEX_IDS are read and their works merged."""

from app.sources import SourceSkipped, openalex
from app.tools import TITLE_MAX, Ctx, tool

MAX_OPENALEX_IDS = 3
MAX_WORKS = 100  # per author id (first page)
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


@tool(label="Publications", cap=1)
async def publications(ctx: Ctx) -> dict:
    """Read the expert's publication record from OpenAlex: author records (works count, citations,
    h-index, ORCID, last institution) and works (title, year, venue, DOI). Several OpenAlex author
    ids of the same person are merged. Use it once; call retraction_check after it. Takes no
    arguments. The reply lists the 10 newest works."""
    ids = ctx.expert.identifiers.openalex[:MAX_OPENALEX_IDS]
    if not ids:
        raise SourceSkipped("no OpenAlex id")
    authors, works, errors = [], {}, []
    for aid in ids:
        try:
            author = await openalex.author(aid)
            ref = ctx.add_finding(
                "author_stats", f"OpenAlex author record: {author.get('display_name') or aid}",
                author.get("url") or author["id"], facts={k: author.get(k) for k in AUTHOR_FACTS},
                confidence="high", source_type="openalex_author")
            authors.append({"ref": ref, **{k: author.get(k) for k in
                                           ("id", "works_count", "cited_by_count", "h_index", "orcid")}})
        except Exception as e:  # noqa: BLE001 - one failed id is a gap, the others still run
            errors.append(e)
        try:
            for work in await openalex.works(aid, limit=MAX_WORKS):
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
