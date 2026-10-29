"""OpenAlex author search (DESIGN 4.1), author stats and works (DESIGN 4.3); gotchas 6 and 12."""

import re
from datetime import UTC, datetime

from app import http
from app.sources import SourceSkipped, given_family, name_tokens

API = "https://api.openalex.org"
MIN_BUDGET_USD = 0.005
_budget: tuple[str, float] | None = None  # (UTC date, last x-ratelimit-remaining-usd seen)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def short_id(openalex_url: str) -> str:
    return openalex_url.rstrip("/").rsplit("/", 1)[-1]


def name_matches(query: str, author: dict) -> bool:
    """Keep an author only if one of its names has both the given and the family name (gotcha 13)."""
    given, family = given_family(query)
    if not given or given == family:
        return False
    names = [author.get("display_name") or ""] + (author.get("display_name_alternatives") or [])
    return any({given, family} <= set(name_tokens(n)) for n in names)


def to_hit(author: dict) -> dict:
    """An author record as a candidate-finder hit. Only fields we need, no free text."""
    aid = short_id(author["id"])
    orcid = author.get("orcid")
    institutions = [i["display_name"] for i in author.get("last_known_institutions") or [] if i.get("display_name")]
    topics = [t["display_name"] for t in author.get("topics") or [] if t.get("display_name")]
    note = f"{author.get('works_count', 0)} works"
    if institutions:
        note += f", last institution {institutions[0]}"
    return {
        "source": "openalex",
        "url": f"{API}/authors/{aid}",
        "title": "OpenAlex author record",
        "note": note,
        "name": author.get("display_name") or "",
        "openalex_id": aid,
        "orcid": short_id(orcid) if orcid else None,
        "affiliations": institutions,
        "field": topics[0] if topics else None,
        "topics": topics[:3],
    }


async def get_json(path: str, params: dict | None = None) -> dict:
    """One OpenAlex read; skipped when today's keyless budget is nearly used up (gotcha 12)."""
    global _budget
    if _budget and _budget[0] == _today() and _budget[1] < MIN_BUDGET_USD:
        raise SourceSkipped(f"OpenAlex daily budget nearly used up (${_budget[1]:.4f} left)")
    r = await http.fetch("openalex", f"{API}/{path}", params=params)
    if (left := r.headers.get("x-ratelimit-remaining-usd")) is not None:
        _budget = (_today(), float(left))
    if r.status_code != 200:
        raise RuntimeError(f"OpenAlex HTTP {r.status_code}")
    return r.json()


async def search_authors(name: str) -> list[dict]:
    """First page of OpenAlex author search, filtered to real name matches."""
    body = await get_json("authors", {"search": name, "per_page": 25})
    return [to_hit(a) for a in body.get("results") or [] if name_matches(name, a)]


# Publications collector (DESIGN 4.3): author stats and works by OpenAlex author id.


def author_key(author_id: str) -> str:
    """"A123" or "https://openalex.org/A123" -> "A123"; anything else is refused (it goes into a filter)."""
    aid = short_id(author_id.strip()).upper()
    if not re.fullmatch(r"A\d+", aid):
        raise ValueError(f"not an OpenAlex author id: {author_id!r}")
    return aid


def bare_doi(doi: object) -> str | None:
    if not isinstance(doi, str) or not doi.strip():
        return None
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.IGNORECASE).lower() or None


async def author(author_id: str) -> dict:
    a = await get_json(f"authors/{author_key(author_id)}")
    institutions = [i.get("display_name") for i in a.get("last_known_institutions") or [] if i.get("display_name")]
    orcid = a.get("orcid")
    return {
        "id": short_id(a.get("id") or author_key(author_id)),
        "url": a.get("id") or f"https://openalex.org/{author_key(author_id)}",
        "display_name": a.get("display_name"),
        "works_count": a.get("works_count"),
        "cited_by_count": a.get("cited_by_count"),
        "h_index": (a.get("summary_stats") or {}).get("h_index"),
        "orcid": short_id(orcid) if orcid else None,
        "last_institution": institutions[0] if institutions else None,
    }


def to_work(w: dict) -> dict:
    doi = bare_doi(w.get("doi"))
    source = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "id": short_id(w.get("id") or ""),
        "title": w.get("title") or w.get("display_name"),
        "year": w.get("publication_year"),
        "venue": source.get("display_name"),
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else w.get("id"),
        "is_retracted": bool(w.get("is_retracted")),
        "cited_by_count": w.get("cited_by_count"),
    }


async def works(author_id: str, limit: int = 100) -> list[dict]:
    """Newest works first; first page only (keyless OpenAlex allows no cursor paging, gotcha 6)."""
    params = {"filter": f"author.id:{author_key(author_id)}", "sort": "publication_year:desc",
              "per_page": max(1, min(limit, 200))}
    body = await get_json("works", params)
    return [to_work(w) for w in (body.get("results") or [])[:limit] if isinstance(w, dict)]
