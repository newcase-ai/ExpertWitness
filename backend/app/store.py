"""SQLite storage (stdlib sqlite3): jobs, searches, candidates, research runs and findings,
URL cache, API call log."""

import json
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.models import (
    Candidate,
    CandidateSearch,
    FailedSource,
    Finding,
    Gap,
    Job,
    JobStep,
    Progress,
    ResearchFindings,
)

SCHEMA = """
create table if not exists jobs (
    job_id text primary key, kind text not null, status text not null,
    created_at text not null, started_at text, finished_at text,
    steps text not null, result text, error text
);
create table if not exists searches (
    search_id text primary key, job_id text not null, name text not null, hints text,
    created_at text not null, failed_sources text not null default '[]'
);
create table if not exists candidates (
    candidate_id text primary key, search_id text not null, position integer not null,
    data text not null
);
create table if not exists http_cache (url text primary key, body blob not null, fetched_at text);
create table if not exists api_calls (source text not null, at real not null);
create table if not exists research_runs (
    dossier_id text primary key, candidate_id text not null, job_id text not null,
    created_at text not null
);
create table if not exists findings (
    finding_id text primary key, dossier_id text not null, position integer not null,
    data text not null
);
create table if not exists gaps (
    dossier_id text not null, position integer not null, step text not null, reason text not null
);
"""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


_ready: set[str] = set()  # database paths whose tables exist


def connect() -> sqlite3.Connection:
    """Open the database at DATABASE_PATH, creating the folder and tables on first use."""
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if str(path) not in _ready:
        conn.executescript(SCHEMA)
        _ready.add(str(path))
    return conn


def execute(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        with conn:
            return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def init_db() -> None:
    connect().close()


# Jobs


def create_job(kind: str, steps: list[tuple[str, str]]) -> str:
    job_id = new_id("job")
    data = [JobStep(key=k, label=label).model_dump() for k, label in steps]
    execute(
        "insert into jobs (job_id, kind, status, created_at, steps) values (?, ?, 'queued', ?, ?)",
        (job_id, kind, now(), json.dumps(data)),
    )
    return job_id


def get_job(job_id: str) -> Job | None:
    rows = execute("select * from jobs where job_id = ?", (job_id,))
    if not rows:
        return None
    row = dict(rows[0])
    steps = [JobStep(**s) for s in json.loads(row.pop("steps"))]
    finished = sum(s.status in ("done", "failed", "skipped") for s in steps)
    return Job(
        **row | {"result": json.loads(row["result"]) if row["result"] else None},
        steps=steps,
        progress=Progress(done=finished, total=len(steps)),
    )


def start_job(job_id: str) -> None:
    execute("update jobs set status = 'running', started_at = ? where job_id = ?", (now(), job_id))


def set_step(job_id: str, key: str, status: str, detail: str | None = None) -> None:
    job = get_job(job_id)
    if job is None:
        return
    for step in job.steps:
        if step.key == key:
            step.status = status
            step.detail = detail
            step.finished_at = now() if status in ("done", "failed", "skipped") else None
    steps = json.dumps([s.model_dump() for s in job.steps])
    execute("update jobs set steps = ? where job_id = ?", (steps, job_id))


def finish_job(
    job_id: str, status: str, result: dict | None = None, error: str | None = None
) -> None:
    """Mark a job done/failed. On failure, unfinished steps become failed (running) or skipped."""
    job = get_job(job_id)
    if job is None:
        return
    if status == "failed":
        for step in job.steps:
            if step.status in ("pending", "running"):
                step.status = "failed" if step.status == "running" else "skipped"
                step.finished_at = now()
    execute(
        "update jobs set status = ?, finished_at = ?, steps = ?, result = ?, error = ?"
        " where job_id = ?",
        (
            status,
            now(),
            json.dumps([s.model_dump() for s in job.steps]),
            json.dumps(result) if result else None,
            error,
            job_id,
        ),
    )


def fail_interrupted_jobs() -> int:
    """At startup: jobs left queued/running by a stopped server are marked failed."""
    rows = execute("select job_id from jobs where status in ('queued', 'running')")
    for row in rows:
        finish_job(row["job_id"], "failed", error="interrupted by restart")
    return len(rows)


# Candidate searches


def create_search(name: str, hints: dict | None, job_id: str) -> str:
    search_id = new_id("srch")
    execute(
        "insert into searches (search_id, job_id, name, hints, created_at) values (?, ?, ?, ?, ?)",
        (search_id, job_id, name, json.dumps(hints), now()),
    )
    return search_id


def save_candidates(
    search_id: str, candidates: list[Candidate], failed_sources: list[FailedSource]
) -> list[Candidate]:
    """Give each candidate a new stable `cand_` id and store it with the failed sources."""
    stored = [c.model_copy(update={"candidate_id": new_id("cand")}) for c in candidates]
    conn = connect()
    try:
        with conn:
            conn.executemany(
                "insert into candidates (candidate_id, search_id, position, data)"
                " values (?, ?, ?, ?)",
                [(c.candidate_id, search_id, i, c.model_dump_json()) for i, c in enumerate(stored)],
            )
            conn.execute(
                "update searches set failed_sources = ? where search_id = ?",
                (json.dumps([f.model_dump() for f in failed_sources]), search_id),
            )
    finally:
        conn.close()
    return stored


def save_failed_sources(search_id: str, failed_sources: list[FailedSource | dict]) -> None:
    """Store failed sources alone (a search whose AI step failed still names them)."""
    data = [FailedSource.model_validate(f).model_dump() for f in failed_sources]
    execute(
        "update searches set failed_sources = ? where search_id = ?", (json.dumps(data), search_id)
    )


def get_search(search_id: str) -> CandidateSearch | None:
    rows = execute(
        "select s.*, j.status, j.error from searches s join jobs j using (job_id)"
        " where s.search_id = ?",
        (search_id,),
    )
    if not rows:
        return None
    row = rows[0]
    done = row["status"] == "done"
    candidates = execute(
        "select data from candidates where search_id = ? order by position", (search_id,)
    )
    return CandidateSearch(
        search_id=row["search_id"],
        name=row["name"],
        status=row["status"],
        created_at=row["created_at"],
        candidates=[Candidate.model_validate_json(r["data"]) for r in candidates] if done else [],
        failed_sources=json.loads(row["failed_sources"]),
        error=row["error"],
    )


def get_candidate(candidate_id: str) -> Candidate | None:
    rows = execute("select data from candidates where candidate_id = ?", (candidate_id,))
    return Candidate.model_validate_json(rows[0]["data"]) if rows else None



# Research runs (one per POST /research; the dossier_id names the run and, from M4, its dossier)


def create_research(candidate_id: str, job_id: str) -> str:
    dossier_id = new_id("dos")
    execute(
        "insert into research_runs (dossier_id, candidate_id, job_id, created_at) values (?, ?, ?, ?)",
        (dossier_id, candidate_id, job_id, now()),
    )
    return dossier_id


def active_research(candidate_id: str) -> tuple[str, str] | None:
    """(job_id, dossier_id) of a queued or running research job for this candidate, else None."""
    rows = execute(
        "select r.job_id, r.dossier_id from research_runs r join jobs j using (job_id)"
        " where r.candidate_id = ? and j.status in ('queued', 'running')"
        " order by r.created_at desc limit 1",
        (candidate_id,),
    )
    return (rows[0]["job_id"], rows[0]["dossier_id"]) if rows else None


def save_findings(dossier_id: str, findings: list[Finding], gaps: list[Gap]) -> list[Finding]:
    """Give each finding a new `fnd_` id and store findings and gaps in one transaction."""
    stored = [f.model_copy(update={"finding_id": new_id("fnd")}) for f in findings]
    conn = connect()
    try:
        with conn:
            conn.executemany(
                "insert into findings (finding_id, dossier_id, position, data) values (?, ?, ?, ?)",
                [(f.finding_id, dossier_id, i, f.model_dump_json()) for i, f in enumerate(stored)],
            )
            conn.executemany(
                "insert into gaps (dossier_id, position, step, reason) values (?, ?, ?, ?)",
                [(dossier_id, i, g.step, g.reason) for i, g in enumerate(gaps)],
            )
    finally:
        conn.close()
    return stored


def get_research(dossier_id: str) -> ResearchFindings | None:
    rows = execute(
        "select r.*, j.status from research_runs r join jobs j using (job_id) where r.dossier_id = ?",
        (dossier_id,),
    )
    if not rows:
        return None
    row = rows[0]
    findings = execute(
        "select data from findings where dossier_id = ? order by position", (dossier_id,)
    )
    gaps = execute(
        "select step, reason from gaps where dossier_id = ? order by position", (dossier_id,)
    )
    return ResearchFindings(
        dossier_id=row["dossier_id"],
        candidate_id=row["candidate_id"],
        job_id=row["job_id"],
        status=row["status"],
        findings=[Finding.model_validate_json(r["data"]) for r in findings],
        gaps=[Gap(step=r["step"], reason=r["reason"]) for r in gaps],
    )


# HTTP cache and API call log (CourtListener hour/day caps)


def cache_get(url: str) -> bytes | None:
    rows = execute("select body from http_cache where url = ?", (url,))
    return rows[0]["body"] if rows else None


def cache_put(url: str, body: bytes) -> None:
    execute(
        "insert or replace into http_cache (url, body, fetched_at) values (?, ?, ?)",
        (url, body, now()),
    )


def record_call(source: str) -> None:
    execute("insert into api_calls (source, at) values (?, ?)", (source, time.time()))


def calls_since(source: str, seconds: float) -> int:
    rows = execute(
        "select count(*) from api_calls where source = ? and at > ?",
        (source, time.time() - seconds),
    )
    return rows[0][0]
