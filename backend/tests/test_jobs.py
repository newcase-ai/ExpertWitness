import asyncio

from fastapi.testclient import TestClient

from app import candidate_agent, jobs, store
from app.main import app
from app.models import Candidate, FailedSource


def new_search():
    job_id = store.create_job("candidate_search", jobs.CANDIDATE_STEPS)
    return job_id, store.create_search("Alex Rivera", None, job_id)


def test_steps_follow_on_step_callback(monkeypatch):
    async def finder(name, hints, on_step):
        on_step("openalex", "running")
        assert store.get_job(job_id).steps[0].status == "running"
        on_step("openalex", "done", "3 authors")
        on_step("courtlistener", "skipped", "no token")
        on_step("web_search", "failed", "timeout")
        on_step("group_candidates", "done")
        evidence = [{"source": "web", "url": "https://example.edu/x", "title": "Page"}]
        return [Candidate(display_name="Alex Rivera", summary="Synthetic.", evidence=evidence)], []

    monkeypatch.setattr(candidate_agent, "find_candidates", finder)
    job_id, search_id = new_search()
    asyncio.run(jobs.run_candidate_search(job_id, search_id, "Alex Rivera", None))

    job = store.get_job(job_id)
    assert job.status == "done" and job.started_at and job.finished_at
    assert [(s.status, s.detail) for s in job.steps] == [
        ("done", "3 authors"), ("skipped", "no token"), ("failed", "timeout"), ("done", None)
    ]
    assert all(s.finished_at for s in job.steps)
    assert len(store.get_search(search_id).candidates) == 1


def test_finder_crash_fails_job_and_closes_steps(monkeypatch):
    async def finder(name, hints, on_step):
        on_step("openalex", "done")
        on_step("courtlistener", "running")
        raise ValueError()

    monkeypatch.setattr(candidate_agent, "find_candidates", finder)
    job_id, search_id = new_search()
    asyncio.run(jobs.run_candidate_search(job_id, search_id, "Alex Rivera", None))

    job = store.get_job(job_id)
    assert job.status == "failed" and job.error == "ValueError"
    assert [s.status for s in job.steps] == ["done", "failed", "skipped", "skipped"]
    assert store.get_search(search_id).status == "failed"


class GroupingFailed(Exception):
    """Stand-in for candidate_agent.FindCandidatesError: jobs.py reads its `failed_sources`."""

    def __init__(self, message, failed_sources):
        super().__init__(message)
        self.failed_sources = failed_sources


def test_failed_ai_step_keeps_failed_sources(monkeypatch):
    failed = [FailedSource(source="courtlistener", reason="HTTP 500")]

    async def finder(name, hints, on_step):
        on_step("group_candidates", "running")
        raise GroupingFailed("AI grouping step failed after 1 retry", failed)

    monkeypatch.setattr(candidate_agent, "find_candidates", finder)
    job_id, search_id = new_search()
    asyncio.run(jobs.run_candidate_search(job_id, search_id, "Alex Rivera", None))

    search = store.get_search(search_id)
    assert search.status == "failed" and search.candidates == []
    assert search.error == "AI grouping step failed after 1 retry"
    assert search.failed_sources == failed


def test_startup_marks_interrupted_jobs_failed():
    job_id, search_id = new_search()
    store.start_job(job_id)
    with TestClient(app) as client:
        job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["error"] == "interrupted by restart"
    assert store.get_search(search_id).error == "interrupted by restart"
