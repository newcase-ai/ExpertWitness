import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response

from app import expert_profile, http, jobs, llm, memory, store
from app.config import get_settings
from app.models import (
    CandidateSearch,
    CandidateSearchRequest,
    CandidateSearchStarted,
    Dossier,
    ExpertProfileView,
    FileRow,
    Job,
    Profile,
    ResearchFindings,
    ResearchRequest,
    ResearchStarted,
    SearchSummary,
    TraceEvents,
)

# The server log: one line per job event and agent tool call (app/store.py), without the UI's status
# checks every 2 s. uvicorn sets up only its own loggers.
POLLED = ("/jobs/", "/candidate-searches/")
agent_log = logging.getLogger("app.agent")
agent_log.setLevel(logging.INFO)
agent_log.addHandler(logging.StreamHandler())
agent_log.handlers[-1].setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
logging.getLogger("uvicorn.access").addFilter(
    lambda r: not (len(r.args) > 2 and r.args[1] == "GET" and str(r.args[2]).startswith(POLLED)))


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


@app.get("/candidate-searches")
def list_candidate_searches() -> list[SearchSummary]:
    """The newest 100 searches with their research runs: status, counts and the latest tool call."""
    return store.list_searches()


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


@app.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)) -> TraceEvents:
    """The run trace (app/trace.py): events with seq > `after`, oldest first. Poll with `after` set to
    the last `last_seq` to get only new ones; after=0 gives the whole trace."""
    if store.get_job(job_id) is None:
        raise HTTPException(404, "job not found")
    events = store.events(job_id, after, limit)
    return TraceEvents(job_id=job_id, events=events, last_seq=events[-1].seq if events else after)


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


@app.get("/dossiers/{dossier_id}")
def get_dossier(dossier_id: str) -> Dossier:
    research = store.get_research(dossier_id)
    if research is None:
        raise HTTPException(404, "dossier not found")
    candidate = store.get_candidate(research.candidate_id)
    return Dossier(dossier_id=dossier_id, candidate_id=research.candidate_id, job_id=research.job_id,
                   status=research.status, display_name=candidate.display_name if candidate else "")


@app.get("/dossiers/{dossier_id}/profile")
def get_profile(dossier_id: str) -> Profile:
    """The run's profile so far (version 0 and empty before its first update), with `sources`: each
    cited finding's url and title."""
    research = store.get_research(dossier_id)
    if research is None:
        raise HTTPException(404, "dossier not found")
    current = store.get_profile(dossier_id)
    current.sources = {f.finding_id: {"url": f.url, "title": f.title} for f in research.findings}
    return current


@app.get("/dossiers/{dossier_id}/findings")
def get_findings(dossier_id: str) -> ResearchFindings:
    research = store.get_research(dossier_id)
    if research is None:
        raise HTTPException(404, "dossier not found")
    if research.status in ("queued", "running"):  # a failed or killed run shows what it saved
        raise HTTPException(409, f"findings not ready: job {research.job_id} is {research.status}")
    return research


def finished_research(dossier_id: str):
    research = store.get_research(dossier_id)
    if research is None:
        raise HTTPException(404, "dossier not found")
    if research.status in ("queued", "running"):
        raise HTTPException(409, f"profile not ready: job {research.job_id} is {research.status}")
    return research


@app.get("/dossiers/{dossier_id}/expert-profile")
def get_expert_profile(dossier_id: str) -> ExpertProfileView:
    """The structured profile for the profile page (app/expert_profile.py). 409 while the run works;
    `built` is false when its final model call failed or never ran (code-built parts only)."""
    finished_research(dossier_id)
    return expert_profile.view(dossier_id)


@app.post("/dossiers/{dossier_id}/expert-profile/rebuild")
async def rebuild_expert_profile(dossier_id: str) -> ExpertProfileView:
    """Run the final model call again for a finished run (older runs, prompt changes)."""
    research = finished_research(dossier_id)
    if not get_settings().model_api_key:
        raise HTTPException(503, "MODEL_API_KEY is not set")
    candidate = store.get_candidate(research.candidate_id)
    if candidate is None:
        raise HTTPException(404, "candidate not found")
    try:
        await expert_profile.build(dossier_id, candidate)
    except llm.ModelError as exc:
        raise HTTPException(502, str(exc)) from exc
    return expert_profile.view(dossier_id)


@app.get("/dossiers/{dossier_id}/files")
def get_files(dossier_id: str) -> list[FileRow]:
    """The run's downloaded files and our notes on them (debug mode); no bytes or text."""
    if store.get_research(dossier_id) is None:
        raise HTTPException(404, "dossier not found")
    return [FileRow(**r, has_text=bool(r["text_path"])) for r in memory.files(dossier_id)]
