"""POST /research, GET /jobs and GET /dossiers/{id}/findings with fake sources. Synthetic people only."""

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import collectors, jobs, store
from app.main import app
from app.models import Candidate
from app.sources import SourceSkipped
from tests.fakes import PAGE_TEXT, RESEARCH_SOURCES, research_sources_fixture  # noqa: F401 - pytest fixture
from tests.test_api import wait_for_job

PERSON = Candidate(
    display_name="Alex J. Rivera", summary="Synthetic toxicologist.", field="toxicology",
    identifiers={"openalex": ["A0000000001"]},
    evidence=[{"source": "openalex", "url": "https://openalex.org/A0000000001", "title": "OpenAlex"}],
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def candidate_id(client):
    job_id = store.create_job("candidate_search", jobs.CANDIDATE_STEPS)
    search_id = store.create_search("Alex Rivera", None, job_id)
    return store.save_candidates(search_id, [PERSON], [])[0].candidate_id


def wait_for(client, job_id, status):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] == status:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job never reached {status}")


def test_research_flow(client, candidate_id, research_sources):
    """3b + 3g: 202 at once, status moves queued -> running -> done; a double submit while running
    gets the same ids (200); findings are 409 until the job is done."""
    gate = threading.Event()

    async def slow_search(name):
        while not gate.is_set():
            await asyncio.sleep(0.01)
        return RESEARCH_SOURCES["courtlistener.search_opinions"]

    research_sources({"courtlistener.search_opinions": slow_search})
    r = client.post("/research", json={"candidate_id": candidate_id})
    assert r.status_code == 202
    started = r.json()
    assert set(started) == {"job_id", "dossier_id", "status"} and started["status"] == "queued"
    assert started["job_id"].startswith("job_") and started["dossier_id"].startswith("dos_")
    job_id, dossier_id = started["job_id"], started["dossier_id"]

    job = wait_for(client, job_id, "running")
    assert job["kind"] == "research" and job["progress"]["total"] == 9
    assert [s["key"] for s in job["steps"]] == [k for k, _ in jobs.RESEARCH_STEPS]
    assert next(s for s in job["steps"] if s["key"] == "courtlistener")["status"] == "running"

    again = client.post("/research", json={"candidate_id": candidate_id})
    assert again.status_code == 200
    assert again.json() == {"job_id": job_id, "dossier_id": dossier_id, "status": "running"}
    r = client.get(f"/dossiers/{dossier_id}/findings")
    assert r.status_code == 409
    assert r.json() == {"detail": f"findings not ready: job {job_id} is running"}

    gate.set()
    job = wait_for_job(client, job_id)
    assert job["status"] == "done" and job["result"] == {"kind": "dossier", "id": dossier_id}
    assert job["progress"] == {"done": 9, "total": 9}
    assert all(s["status"] == "done" for s in job["steps"])

    body = client.get(f"/dossiers/{dossier_id}/findings").json()
    assert body["dossier_id"] == dossier_id and body["job_id"] == job_id
    assert body["candidate_id"] == candidate_id and body["status"] == "done"
    kinds = {f["kind"] for f in body["findings"]}
    assert kinds == {"court_opinion", "web_testimony", "work", "retraction", "author_stats",
                     "leie_possible_match", "web_credential", "page_testimony_case", "page_credential",
                     "case_check"}
    assert PAGE_TEXT not in json.dumps(body)  # 3j: page text is never stored
    for f in body["findings"]:  # 3c
        assert f["finding_id"].startswith("fnd_") and f["url"] and f["retrieved_at"]
        if f["source_type"] == "web":  # 3h
            assert f["confidence"] == "low" and "verify" in f["note"]
    assert "snippet" not in json.dumps(body).lower()  # 3h: no snippet key or text anywhere
    assert {"step": "courtlistener", "reason": collectors.STATE_COURTS_GAP} in body["gaps"]

    # a finished job does not block a fresh run
    fresh = client.post("/research", json={"candidate_id": candidate_id})
    assert fresh.status_code == 202 and fresh.json()["dossier_id"] != dossier_id
    wait_for_job(client, fresh.json()["job_id"])


def test_failing_collector_is_a_gap(client, candidate_id, research_sources):
    """3d: one collector's sources fail; the job still ends done and the other findings are saved."""
    research_sources({"openalex.author": RuntimeError("down"), "openalex.works": RuntimeError("down")})
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "done" and job["error"] is None
    steps = {s["key"]: (s["status"], s["detail"]) for s in job["steps"]}
    assert steps["openalex"] == ("failed", "OpenAlex failed (RuntimeError)")
    assert steps["crossref"] == ("skipped", "no works to check")
    body = client.get(f"/dossiers/{started['dossier_id']}/findings").json()
    assert {f["collector"] for f in body["findings"]} == {"testimony", "credentials"}
    assert {"step": "openalex", "reason": "OpenAlex failed (RuntimeError)"} in body["gaps"]


def test_collector_crash_fails_job(client, candidate_id, research_sources, monkeypatch):
    research_sources()

    async def crash(candidate, step):
        raise ValueError()

    monkeypatch.setattr(collectors, "collect_publications", crash)
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "failed" and job["error"] == "ValueError" and job["result"] is None
    r = client.get(f"/dossiers/{started['dossier_id']}/findings")
    assert r.json() == {"detail": f"findings not ready: job {started['job_id']} is failed"}


def test_findings_survive_restart(candidate_id, research_sources):
    """3f: findings are in the SQLite file, not in memory: a new app start reads them back."""
    research_sources()
    with TestClient(app) as client:
        started = client.post("/research", json={"candidate_id": candidate_id}).json()
        wait_for_job(client, started["job_id"])
        before = client.get(f"/dossiers/{started['dossier_id']}/findings").json()
    store._ready.clear()  # as in a new process: tables are re-checked on the same file
    with TestClient(app) as client:
        after = client.get(f"/dossiers/{started['dossier_id']}/findings")
        assert after.status_code == 200 and after.json() == before
        assert client.get(f"/jobs/{started['job_id']}").json()["status"] == "done"
    assert before["findings"] and before["gaps"]


@pytest.mark.parametrize("body", [{}, {"candidate_id": 5}, {"candidate_id": "cand_x", "extra": 1}])
def test_bad_body_is_422(client, body):
    assert client.post("/research", json=body).status_code == 422


def test_errors(client, monkeypatch):
    r = client.post("/research", json={"candidate_id": "cand_nope"})
    assert r.status_code == 404 and r.json() == {"detail": "candidate not found"}
    r = client.get("/dossiers/dos_nope/findings")
    assert r.status_code == 404 and r.json() == {"detail": "dossier not found"}
    monkeypatch.delenv("MODEL_API_KEY")
    r = client.post("/research", json={"candidate_id": "cand_nope"})
    assert r.status_code == 503 and r.json() == {"detail": "MODEL_API_KEY is not set"}


def test_pasted_testimony_list_is_parsed_not_stored(client, candidate_id, research_sources, tmp_path):
    """3k: the pasted list goes to one AI call; only parsed cases are saved, never the text."""
    calls = research_sources()
    pasted = "SYNTHETIC-PASTED-LIST 1. Poe vs. Demo LLC, Example County Court, 2017"
    started = client.post("/research", json={"candidate_id": candidate_id, "testimony_list": pasted}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "done"
    body = client.get(f"/dossiers/{started['dossier_id']}/findings").json()
    checks = {f["title"]: f for f in body["findings"] if f["kind"] == "case_check"}
    assert checks["Poe vs. Demo LLC"]["facts"]["listed_in"] == "testimony_list"
    assert checks["Doe v. Example Co."]["facts"]["status"] == "found"
    assert all(f["url"] and f["retrieved_at"] for f in checks.values())
    assert any(pasted in args[1] for key, args, _ in calls if key == "llm.call_model")  # the AI saw it
    assert "SYNTHETIC-PASTED-LIST" not in json.dumps(body)
    db = (tmp_path / "data" / "test.db").read_bytes()
    assert b"SYNTHETIC-PASTED-LIST" not in db and PAGE_TEXT.encode() not in db


def test_testimony_list_too_long_is_422(client, candidate_id):
    r = client.post("/research", json={"candidate_id": candidate_id, "testimony_list": "x" * 20_001})
    assert r.status_code == 422


def test_no_firecrawl_key_is_a_gap(client, candidate_id, research_sources):
    """No Firecrawl key: the page reader is skipped (gap), the job still finishes done."""
    research_sources({"firecrawl.scrape": SourceSkipped("FIRECRAWL_API_KEY is not set")})
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "done" and job["error"] is None
    steps = {s["key"]: (s["status"], s["detail"]) for s in job["steps"]}
    assert steps["read_pages"] == ("skipped", "FIRECRAWL_API_KEY is not set")
    assert steps["cross_check"] == ("skipped", "no cases to check")
    body = client.get(f"/dossiers/{started['dossier_id']}/findings").json()
    assert {"step": "read_pages", "reason": "FIRECRAWL_API_KEY is not set"} in body["gaps"]
