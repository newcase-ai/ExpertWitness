"""End to end through the API with fake sources and a fake model (no network, no real AI)."""

import time

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.models import NO_EXPERTS_NOTICE
from tests.fakes import (  # noqa: F401 - fixture
    GROUPING,
    NAME,
    WEB_NAME_RESULTS,
    WEB_RESULTS,
    FakeModel,
    sources_fixture,
)

CANDIDATE_KEYS = {"candidate_id", "display_name", "summary", "field", "affiliations", "location",
                  "identifiers", "evidence", "match_notes", "expert_signals", "expert_evidence", "expert_note"}
SEARCH_KEYS = {"search_id", "name", "status", "created_at", "candidates", "failed_sources", "error",
               "notice"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def search(client, body):
    started = client.post("/candidate-searches", json=body)
    assert started.status_code == 202
    ids = started.json()
    assert ids["status"] == "queued"
    for _ in range(300):
        job = client.get(f"/jobs/{ids['job_id']}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("job did not finish")
    result = client.get(f"/candidate-searches/{ids['search_id']}").json()
    return job, result


def test_candidate_search_flow(sources, client):
    sources()
    job, result = search(client, {"name": NAME, "hints": {"field": "toxicology"}})
    assert job["status"] == "done"
    assert job["result"]["kind"] == "candidate_search"
    assert {s["key"]: s["status"] for s in job["steps"]} == {
        "openalex": "done", "courtlistener": "done", "web_search": "done", "group_candidates": "done"}

    assert set(result) == SEARCH_KEYS
    assert result["status"] == "done" and result["error"] is None and result["failed_sources"] == []
    assert result["notice"] is None
    candidates = result["candidates"]
    assert len(candidates) == 3  # the same-name chiropractor is listed last, flagged
    assert [c["expert_signals"] for c in candidates] == [
        ["court_opinion_expert", "web_expert_witness"], ["expert_directory"], []]
    assert [c["expert_evidence"] for c in candidates] == [True, True, False]
    assert [c["expert_note"] for c in candidates] == [None, None, "No expert-witness evidence found"]
    for c in candidates:
        assert set(c) == CANDIDATE_KEYS
        assert c["candidate_id"].startswith("cand_")
        assert set(c["identifiers"]) == {"openalex", "orcid"}
        assert c["evidence"], "evidence is never empty"
        for e in c["evidence"]:
            assert set(e) == {"source", "url", "title", "note"}
            assert e["url"].startswith("https://")
    assert "SNIPPET" not in str(result)


def test_one_source_down_is_named(sources, client):
    sources({"api.openalex.org": (503, {})})
    job, result = search(client, {"name": NAME})
    assert job["status"] == "done"
    assert {s["key"]: s["status"] for s in job["steps"]}["openalex"] == "failed"
    assert result["failed_sources"] == [{"source": "openalex", "reason": "OpenAlex HTTP 503"}]
    assert result["candidates"]  # CourtListener and web hits still answer
    assert all(c["evidence"] for c in result["candidates"])


def test_non_expert_only_is_listed_flagged_without_notice(sources, client):
    clinic_only = {"people": [GROUPING["people"][2]]}
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[clinic_only]))
    _, result = search(client, {"name": NAME})
    assert [c["expert_evidence"] for c in result["candidates"]] == [False]
    assert result["notice"] is None


def test_zero_candidates_gives_empty_list_and_notice(sources, client):
    openalex_only = {"people": [GROUPING["people"][1] | {"evidence": [{"ref": "E3", "note": None}]}]}
    sources(model=FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[openalex_only]))
    job, result = search(client, {"name": NAME})
    assert job["status"] == "done"
    assert result["candidates"] == [] and result["error"] is None
    assert result["notice"] == NO_EXPERTS_NOTICE == "No expert-witness evidence found for this name."


def test_no_notice_when_every_source_failed(sources, client):
    down = llm.ModelError("model call ended with error")
    sources({"api.openalex.org": (500, {}), "www.courtlistener.com": (500, {})},
            model=FakeModel(web=[down, down]))
    job, result = search(client, {"name": NAME})
    assert job["status"] == "done" and result["candidates"] == []
    assert {f["source"] for f in result["failed_sources"]} == {"openalex", "courtlistener", "web_search"}
    assert result["notice"] is None  # failed_sources explains it
