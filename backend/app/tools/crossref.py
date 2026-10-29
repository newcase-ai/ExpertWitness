"""Tool 10: retraction check (Crossref + OpenAlex's flag; DESIGN 4B.9). Gotcha 7: `mailto`, one request
at a time and the 429 backoff stay in app/sources/crossref.py."""

from app.evidence import refuse
from app.sources import SourceSkipped, crossref
from app.tools import TITLE_MAX, Ctx, tool

MAX_CROSSREF_DOIS = 50  # newest first; older works rely on OpenAlex's flag
NO_WORKS = "no works yet: call publications first"
REASONS_GAP = "Retraction reasons are not collected (the Retraction Watch CSV is not used)."
UNCHECKED_GAP = ("{} works not checked in Crossref (no DOI or beyond the newest {}); only OpenAlex's flag"
                 " was read")
OPENALEX_NOTE = "flagged as retracted by OpenAlex; no Crossref notice found"


@tool(label="Retraction check", cap=2)
async def retraction_check(ctx: Ctx) -> dict:
    """Check the expert's saved works for retractions: Crossref retraction notices for the 50 newest
    works with a DOI, plus OpenAlex's retracted flag for every work. Call publications first.
    Takes no arguments."""
    works = ctx.findings(kind="work")
    if not works:
        return refuse(NO_WORKS)
    ctx.add_gap(REASONS_GAP)
    newest = sorted(works, key=lambda w: w.facts.get("year") or 0, reverse=True)
    to_check = [w for w in newest if w.facts.get("doi")][:MAX_CROSSREF_DOIS]
    if unchecked := len(works) - len(to_check):
        ctx.add_gap(UNCHECKED_GAP.format(unchecked, MAX_CROSSREF_DOIS))
    found, flagged, checked, failed, error = [], set(), 0, 0, None
    for work in to_check:
        try:
            hit = await crossref.retraction(work.facts["doi"])
        except SourceSkipped as e:
            error = e
            break
        except Exception as e:  # noqa: BLE001 - one failed DOI is counted, the rest still run
            failed, error = failed + 1, e
            continue
        checked += 1
        if hit:
            flagged.add(work.finding_id)
            doi = hit.get("doi") or work.facts["doi"]
            ref = ctx.add_finding(
                "retraction", work.title, hit.get("notice_url") or hit.get("url") or work.url,
                facts={"doi": doi, "date": hit.get("date"), "source": hit.get("source"),
                       "notice_url": hit.get("notice_url"),
                       "openalex_is_retracted": bool(work.facts.get("is_retracted"))},
                confidence="high", source_type="crossref_work")
            found.append({"ref": ref, "title": work.title[:TITLE_MAX], "doi": doi, "found_by": "crossref"})
    for work in newest:
        if work.facts.get("is_retracted") and work.finding_id not in flagged:
            ref = ctx.add_finding("retraction", work.title, work.url,
                                  facts={"doi": work.facts.get("doi"), "flagged_by": "openalex"},
                                  confidence="high", note=OPENALEX_NOTE, source_type="openalex_work")
            found.append({"ref": ref, "title": work.title[:TITLE_MAX], "doi": work.facts.get("doi"),
                          "found_by": "openalex"})
    if to_check and not checked:  # Crossref not usable at all: the wrapper makes the gap
        raise error
    if isinstance(error, SourceSkipped):
        ctx.add_gap(str(error))
    if failed:
        ctx.add_gap(f"Crossref check failed for {failed} DOIs")
    ctx.log(f"{checked} DOIs checked in Crossref, {len(found)} retractions")
    return {"retractions": found, "checked_in_crossref": checked, "works": len(works)}
