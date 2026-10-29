from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response

from app import http, jobs, store
from app.config import get_settings
from app.models import (
    CandidateSearch,
    CandidateSearchRequest,
    CandidateSearchStarted,
    Job,
    ResearchFindings,
    ResearchRequest,
    ResearchStarted,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    store.fail_interrupted_jobs()
    yield
    await http.close_client()


app = FastAPI(title="Expert Witness API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/candidate-searches", status_code=202)
async def start_candidate_search(body: CandidateSearchRequest) -> CandidateSearchStarted:
    if not get_settings().model_api_key:
        raise HTTPException(503, "MODEL_API_KEY is not set")
    hints = body.hints.model_dump() if body.hints else None
    search_id, job_id = jobs.start_candidate_search(body.name, hints)
    return CandidateSearchStarted(search_id=search_id, job_id=job_id, status="queued")


@app.get("/candidate-searches/{search_id}")
def get_candidate_search(search_id: str) -> CandidateSearch:
    search = store.get_search(search_id)
    if search is None:
        raise HTTPException(404, "candidate search not found")
    return search


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> Job:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.post("/research", status_code=202)
async def start_research(body: ResearchRequest, response: Response) -> ResearchStarted:
    """202 for a new job; 200 with the same ids if one for this candidate is queued or running (a
    `testimony_list` sent with the repeat is ignored). The pasted list is never stored."""
    if not get_settings().model_api_key:
        raise HTTPException(503, "MODEL_API_KEY is not set")
    candidate = store.get_candidate(body.candidate_id)
    if candidate is None:
        raise HTTPException(404, "candidate not found")
    job_id, dossier_id, created = jobs.start_research(candidate, body.testimony_list)
    if not created:
        response.status_code = 200
    status = store.get_job(job_id).status
    return ResearchStarted(job_id=job_id, dossier_id=dossier_id, status=status)


@app.get("/dossiers/{dossier_id}/findings")
def get_findings(dossier_id: str) -> ResearchFindings:
    research = store.get_research(dossier_id)
    if research is None:
        raise HTTPException(404, "dossier not found")
    if research.status != "done":
        raise HTTPException(409, f"findings not ready: job {research.job_id} is {research.status}")
    return research
