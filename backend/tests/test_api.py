import time

import pytest
from fastapi.testclient import TestClient

from app import agents
from app.main import app
from app.models import Candidate, FailedSource

PERSON = {
    "display_name": "Alex J. Rivera",
    "summary": "Toxicologist (synthetic test person).",
    "field": "toxicology",
    "affiliations": ["Example State University"],
    "location": "Ohio, US",
    "identifiers": {"openalex": ["A0000000001"], "orcid": None},
    "evidence": [
        {
            "source": "openalex",
            "url": "https://api.openalex.org/authors/A0000000001",
            "title": "OpenAlex author record",
            "note": "92 works",
        }
    ],
    "match_notes": None,
    "expert_signals": ["court_opinion_expert"],
}


async def fake_find(name, hints, on_step):
    for step in ("openalex", "web_search", "group_candidates"):
        on_step(step, "done", "1 hit")
    on_step("courtlistener", "skipped", "no token")
    other = PERSON | {"display_name": "Alex Rivera", "field": "economics"}
    failed = [FailedSource(source="courtlistener", reason="COURTLISTENER_TOKEN is not set")]
    return [Candidate(**PERSON), Candidate(**other)], failed


async def broken_find(name, hints, on_step):
    on_step("openalex", "running")
    raise RuntimeError("AI grouping step failed after 1 retry")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def wait_for_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": ""},
        {"name": "   "},
        {"name": "A"},
        {"name": "x" * 121},
        {"name": "12345"},
        {"name": "Alex Rivera", "extra": 1},
        {"name": "Alex Rivera", "hints": {"shoe_size": "9"}},
        {"name": "Alex Rivera", "hints": {"field": "x" * 201}},
    ],
)
def test_bad_input_is_422(client, body):
    assert client.post("/candidate-searches", json=body).status_code == 422


def test_candidate_search_flow(client, monkeypatch):
    seen = {}

    async def spy(name, hints, on_step):
        seen.update(name=name, hints=hints)
        return await fake_find(name, hints, on_step)

    monkeypatch.setattr(agents, "find_candidates", spy)
    body = {"name": "  Alex   Rivera ", "hints": {"field": "toxicology"}}
    r = client.post("/candidate-searches", json=body)
    assert r.status_code == 202
    started = r.json()
    assert set(started) == {"search_id", "job_id", "status"}
    assert started["status"] == "queued"
    assert started["search_id"].startswith("srch_") and started["job_id"].startswith("job_")

    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "done" and job["kind"] == "candidate_search"
    assert job["result"] == {"kind": "candidate_search", "id": started["search_id"]}
    assert job["progress"] == {"done": 4, "total": 4}
    assert [s["key"] for s in job["steps"]] == [
        "openalex", "courtlistener", "web_search", "group_candidates"
    ]
    assert job["started_at"] and job["finished_at"] and job["error"] is None
    assert seen == {
        "name": "Alex Rivera",
        "hints": {"field": "toxicology", "location": None, "affiliation": None},
    }

    search = client.get(f"/candidate-searches/{started['search_id']}").json()
    assert search["status"] == "done" and search["name"] == "Alex Rivera"
    assert search["error"] is None and search["notice"] is None
    assert search["failed_sources"] == [
        {"source": "courtlistener", "reason": "COURTLISTENER_TOKEN is not set"}
    ]
    assert len(search["candidates"]) == 2
    for cand in search["candidates"]:
        assert cand["candidate_id"].startswith("cand_")
        assert set(cand) == set(PERSON) | {"candidate_id", "expert_evidence", "expert_note"}
        assert cand["expert_evidence"] is True and cand["expert_note"] is None
        assert cand["evidence"] and all(e["url"] for e in cand["evidence"])
    again = client.get(f"/candidate-searches/{started['search_id']}").json()
    assert again["candidates"] == search["candidates"]


def test_failed_search(client, monkeypatch):
    monkeypatch.setattr(agents, "find_candidates", broken_find)
    started = client.post("/candidate-searches", json={"name": "Alex Rivera"}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "failed"
    assert job["error"] == "AI grouping step failed after 1 retry"
    assert job["result"] is None
    search = client.get(f"/candidate-searches/{started['search_id']}").json()
    assert search["status"] == "failed" and search["candidates"] == []
    assert search["error"] == "AI grouping step failed after 1 retry"


def test_unknown_ids_are_404(client):
    assert client.get("/jobs/job_nope").json() == {"detail": "job not found"}
    assert client.get("/jobs/job_nope").status_code == 404
    assert client.get("/candidate-searches/srch_nope").status_code == 404
