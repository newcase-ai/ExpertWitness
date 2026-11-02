"""POST /research, GET /jobs and GET /dossiers/{id}/findings: the research job runs the main agent
(FakeAdkModel scripts, test-only tools). Synthetic people only."""

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import jobs, store
from app.main import app
from app.models import Candidate, Finding
from app.tools import Ctx
from tests.fakes import PAGE_MARKDOWN, PAGE_TEXT, call, register_tool_fixture  # noqa: F401 - fixture
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


CASE_URL = "https://example.org/news/rivera-testimony"


@pytest.fixture
def saving_tool(register_tool):
    """A test-only tool that reads a fake page (in memory) and saves one finding."""

    async def save_case(ctx: Ctx, case: str) -> dict:
        """Save a case found on a page."""
        page = PAGE_MARKDOWN  # page text: fine in memory and in the reply, never stored
        ref = ctx.add_finding("web_testimony", f"Testimony in {case}", CASE_URL, facts={"case": case})
        ctx.log("1 case saved")
        return {"refs": [ref], "page": page}

    return register_tool(save_case, label="Save a case")


def test_research_flow(client, candidate_id, register_tool, saving_tool, adk_model):
    """7e: 202 at once; while the agent's tool runs, GET /jobs/{id} shows one step row and the
    activity row `running`; a double submit gets the same ids (200); findings are 409 until done."""
    gate = threading.Event()

    async def hold(ctx: Ctx) -> dict:
        """Wait (test only)."""
        while not gate.is_set():  # gotcha 19: poll, never block the app's event loop
            await asyncio.sleep(0.01)
        ctx.log("held")
        return {"ok": True}

    register_tool(hold, label="Hold")
    adk_model.script = [call("save_case", case="Doe v. Example Co."), call("hold"), "DONE"]
    r = client.post("/research", json={"candidate_id": candidate_id})
    assert r.status_code == 202
    started = r.json()
    assert set(started) == {"job_id", "dossier_id", "status"} and started["status"] == "queued"
    assert started["job_id"].startswith("job_") and started["dossier_id"].startswith("dos_")
    job_id, dossier_id = started["job_id"], started["dossier_id"]

    for _ in range(300):
        job = client.get(f"/jobs/{job_id}").json()
        if len(job["activity"]) == 2:
            break
        time.sleep(0.01)
    assert job["status"] == "running" and job["progress"] == {"done": 0, "total": 1}
    assert job["steps"] == [{"key": "main_agent", "label": "Research agent", "status": "running",
                             "detail": "1 tool calls", "finished_at": None}]
    first, second = job["activity"]
    assert first["seq"] == 1 and first["tool"] == "save_case" and first["label"] == "Save a case"
    assert first["status"] == "done" and first["detail"] == "1 case saved"
    assert first["args"] == {"case": "Doe v. Example Co."} and first["finished_at"]
    assert second["seq"] == 2 and second["status"] == "running" and second["finished_at"] is None

    again = client.post("/research", json={"candidate_id": candidate_id})
    assert again.status_code == 200
    assert again.json() == {"job_id": job_id, "dossier_id": dossier_id, "status": "running"}
    r = client.get(f"/dossiers/{dossier_id}/findings")
    assert r.status_code == 409
    assert r.json() == {"detail": f"findings not ready: job {job_id} is running"}

    gate.set()
    job = wait_for_job(client, job_id)
    assert job["status"] == "done" and job["result"] == {"kind": "dossier", "id": dossier_id}
    assert job["progress"] == {"done": 1, "total": 1}
    assert job["steps"][0]["status"] == "done" and job["steps"][0]["detail"] == "agent finished"
    assert [a["status"] for a in job["activity"]] == ["done", "done"]

    body = client.get(f"/dossiers/{dossier_id}/findings").json()
    assert body["dossier_id"] == dossier_id and body["job_id"] == job_id
    assert body["candidate_id"] == candidate_id and body["status"] == "done"
    [finding] = body["findings"]
    assert finding["collector"] == "save_case" and finding["kind"] == "web_testimony"
    assert finding["url"] == CASE_URL and finding["source_type"] == "web"
    assert finding["finding_id"].startswith("fnd_") and finding["retrieved_at"]
    assert PAGE_TEXT not in json.dumps(body) and PAGE_TEXT not in json.dumps(job)

    # a finished job does not block a fresh run
    fresh = client.post("/research", json={"candidate_id": candidate_id})
    assert fresh.status_code == 202 and fresh.json()["dossier_id"] != dossier_id
    wait_for_job(client, fresh.json()["job_id"])


def test_candidate_search_job_has_empty_activity(client):
    job_id = store.create_job("candidate_search", jobs.CANDIDATE_STEPS)
    assert client.get(f"/jobs/{job_id}").json()["activity"] == []


def test_model_failure_before_any_tool_fails_job(client, candidate_id, adk_model):
    adk_model.script = [RuntimeError("SECRET provider text")]
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "failed" and job["result"] is None
    assert job["error"] == "model call failed (RuntimeError)"
    assert job["steps"][0]["status"] == "failed" and "SECRET" not in json.dumps(job)
    r = client.get(f"/dossiers/{started['dossier_id']}/findings")
    assert r.status_code == 200 and r.json()["status"] == "failed" and r.json()["findings"] == []


def test_model_failure_after_a_tool_keeps_findings(client, candidate_id, saving_tool, adk_model):
    """D5: the model fails after one tool call finished: the job is done with a gap."""
    adk_model.script = [call("save_case", case="Doe v. Example Co."), RuntimeError("SECRET")]
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "done" and job["error"] is None
    assert (job["steps"][0]["status"], job["steps"][0]["detail"]) == ("failed", "model call failed (RuntimeError)")
    body = client.get(f"/dossiers/{started['dossier_id']}/findings").json()
    assert len(body["findings"]) == 1
    assert body["gaps"] == [{"step": "main_agent", "reason": "model call failed (RuntimeError)"}]


def test_crash_outside_the_agent_fails_job(client, candidate_id, monkeypatch):
    from app import research_agent

    async def crash(*args):
        raise ValueError()

    monkeypatch.setattr(research_agent, "research", crash)
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    job = wait_for_job(client, started["job_id"])
    assert job["status"] == "failed" and job["error"] == "ValueError" and job["result"] is None


def test_findings_survive_restart(candidate_id, saving_tool, adk_model):
    """3f: findings and activity are in the SQLite file: a new app start reads them back."""
    adk_model.script = [call("save_case", case="Doe v. Example Co."), "DONE"]
    with TestClient(app) as client:
        started = client.post("/research", json={"candidate_id": candidate_id}).json()
        job = wait_for_job(client, started["job_id"])
        before = client.get(f"/dossiers/{started['dossier_id']}/findings").json()
    store._ready.clear()  # as in a new process: tables are re-checked on the same file
    with TestClient(app) as client:
        after = client.get(f"/dossiers/{started['dossier_id']}/findings")
        assert after.status_code == 200 and after.json() == before
        assert client.get(f"/jobs/{started['job_id']}").json() == job
    assert before["findings"] and job["activity"]


def test_pasted_testimony_list_goes_to_the_agent_not_stored(client, candidate_id, adk_model, tmp_path):
    """3k: the pasted list is in the agent's first message (memory only); never stored."""
    pasted = "SYNTHETIC-PASTED-LIST 1. Poe vs. Demo LLC, Example County Court, 2017"
    started = client.post("/research", json={"candidate_id": candidate_id, "testimony_list": pasted}).json()
    assert wait_for_job(client, started["job_id"])["status"] == "done"
    assert pasted in adk_model.seen[0].contents[0].parts[0].text
    db = (tmp_path / "data" / "test.db").read_bytes()
    assert b"SYNTHETIC-PASTED-LIST" not in db


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


def test_testimony_list_too_long_is_422(client, candidate_id):
    r = client.post("/research", json={"candidate_id": candidate_id, "testimony_list": "x" * 20_001})
    assert r.status_code == 422




def test_get_dossier_reads_a_saved_run(client, candidate_id):
    """A page reload reads the run by its dossier id; nothing new is started."""
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    wait_for_job(client, started["job_id"])
    jobs_before = store.execute("select count(*) as n from jobs")[0]["n"]
    assert client.get(f"/dossiers/{started['dossier_id']}").json() == {
        "dossier_id": started["dossier_id"], "candidate_id": candidate_id, "job_id": started["job_id"],
        "status": "done", "display_name": "Alex J. Rivera"}
    assert store.execute("select count(*) as n from jobs")[0]["n"] == jobs_before
    assert client.get("/dossiers/dos_nope").status_code == 404


def test_killed_run_still_shows_what_it_saved(client, candidate_id):
    """Findings are saved as the agent finds them, so a run killed by a restart keeps them."""
    job_id = store.create_job("research", jobs.RESEARCH_STEPS)
    dossier_id = store.create_research(candidate_id, job_id)
    store.start_job(job_id)
    store.add_finding(dossier_id, Finding(collector="save_case", kind="web_testimony", title="Testimony in Doe",
                                          url=CASE_URL, source_type="web", retrieved_at=store.now(), confidence="low"))
    store.fail_interrupted_jobs()  # what startup does after a kill
    body = client.get(f"/dossiers/{dossier_id}/findings").json()
    assert body["status"] == "failed" and [f["title"] for f in body["findings"]] == ["Testimony in Doe"]


def test_searches_list_shows_each_search_and_how_its_research_is_going(client, candidate_id, saving_tool,
                                                                        adk_model):
    adk_model.script = [call("save_case", case="Doe v. Example Co."), "DONE"]
    started = client.post("/research", json={"candidate_id": candidate_id}).json()
    wait_for_job(client, started["job_id"])
    [search] = client.get("/candidate-searches").json()
    run = search["research"][0]
    assert (search["name"], search["candidates"]) == ("Alex Rivera", 1)
    assert run["dossier_id"] == started["dossier_id"] and run["display_name"] == "Alex J. Rivera"
    assert (run["status"], run["tool_calls"], run["findings"]) == ("done", 1, 1)
    assert run["last_activity"] == "Save a case · done · 1 case saved"
