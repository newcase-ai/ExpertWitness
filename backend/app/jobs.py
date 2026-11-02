"""Background jobs: an asyncio task in the same process, progress kept in SQLite."""

import asyncio

from app import candidate_agent, llm, research_agent, store, trace
from app.models import Candidate

CANDIDATE_STEPS = [
    ("openalex", "Publication records"),
    ("courtlistener", "Court opinions"),
    ("web_search", "Web search"),
    ("group_candidates", "Grouping people"),
]

RESEARCH_STEPS = [("main_agent", "Research agent")]  # DESIGN 4B.5

_tasks: set[asyncio.Task] = set()  # keep references so running tasks are not garbage-collected


def start_candidate_search(name: str, hints: dict | None) -> tuple[str, str]:
    """Create the job and search rows, start the work, return (search_id, job_id)."""
    job_id = store.create_job("candidate_search", CANDIDATE_STEPS)
    search_id = store.create_search(name, hints, job_id)
    task = asyncio.create_task(run_candidate_search(job_id, search_id, name, hints))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return search_id, job_id


async def run_candidate_search(job_id: str, search_id: str, name: str, hints: dict | None) -> None:
    store.start_job(job_id)

    def on_step(step: str, status: str, detail: str | None = None) -> None:
        store.set_step(job_id, step, status, detail)

    try:
        candidates, failed = await candidate_agent.find_candidates(name, hints, on_step)
        store.save_candidates(search_id, candidates, failed)
    except Exception as exc:  # noqa: BLE001 - any crash must mark the job failed
        # candidate_agent.FindCandidatesError carries the sources that failed before the AI step did
        if failed := getattr(exc, "failed_sources", None):
            store.save_failed_sources(search_id, failed)
        store.finish_job(job_id, "failed", error=str(exc) or type(exc).__name__)
        return
    store.finish_job(job_id, "done", result={"kind": "candidate_search", "id": search_id})


def start_research(candidate: Candidate, testimony_list: str | None = None) -> tuple[str, str, bool]:
    """Return (job_id, dossier_id, created). A queued/running job for the same candidate is reused
    (created=False). No await between the check and the insert, so two requests cannot both start.
    `testimony_list` (pasted by the user) goes to the main agent in memory only; it is never stored."""
    if active := store.active_research(candidate.candidate_id):
        return *active, False
    job_id = store.create_job("research", RESEARCH_STEPS)
    dossier_id = store.create_research(candidate.candidate_id, job_id)
    task = asyncio.create_task(run_research(job_id, dossier_id, candidate, testimony_list))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job_id, dossier_id, True


async def run_research(job_id: str, dossier_id: str, candidate: Candidate, testimony_list: str | None = None) -> None:
    """Run the main agent; its tools save findings and gaps as they go. DONE, the step limit or the
    time limit end the job `done`. A model failure is a gap: the job fails only if no tool call
    finished before it (DESIGN 4B.11 D5). Any other crash fails the job."""
    store.start_job(job_id)
    store.set_step(job_id, "main_agent", "running")
    traced = trace.start(job_id)  # every model turn and tool call of this job goes to its trace
    try:
        try:
            run = await research_agent.research(job_id, dossier_id, candidate, testimony_list)
        except llm.ModelError as exc:
            store.add_gap(dossier_id, "main_agent", str(exc))
            trace.record("gap", step="main_agent", reason=str(exc))
            store.set_step(job_id, "main_agent", "failed", str(exc))
            failed = not store.tool_calls_finished(job_id)
            trace.record("run_end", "main_agent", stopped="model error", job="failed" if failed else "done",
                         error=trace.error(exc), **trace.totals())
            if failed:
                store.finish_job(job_id, "failed", error=str(exc))
                return
        else:
            store.set_step(job_id, "main_agent", "done", research_agent.stop_detail(run.stopped))
            trace.record("run_end", "main_agent", stopped=research_agent.stop_detail(run.stopped), job="done",
                         final_text=run.text, model_calls=run.calls, **trace.totals())
    except Exception as exc:  # noqa: BLE001 - any crash must mark the job failed
        trace.record("run_end", "main_agent", stopped="crash", job="failed", error=trace.error(exc), **trace.totals())
        store.finish_job(job_id, "failed", error=str(exc) or type(exc).__name__)
        return
    finally:
        trace.stop(traced)
    store.finish_job(job_id, "done", result={"kind": "dossier", "id": dossier_id})
