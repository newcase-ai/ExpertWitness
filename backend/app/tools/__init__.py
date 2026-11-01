"""Main agent tools (DESIGN 4B.2-4B.3): the registry, the one wrapper every tool call goes through,
and `Ctx`, what a tool gets. Adding a tool = one new file in this folder (imported at the bottom of
this module) + its test; no framework file changes."""

import asyncio
import dataclasses
import importlib
import inspect
import json
import mimetypes
import pkgutil
import time
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app import llm, memory, profile, store, trace
from app.config import get_settings
from app.evidence import (
    BAD_ARGUMENTS,
    LIMIT_REACHED,
    TOOL_FAILED,
    Run,
    checked_args,
    copied,
    refuse,
    squash,
)
from app.models import PROFILE_SECTIONS, Candidate, Finding, SourceType
from app.sources import SourceFailed, SourceSkipped, host_of, is_blocked, on_domain

RESULT_MAX = 8_000  # characters of a tool reply as JSON
TITLE_MAX = 200
FACTS_MAX = 4_000  # characters of a finding's facts as JSON
DETAIL_MAX = 200  # characters of an activity detail (ctx.log)
ARG_MAX = 100  # characters of each string argument kept in an activity row
ANSWER_MAX = 500  # characters of each string in an ask_model answer (unless the schema says less)
FILE_MAX = 20 * 1024 * 1024  # bytes per saved file
FILES_PER_JOB = 30
FILE_TEXT_MAX = 60_000
# Read through their own source clients and tools, never fetched as pages (gotchas 5, 11, 40).
API_HOSTS = ("courtlistener.com", "openalex.org", "crossref.org", "oig.hhs.gov")
# Fixed texts (never source or model text).
NOT_HTTP = "only http(s) links can be read"
BANNED_SITE = "this site must not be read"
API_SITE = "this site is read through its own tool, not as a page"
REPLY_TOO_LONG = "tool reply too long"
UNKNOWN_TOOL = "unknown tool"
SECRET_DROPPED = "finding dropped: looks like a secret"
HIDDEN = "[hidden]"


@dataclass(frozen=True)
class Tool:
    fn: Callable[..., Awaitable[dict]]
    label: str
    cap: int


REGISTRY: dict[str, Tool] = {}


def tool(label: str, cap: int):
    """Register an async `fn(ctx, ...) -> dict` as a main agent tool. The function name is the tool
    name and must be unique across app/tools/ (gotcha 44)."""

    def register(fn):
        if fn.__name__ in REGISTRY:
            raise ValueError(f"duplicate tool name: {fn.__name__}")
        if "name" in inspect.signature(fn).parameters:
            raise ValueError("a tool argument must not be called `name` (gotcha 36)")
        REGISTRY[fn.__name__] = Tool(fn, label, cap)
        return fn

    return register


def model_view(name: str, call: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    """`call` dressed as the tool for ADK: its name, docstring and arguments, without `ctx`. ADK reads
    `__signature__` and `__annotations__` (gotcha 34; checked offline in the framework tests)."""
    fn = REGISTRY[name].fn
    hints = typing.get_type_hints(fn)
    params = list(inspect.signature(fn).parameters.values())[1:]
    call.__name__ = call.__qualname__ = name
    call.__doc__ = fn.__doc__
    call.__signature__ = inspect.signature(fn).replace(parameters=params)
    call.__annotations__ = {k: v for k, v in hints.items() if k != "ctx"}
    return call


def strings(value) -> list[str]:
    """Every string inside a value (nested dicts and lists too)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in strings(v)]
    return []


def secret(text: str) -> bool:
    return memory.looks_secret(text, url=text.startswith(("http://", "https://")))


def hide(text: str | None) -> str | None:
    """Activity text: HIDDEN if it looks like a secret (gotcha 45)."""
    return HIDDEN if text and secret(text) else text


def short_args(value):
    """Arguments for an activity row: strings (nested too) cut to ARG_MAX, secrets HIDDEN."""
    if isinstance(value, str):
        return hide(value)[:ARG_MAX]
    if isinstance(value, dict):
        return {k: short_args(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [short_args(v) for v in value]
    return value


def source_type_of(url: str) -> str:
    """Code-made source type of a finding's URL."""
    host = host_of(url) or ""
    if on_domain(host, ("courtlistener.com",)):
        if "/docket/" in url:
            return "courtlistener_docket"
        return "courtlistener_opinion" if "/opinion/" in url else "courtlistener_search"
    if on_domain(host, ("openalex.org",)):
        return "openalex_work" if "/W" in url else "openalex_author"
    if on_domain(host, ("crossref.org",)):
        return "crossref_work"
    if on_domain(host, ("oig.hhs.gov",)):
        return "oig_leie"
    return "web"


def clean_answer(value, schema: dict | None, input: str):
    """Every string copying COPY_RUN+ characters of `input` dropped, the rest cut to ANSWER_MAX or the
    schema's maxLength."""
    schema = schema or {}
    if isinstance(value, str):
        return None if copied(value, input) else value[:schema.get("maxLength", ANSWER_MAX)]
    if isinstance(value, dict):
        props = schema.get("properties", {})
        out = {k: clean_answer(v, props.get(k), input) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not None}
    if isinstance(value, list):
        out = [clean_answer(v, schema.get("items"), input) for v in value]
        return [v for v in out if v is not None]
    return value


@dataclass
class Ctx:
    """What a tool gets (DESIGN 4B.3). One per research job; the wrapper gives each call a copy with
    `tool` set, so `log` and `add_finding` know which call they belong to."""

    job_id: str
    dossier_id: str
    expert: Candidate
    run: Run
    tool: str = ""
    detail: str | None = None

    def time_left(self) -> float:
        return self.run.left()

    # The profile (memory 1)

    def add_finding(self, kind: str, title: str, url: str, facts: dict | None = None,
                    confidence: str = "low", note: str | None = None,
                    source_type: SourceType | None = None) -> str | None:
        """Save one finding now and return its ref (`finding_id`). None (not saved) for a URL that is
        not http(s), is on a gotcha 2 site, or a title, note or fact that looks like a secret (gotcha
        45; noted in a gap). `source_type` defaults to one made from the URL."""
        if not url.startswith(("http://", "https://")) or is_blocked(url):
            return None
        facts = facts or {}
        if any(secret(s) for s in [title, note or "", *strings(facts)]):
            self.add_gap(SECRET_DROPPED)
            self.log(SECRET_DROPPED)
            return None
        if len(json.dumps(facts)) > FACTS_MAX:
            raise ValueError("finding facts too long")
        finding = Finding(collector=self.tool or "main_agent", kind=kind, title=title[:TITLE_MAX], url=url,
                          source_type=source_type or source_type_of(url), retrieved_at=store.now(),
                          confidence=confidence, facts=facts, note=note)
        ref = store.add_finding(self.dossier_id, finding)
        trace.record("finding", cut=trace.PREVIEW, ref=ref, kind=kind, title=finding.title, url=url,
                     confidence=confidence, collector=finding.collector, note=note, facts=facts)
        return ref

    def add_gap(self, reason: str) -> None:
        """Save a gap for this tool. Fixed or code-made text only."""
        store.add_gap(self.dossier_id, self.tool or "main_agent", reason)
        trace.record("gap", step=self.tool or "main_agent", reason=reason)

    def findings(self, kind: str | None = None) -> list[Finding]:
        return [f for f in store.findings(self.dossier_id) if kind is None or f.kind == kind]

    # Fetching and files

    def fetch_ok(self, url: str) -> str | None:
        """None if a tool may fetch `url`; otherwise a fixed refusal text."""
        host = host_of(url) if url.startswith(("http://", "https://")) else None
        if not host:
            return NOT_HTTP
        if is_blocked(url):
            return BANNED_SITE
        if on_domain(host, API_HOSTS):
            return API_SITE
        if reason := memory.blocked(host):
            return f"blocked site: {reason}"
        return None

    def save_file(self, url: str, data: bytes, mime: str, text: str | None = None) -> str:
        """Store downloaded bytes (and a text version, if given) under data/files/<dossier_id>/ and
        return the new file_id. Raises ValueError for a refused URL, a file over FILE_MAX or more than
        FILES_PER_JOB files."""
        if refusal := self.fetch_ok(url):
            raise ValueError(refusal)
        if len(data) > FILE_MAX:
            raise ValueError("file too large")
        if len(memory.files(self.dossier_id)) >= FILES_PER_JOB:
            raise ValueError("file limit reached for this job")
        file_id = store.new_id("file")
        folder = Path(get_settings().database_path).parent / "files" / self.dossier_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{file_id}{mimetypes.guess_extension(mime) or '.bin'}"
        path.write_bytes(data)
        text_path = None
        if text is not None:
            text_path = folder / f"{file_id}.md"
            text_path.write_text(text)
        memory.add_file(file_id, self.dossier_id, url, str(path), text_path and str(text_path), mime,
                        len(data))
        return file_id

    def file_text(self, file_id: str, max_chars: int = FILE_TEXT_MAX) -> str:
        """The text version of a saved file of this dossier ("" if none)."""
        row = memory.file(file_id)
        if not row or row["dossier_id"] != self.dossier_id or not row["text_path"]:
            return ""
        return Path(row["text_path"]).read_text()[:max_chars]

    # The model

    async def ask_model(self, instructions: str, input: str, schema: dict | None = None):
        """One model call. Returns the parsed JSON (with `schema`) or the text, copy-checked against
        `input` and cut (see clean_answer). Use it for any model-written text that will be stored."""
        result = await llm.call_model(instructions, input, schema=schema)
        answer = clean_answer(result.data if schema else result.text, schema, squash(input))
        return "" if answer is None else answer

    async def sub_agent(self, instructions: str, prompt: str, tools: list[str] = (),
                        max_llm_calls: int = 6) -> llm.AgentRun:
        """A small ADK agent whose `tools` (registered tool names) go through this same wrapper."""
        agent = llm.make_agent(f"{self.tool or 'main'}_helper", instructions,
                               tools=[self.adk_tool(t) for t in tools])
        return await llm.run_agent(agent, prompt, max_llm_calls=max_llm_calls, timeout=self.time_left())

    # Calls

    def log(self, text: str) -> None:
        """This call's activity detail. Code-made text only (counts, reasons), never page or model text."""
        self.detail = hide(text)[:DETAIL_MAX]

    def adk_tool(self, name: str) -> Callable[..., Awaitable[dict]]:
        """Tool `name` for ADK: the model sees its arguments without `ctx`; calls go through call_tool."""

        async def call(**args) -> dict:
            return await self.call_tool(name, **args)

        return model_view(name, call)

    async def call_tool(self, tool_name: str, **args) -> dict:
        """Run a registered tool through the wrapper (DESIGN 4B.2 table): arguments, cap, clock, one
        activity row, never raises."""
        entry = REGISTRY.get(tool_name)
        if entry is None:
            return refuse(UNKNOWN_TOOL)
        self.run.caps.setdefault(tool_name, entry.cap)
        try:
            arguments = checked_args(model_view(tool_name, lambda: None), (), args)
        except (TypeError, ValueError):
            s = store.start_activity(self.job_id, tool_name, entry.label, {}, "refused", "bad arguments")
            trace.record("tool_end", cut=trace.PREVIEW, seq=s, tool=tool_name, status="refused",
                         detail="bad arguments", args=args)  # the model's arguments as sent
            return refuse(BAD_ARGUMENTS)
        shown = short_args(arguments)
        if refusal := self.run.check(tool_name):
            why = "time limit" if self.run.left() <= 0 else "cap used up"
            s = store.start_activity(self.job_id, tool_name, entry.label, shown, "refused", why)
            trace.record("tool_end", seq=s, tool=tool_name, status="refused", detail=why, args=arguments)
            return refusal
        seq = store.start_activity(self.job_id, tool_name, entry.label, shown)
        trace.record("tool_start", seq=seq, tool=tool_name, label=entry.label, args=arguments)
        started, inside = time.monotonic(), trace.TOOL.set(seq)  # model calls inside link to this call
        before = len(self.findings()) if profile.AUTO_UPDATE else None
        call = dataclasses.replace(self, tool=tool_name, detail=None)
        status, detail, reply, size, err = "done", None, None, None, None
        try:
            async with asyncio.timeout(self.run.left()) as clock:
                reply = await entry.fn(call, **arguments)
            if not isinstance(reply, dict):
                raise TypeError("tool reply is not a dict")
            size = len(json.dumps(reply))
        except TimeoutError as e:
            err = e
            if not clock.expired():
                status, detail, reply = "failed", type(e).__name__, refuse(TOOL_FAILED)
                call.add_gap(f"{entry.label} failed ({type(e).__name__})")
            else:
                status, detail, reply = "failed", "time limit", refuse(LIMIT_REACHED)
        except SourceSkipped as e:
            err = e
            status, detail, reply = "skipped", str(e)[:DETAIL_MAX], refuse(f"source not available: {e}")
            call.add_gap(str(e))
        except SourceFailed as e:  # fixed text from the source: what happened, whether to call again
            err = e
            self.run.failures.append((tool_name, type(e).__name__))
            status, detail, reply = "failed", str(e)[:DETAIL_MAX], refuse(f"{TOOL_FAILED}: {e}")
            call.add_gap(f"{entry.label} failed: {e}")
        except asyncio.CancelledError:  # the job's own deadline stopped the agent mid-call
            store.finish_activity(self.job_id, seq, "failed", "time limit")
            trace.record("tool_end", seq=seq, tool=tool_name, status="failed", detail="time limit",
                         ms=round((time.monotonic() - started) * 1000))
            trace.TOOL.reset(inside)
            raise
        except Exception as e:  # noqa: BLE001 - a tool never raises to the model; only the type is kept
            err = e
            self.run.failures.append((tool_name, type(e).__name__))
            status, detail, reply = "failed", type(e).__name__, refuse(TOOL_FAILED)
            call.add_gap(f"{entry.label} failed ({type(e).__name__})")
        else:
            if size > RESULT_MAX:
                status, detail, reply = "failed", "reply too long", refuse(REPLY_TOO_LONG)
            elif set(reply) == {"error"}:  # the tool refused: refuse(text)
                status, detail = "refused", str(reply["error"])[:DETAIL_MAX]
            else:
                detail = call.detail
        store.finish_activity(self.job_id, seq, status, hide(detail))
        trace.record("tool_end", cut=trace.PREVIEW, seq=seq, tool=tool_name, status=status, detail=detail,
                     ms=round((time.monotonic() - started) * 1000), reply=reply, reply_chars=size,
                     error=trace.error(err) if err else None)
        trace.TOOL.reset(inside)
        if before is not None and (new := self.findings()[before:]):  # findings come back in saved order
            await self.update_profile(new)
        store.set_step(self.job_id, "main_agent", "running",
                       f"{store.tool_calls_finished(self.job_id)} tool calls")
        return reply

    async def update_profile(self, new: list[Finding]) -> None:
        """The automatic profile update after a tool call saved `new` findings: its own activity row;
        a failure is a gap (the old profile stays), never raised to the tool or the model."""
        seq = store.start_activity(self.job_id, "update_profile", "Update profile", {"new_findings": len(new)})
        trace.record("tool_start", seq=seq, tool="update_profile", label="Update profile",
                     args={"new_findings": [f.finding_id for f in new]})
        started, inside = time.monotonic(), trace.TOOL.set(seq)
        try:
            async with asyncio.timeout(max(self.run.left(), 0)):
                updated = await profile.update(self.dossier_id, self.expert, new,
                                               {f.finding_id for f in self.findings()})
        except asyncio.CancelledError:
            store.finish_activity(self.job_id, seq, "failed", "time limit")
            trace.TOOL.reset(inside)
            raise
        except Exception as e:  # noqa: BLE001 - the profile must never break a tool call
            store.finish_activity(self.job_id, seq, "failed", type(e).__name__)
            trace.record("tool_end", seq=seq, tool="update_profile", status="failed", detail=type(e).__name__,
                         ms=round((time.monotonic() - started) * 1000), error=trace.error(e))
            trace.TOOL.reset(inside)
            dataclasses.replace(self, tool="update_profile").add_gap(f"profile not updated ({type(e).__name__})")
            return
        items = sum(len(getattr(updated, s)) for s in PROFILE_SECTIONS)
        detail = f"version {updated.version}, {len(new)} new findings, {items} items"
        store.finish_activity(self.job_id, seq, "done", detail)
        trace.record("tool_end", seq=seq, tool="update_profile", status="done", detail=detail,
                     ms=round((time.monotonic() - started) * 1000))
        trace.TOOL.reset(inside)


# Discovery: every module in this folder registers its tools on import.
for _module in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_module.name}")
