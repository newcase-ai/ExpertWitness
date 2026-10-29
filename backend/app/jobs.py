"""Background jobs: an asyncio task in the same process, progress kept in SQLite."""

import asyncio
import time

from app import agents, collectors, store
from app.models import Candidate

CANDIDATE_STEPS = [
    ("openalex", "Publication records"),
    ("courtlistener", "Court opinions"),
    ("web_search", "Web search"),
    ("group_candidates", "Grouping people"),
]

RESEARCH_STEPS = [
    ("courtlistener", "Court opinions"),
    ("web_testimony", "Testimony on the web"),
    ("openalex", "Publications"),
    ("crossref", "Retraction check"),
    ("oig_leie", "OIG exclusions"),
    ("web_credentials", "Licence and board pages"),
    ("read_pages", "Expert's own pages"),
    ("cross_check", "Testimony list check"),
    ("follow_up", "Following leads"),
]

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
        candidates, failed = await agents.find_candidates(name, hints, on_step)
        store.save_candidates(search_id, candidates, failed)
    except Exception as exc:  # noqa: BLE001 - any crash must mark the job failed
        # agents.FindCandidatesError carries the sources that failed before the AI step did
        if failed := getattr(exc, "failed_sources", None):
            store.save_failed_sources(search_id, failed)
        store.finish_job(job_id, "failed", error=str(exc) or type(exc).__name__)
        return
    store.finish_job(job_id, "done", result={"kind": "candidate_search", "id": search_id})


def start_research(candidate: Candidate, testimony_list: str | None = None) -> tuple[str, str, bool]:
    """Return (job_id, dossier_id, created). A queued/running job for the same candidate is reused
    (created=False). No await between the check and the insert, so two requests cannot both start.
    `testimony_list` (pasted by the user) stays in memory; only the cases parsed from it are saved."""
    if active := store.active_research(candidate.candidate_id):
        return *active, False
    job_id = store.create_job("research", RESEARCH_STEPS)
    dossier_id = store.create_research(candidate.candidate_id, job_id)
    task = asyncio.create_task(run_research(job_id, dossier_id, candidate, testimony_list))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job_id, dossier_id, True


async def run_research(job_id: str, dossier_id: str, candidate: Candidate, testimony_list: str | None = None) -> None:
    """Run the three collectors in parallel, then read the pages they found, then check the listed
    cases, then let the follow-up agent chase leads, and save everything. Failed sources are gaps, so the job ends `done` even if every step
    failed; only an unexpected crash fails it."""
    store.start_job(job_id)
    started = time.monotonic()

    def step(key: str, status: str, detail: str | None = None) -> None:
        store.set_step(job_id, key, status, detail)

    try:
        results = await asyncio.gather(
            collectors.collect_testimony(candidate, step),
            collectors.collect_publications(candidate, step),
            collectors.collect_credentials(candidate, step),
        )
        pages = await collectors.collect_pages(candidate, [f for found, _ in results for f in found], step)
        checks = await collectors.cross_check(candidate, pages[0], testimony_list, step)
        results = [*results, pages, checks]
        results.append(await collectors.follow_up(candidate, [f for found, _ in results for f in found], step,
                                                  started))
        store.save_findings(
            dossier_id,
            [f for found, _ in results for f in found],
            [g for _, gaps in results for g in gaps],
        )
    except Exception as exc:  # noqa: BLE001 - any crash must mark the job failed
        store.finish_job(job_id, "failed", error=str(exc) or type(exc).__name__)
        return
    store.finish_job(job_id, "done", result={"kind": "dossier", "id": dossier_id})
