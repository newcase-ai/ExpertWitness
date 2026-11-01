from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

JobStatus = Literal["queued", "running", "done", "failed"]
StepStatus = Literal["pending", "running", "done", "failed", "skipped"]
ActivityStatus = Literal["running", "done", "refused", "skipped", "failed"]
EvidenceSource = Literal["openalex", "courtlistener", "web"]
# Strongest first; candidates are ranked in this order (app/agents.py).
ExpertSignal = Literal["court_opinion_expert", "expert_directory", "web_expert_witness"]
NO_EXPERTS_NOTICE = "No expert-witness evidence found for this name."
NO_EXPERT_EVIDENCE_NOTE = "No expert-witness evidence found"  # on each candidate without a signal
SEARCH_SOURCES = frozenset({"openalex", "courtlistener", "web_search"})
SourceType = Literal[
    "courtlistener_opinion", "courtlistener_docket", "courtlistener_search", "openalex_author", "openalex_work",
    "crossref_work", "oig_leie", "web",
]


# Requests


class Hints(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    field: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    affiliation: str | None = Field(default=None, max_length=200)


class CandidateSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    hints: Hints | None = None

    @field_validator("name")
    @classmethod
    def check_name(cls, name: str) -> str:
        name = " ".join(name.split())
        if not 2 <= len(name) <= 120:
            raise ValueError("name must be 2-120 characters")
        if not any(c.isalpha() for c in name):
            raise ValueError("name must contain a letter")
        return name


# Candidates


class Evidence(BaseModel):
    source: EvidenceSource
    url: str
    title: str
    note: str | None = None


class Identifiers(BaseModel):
    openalex: list[str] = []
    orcid: str | None = None


class Candidate(BaseModel):
    candidate_id: str = ""  # assigned by app/jobs.py when the search is stored
    display_name: str
    summary: str
    field: str | None = None
    affiliations: list[str] = []
    location: str | None = None
    identifiers: Identifiers = Identifiers()
    evidence: list[Evidence] = Field(min_length=1)
    match_notes: str | None = None
    expert_signals: list[ExpertSignal] = []  # set by code from evidence, never by the AI

    @computed_field
    @property
    def expert_evidence(self) -> bool:
        """False for a same-name person listed without any expert-witness evidence (shown last)."""
        return bool(self.expert_signals)

    @computed_field
    @property
    def expert_note(self) -> str | None:
        """Fixed text set by code for a candidate without expert evidence, else null."""
        return None if self.expert_signals else NO_EXPERT_EVIDENCE_NOTE


class FailedSource(BaseModel):
    source: str
    reason: str


# Responses


class CandidateSearchStarted(BaseModel):
    search_id: str
    job_id: str
    status: JobStatus


class CandidateSearch(BaseModel):
    search_id: str
    name: str
    status: JobStatus
    created_at: str
    candidates: list[Candidate] = []
    failed_sources: list[FailedSource] = []
    error: str | None = None
    notice: str | None = None

    @model_validator(mode="after")
    def no_experts_notice(self):
        """A finished search with no candidates at all says so. Not when every source failed:
        `failed_sources` already explains that."""
        all_failed = SEARCH_SOURCES <= {f.source for f in self.failed_sources}
        if self.status == "done" and not self.candidates and not all_failed:
            self.notice = NO_EXPERTS_NOTICE
        return self


class JobStep(BaseModel):
    key: str
    label: str
    status: StepStatus = "pending"
    detail: str | None = None
    finished_at: str | None = None


class Progress(BaseModel):
    done: int
    total: int


class ActivityRow(BaseModel):
    """One tool call of the main agent (DESIGN 4B.6). Written by the tool wrapper only."""

    seq: int
    tool: str
    label: str
    status: ActivityStatus
    args: dict = {}
    detail: str | None = None
    started_at: str
    finished_at: str | None = None


class TraceEvent(BaseModel):
    """One step of a research run's trace (app/trace.py): a model turn, a tool call starting or
    ending, a finding, a gap, the run starting or ending. `tool_seq`: the activity seq of the tool
    call it happened inside (nested model calls, tools calling tools)."""

    seq: int
    at: str
    kind: str
    agent: str | None = None
    tool_seq: int | None = None
    data: dict = {}


class TraceEvents(BaseModel):
    job_id: str
    events: list[TraceEvent]
    last_seq: int  # pass as `after` to get only newer events


class JobResult(BaseModel):
    kind: Literal["dossier", "candidate_search"]
    id: str


class Job(BaseModel):
    job_id: str
    kind: Literal["candidate_search", "research"]
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress: Progress
    steps: list[JobStep]
    result: JobResult | None = None
    error: str | None = None
    activity: list[ActivityRow] = []  # always [] for candidate searches


# Dossier sources (used from M3 on)


class Source(BaseModel):
    id: str
    url: str
    title: str
    source_type: SourceType
    retrieved_at: str


# Research job: raw findings saved by the tools (the expert profile), before the dossier is written
TESTIMONY_LIST_MAX = 20_000  # characters of a pasted testimony list (parsed by the AI, never stored)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    # Optional list of cases the expert testified in, pasted by the app's user. Only the cases the AI
    # parses from it are kept (as case_check findings); the text itself is never stored.
    testimony_list: str | None = Field(default=None, max_length=TESTIMONY_LIST_MAX)


class ResearchStarted(BaseModel):
    job_id: str
    dossier_id: str
    status: JobStatus


PROFILE_SECTIONS = ("identity", "expertise", "credentials", "cases", "publications", "red_flags", "not_him",
                    "conflicts", "open_leads")


class ProfileItem(BaseModel):
    text: str
    refs: list[str] = []  # the finding ids it rests on (code-checked: real findings of this run)
    confidence: Literal["high", "low"] = "low"


class Profile(BaseModel):
    """What one research run knows about the expert so far, kept up to date by AI from its findings
    (app/profile.py). `sources` (finding id -> url, title) is filled for the API only."""

    dossier_id: str
    version: int = 0
    updated_at: str | None = None
    identity: list[ProfileItem] = []  # other forms of the name, current role, employers, cities and states
    expertise: list[ProfileItem] = []  # field, specialties, typical case types
    credentials: list[ProfileItem] = []  # degrees, licences, boards and their status
    cases: list[ProfileItem] = []  # one per case: name, court, year, side, role, what happened
    publications: list[ProfileItem] = []
    red_flags: list[ProfileItem] = []  # excluded or criticised testimony, retractions, discipline
    not_him: list[ProfileItem] = []  # people with the same name ruled out
    conflicts: list[ProfileItem] = []  # findings that disagree
    open_leads: list[ProfileItem] = []  # concrete things worth searching next
    sources: dict[str, dict] = {}


class ResearchSummary(BaseModel):
    """One research run in the searches list: how it is going."""

    dossier_id: str
    display_name: str
    status: JobStatus
    error: str | None = None
    created_at: str
    finished_at: str | None = None
    tool_calls: int
    findings: int
    last_activity: str | None = None  # the latest tool call: label, status and detail


class SearchSummary(BaseModel):
    """One candidate search in the searches list, with the research runs started from it."""

    search_id: str
    name: str
    created_at: str
    status: JobStatus
    candidates: int
    research: list[ResearchSummary] = []


class Dossier(BaseModel):
    """One research run, so a page can follow it again after a reload."""

    dossier_id: str
    candidate_id: str
    job_id: str
    status: JobStatus
    display_name: str


class Finding(BaseModel):
    finding_id: str = ""  # assigned by app/store.py when saved
    # Open strings (DESIGN 4B.11 D2): `collector` is the tool name, `kind` is listed on each tool card.
    collector: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    title: str
    url: str = Field(min_length=1)
    source_type: SourceType
    retrieved_at: str = Field(min_length=1)
    confidence: Literal["high", "low"]
    facts: dict = {}
    note: str | None = None


class Gap(BaseModel):
    step: str
    reason: str


class ResearchFindings(BaseModel):
    dossier_id: str
    candidate_id: str
    job_id: str
    status: JobStatus
    findings: list[Finding] = []
    gaps: list[Gap] = []
