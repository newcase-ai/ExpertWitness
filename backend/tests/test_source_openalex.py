"""Source client app/sources/openalex.py: author search, author stats and works.
SYNTHETIC data only (gotcha 8)."""

import asyncio

import httpx
import pytest

from app.sources import SourceSkipped, openalex
from tests.fakes import NAME, sources_fixture  # noqa: F401 - fixture


def test_openalex_filters_by_given_and_family_name(sources):
    _, seen = sources()
    hits = asyncio.run(openalex.search_authors(NAME))
    assert [h["openalex_id"] for h in hits] == ["A0000000001", "A0000000002", "A0000000003"]
    first = hits[0]
    assert first["url"] == "https://api.openalex.org/authors/A0000000001"
    assert first["orcid"] == "0000-0000-0000-0001"
    assert first["affiliations"] == ["Example State University"]
    assert seen[0].url.params["search"] == NAME


def test_openalex_surname_only_matches_nothing(sources):
    sources()
    assert asyncio.run(openalex.search_authors("Rivera")) == []


def test_openalex_low_budget_skips_next_call(sources, monkeypatch):
    monkeypatch.setattr(openalex, "_budget", None)
    low = lambda r: httpx.Response(200, json={"results": []}, headers={"x-ratelimit-remaining-usd": "0.001"})
    _, seen = sources({"api.openalex.org": low})
    asyncio.run(openalex.search_authors(NAME))
    with pytest.raises(SourceSkipped, match="budget"):
        asyncio.run(openalex.search_authors(NAME))
    assert len(seen) == 1


def test_openalex_http_error_raises(sources):
    sources({"api.openalex.org": (503, {})})
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(openalex.search_authors(NAME))


# --- OpenAlex author and works ------------------------------------------------------------------

AUTHOR = {
    "id": "https://openalex.org/A0000000001", "display_name": "Alex J. Rivera",
    "orcid": "https://orcid.org/0000-0000-0000-0001", "works_count": 92, "cited_by_count": 1200,
    "summary_stats": {"h_index": 18}, "last_known_institutions": [{"display_name": "Example State University"}],
}
WORKS = {"results": [
    {"id": "https://openalex.org/W1", "title": "Synthetic toxicology study", "publication_year": 2021,
     "primary_location": {"source": {"display_name": "Journal of Examples"}},
     "doi": "https://doi.org/10.5555/SYNTH.1", "is_retracted": True, "cited_by_count": 4},
    {"id": "https://openalex.org/W2", "display_name": "Untitled synthetic work", "publication_year": None,
     "primary_location": None, "doi": None, "is_retracted": None},
]}


def test_openalex_author_stats(sources, monkeypatch):
    monkeypatch.setattr(openalex, "_budget", None)
    _, seen = sources(routes={"api.openalex.org": (200, AUTHOR)})
    assert asyncio.run(openalex.author("https://openalex.org/A0000000001")) == {
        "id": "A0000000001", "url": "https://openalex.org/A0000000001", "display_name": "Alex J. Rivera",
        "works_count": 92, "cited_by_count": 1200, "h_index": 18, "orcid": "0000-0000-0000-0001",
        "last_institution": "Example State University"}
    assert seen[0].url.path == "/authors/A0000000001"


def test_openalex_works_first_page_newest_first(sources, monkeypatch):
    monkeypatch.setattr(openalex, "_budget", None)
    _, seen = sources(routes={"api.openalex.org": (200, WORKS)})
    works = asyncio.run(openalex.works("A0000000001", limit=50))
    assert seen[0].url.params["filter"] == "author.id:A0000000001"
    assert seen[0].url.params["sort"] == "publication_year:desc"
    assert seen[0].url.params["per_page"] == "50" and "cursor" not in seen[0].url.params
    assert works[0] == {"id": "W1", "title": "Synthetic toxicology study", "year": 2021,
                        "venue": "Journal of Examples", "doi": "10.5555/synth.1",
                        "url": "https://doi.org/10.5555/synth.1", "is_retracted": True, "cited_by_count": 4}
    assert works[1]["doi"] is None and works[1]["url"] == "https://openalex.org/W2"
    assert works[1]["title"] == "Untitled synthetic work" and works[1]["is_retracted"] is False


def test_openalex_bad_id_and_budget(sources, monkeypatch):
    _, seen = sources()
    with pytest.raises(ValueError):
        asyncio.run(openalex.works("A1,author.id:A2"))
    monkeypatch.setattr(openalex, "_budget", (openalex._today(), 0.0))
    with pytest.raises(SourceSkipped):
        asyncio.run(openalex.author("A0000000001"))
    assert seen == []
