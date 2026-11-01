"""SQLite storage (stdlib sqlite3): jobs, searches, candidates, research runs and findings,
URL cache, API call log, the main agent's activity log and memory tables (rows read and written
by app/memory.py)."""

import json
import logging
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.models import (
    ActivityRow,
    Candidate,
    CandidateSearch,
    FailedSource,
    Finding,
    Gap,
    Job,
    JobStep,
    Profile,
    Progress,
    ResearchFindings,
    ResearchSummary,
    SearchSummary,
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
create table if not exists activity (
    job_id text not null, seq integer not null, tool text not null, label text not null,
    status text not null, args text not null, detail text, started_at text not null, finished_at text,
    primary key (job_id, seq)
);
create table if not exists site_manuals (
    domain text primary key, how_to text not null, login_notes text not null, updated_at text not null,
    job_id text
);
create table if not exists state_sites (
    state text not null, domain text not null, what text not null, reliability text not null,
    notes text not null, updated_at text not null, primary key (state, domain)
);
create table if not exists blocked_sites (
    domain text primary key, reason text not null, url text, noted_at text not null
);
create table if not exists files (
    file_id text primary key, dossier_id text not null, source_url text not null, path text not null,
    text_path text, mime text not null, bytes integer not null, retrieved_at text not null, note_md text
);
create table if not exists profiles (
    dossier_id text primary key, data text not null, version integer not null, updated_at text not null
);
"""


log = logging.getLogger("app.agent")  # one line per job event and tool call (app/main.py shows it)


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
        activity=activity(job_id),
    )


def start_job(job_id: str) -> None:
    execute("update jobs set status = 'running', started_at = ? where job_id = ?", (now(), job_id))
    log.info("%s started", job_id)


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
    if status in ("done", "failed", "skipped"):  # not the running updates after every tool call
        log.info("%s step %s %s %s", job_id, key, status, detail or "")


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
    log.info("%s %s %s", job_id, status, error or "")


def fail_interrupted_jobs() -> int:
    """At startup: jobs left queued/running by a stopped server are marked failed (and their
    running tool calls)."""
    execute("update activity set status = 'failed', detail = 'interrupted by restart', finished_at = ?"
            " where status = 'running'", (now(),))
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



def list_searches(limit: int = 100) -> list[SearchSummary]:
    """The newest searches, each with its research runs (newest first) and how they are going."""
    searches = execute(
        "select s.search_id, s.name, s.created_at, j.status,"
        " (select count(*) from candidates c where c.search_id = s.search_id) as candidates"
        " from searches s join jobs j using (job_id) order by s.created_at desc limit ?",
        (limit,),
    )
    runs = execute(
        "select c.search_id, r.dossier_id, json_extract(c.data, '$.display_name') as display_name,"
        " j.status, j.error, r.created_at, j.finished_at,"
        " (select count(*) from activity a where a.job_id = r.job_id) as tool_calls,"
        " (select count(*) from findings f where f.dossier_id = r.dossier_id) as findings,"
        " (select a.label || ' · ' || a.status || coalesce(' · ' || a.detail, '') from activity a"
        "  where a.job_id = r.job_id order by a.seq desc limit 1) as last_activity"
        " from research_runs r join jobs j using (job_id) join candidates c using (candidate_id)"
        " order by r.created_at desc"
    )
    by_search: dict[str, list[ResearchSummary]] = {}
    for run in runs:
        by_search.setdefault(run["search_id"], []).append(
            ResearchSummary(**dict(run)))  # search_id is ignored
    return [SearchSummary(**dict(s), research=by_search.get(s["search_id"], [])) for s in searches]


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


def add_finding(dossier_id: str, finding: Finding) -> str:
    """Save one finding at once (the main agent's tools) and return its new `fnd_` id. The same
    kind + URL again returns the existing id."""
    rows = execute(
        "select finding_id from findings where dossier_id = ? and json_extract(data, '$.kind') = ?"
        " and json_extract(data, '$.url') = ?",
        (dossier_id, finding.kind, finding.url),
    )
    if rows:
        return rows[0]["finding_id"]
    finding_id = new_id("fnd")
    data = finding.model_copy(update={"finding_id": finding_id}).model_dump_json()
    execute(
        "insert into findings (finding_id, dossier_id, position, data) select ?, ?,"
        " coalesce(max(position), -1) + 1, ? from findings where dossier_id = ?",
        (finding_id, dossier_id, data, dossier_id),
    )
    return finding_id


def add_gap(dossier_id: str, step: str, reason: str) -> None:
    execute(
        "insert into gaps (dossier_id, position, step, reason) select ?, coalesce(max(position), -1) + 1,"
        " ?, ? from gaps where dossier_id = ?",
        (dossier_id, step, reason, dossier_id),
    )


def findings(dossier_id: str) -> list[Finding]:
    rows = execute("select data from findings where dossier_id = ? order by position", (dossier_id,))
    return [Finding.model_validate_json(r["data"]) for r in rows]


def get_profile(dossier_id: str) -> Profile:
    """The run's profile; an empty one (version 0) before its first update."""
    rows = execute("select * from profiles where dossier_id = ?", (dossier_id,))
    if not rows:
        return Profile(dossier_id=dossier_id)
    row = rows[0]
    return Profile(dossier_id=dossier_id, version=row["version"], updated_at=row["updated_at"], **json.loads(row["data"]))


def save_profile(profile: Profile) -> None:
    data = profile.model_dump(exclude={"dossier_id", "version", "updated_at", "sources"})
    execute("insert or replace into profiles (dossier_id, data, version, updated_at) values (?, ?, ?, ?)",
            (profile.dossier_id, json.dumps(data), profile.version, profile.updated_at))


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


# Activity log (one row per main agent tool call, DESIGN 4B.6)


def start_activity(job_id: str, tool: str, label: str, args: dict, status: str = "running",
                   detail: str | None = None) -> int:
    """Add a row and return its `seq`. A row that is not `running` is finished at once (refusals)."""
    at = now()
    rows = execute(
        "insert into activity (job_id, seq, tool, label, status, args, detail, started_at, finished_at)"
        " select ?, coalesce(max(seq), 0) + 1, ?, ?, ?, ?, ?, ?, ? from activity where job_id = ?"
        " returning seq",
        (job_id, tool, label, status, json.dumps(args), detail, at, None if status == "running" else at,
         job_id),
    )
    log.info("%s %-16s %-8s %s", job_id, tool, status, json.dumps(args) if status == "running" else detail or "")
    return rows[0]["seq"]


def finish_activity(job_id: str, seq: int, status: str, detail: str | None = None) -> None:
    rows = execute(
        "update activity set status = ?, detail = ?, finished_at = ? where job_id = ? and seq = ?"
        " returning tool",
        (status, detail, now(), job_id, seq),
    )
    log.info("%s %-16s %-8s %s", job_id, rows[0]["tool"] if rows else "?", status, detail or "")


def activity(job_id: str) -> list[ActivityRow]:
    rows = execute("select * from activity where job_id = ? order by seq", (job_id,))
    return [ActivityRow(**dict(r) | {"args": json.loads(r["args"])}) for r in rows]


def tool_calls_finished(job_id: str) -> int:
    """Tool calls that ran to an end (done, skipped or failed; refusals never ran)."""
    rows = execute(
        "select count(*) from activity where job_id = ? and status in ('done', 'skipped', 'failed')",
        (job_id,),
    )
    return rows[0][0]


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
