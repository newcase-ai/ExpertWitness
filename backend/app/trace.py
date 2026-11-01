"""Run trace: an ordered record of everything a research job does, for testing and debugging. Model
turns (text, reasoning, the tool calls asked for, tokens), tool calls (full arguments, reply, time,
error), findings, gaps, and the run starting and ending. Stored in the `events` table and served by
GET /jobs/{id}/events; a one-line summary of each also goes to the `app.agent` console log.

`start(job_id)` turns the trace on for one job (a ContextVar, so every model call inside the job is
recorded, also the ones inside tools); outside a job `record` does nothing.

Content rules. Always: strings that look like secrets become "[hidden]" (gotcha 45).
Safe mode (default): tool replies and nested model text (page text can be in them, gotcha 15) are cut
to PREVIEW characters; the pasted testimony list is not stored; errors keep type and message only.
DEBUG_TRACE=1: everything in full, tracebacks too. For local testing only, never on a shared server."""

import logging
import traceback
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from app import memory, store
from app.config import get_settings

PREVIEW = 300  # characters of a tool reply string or nested model text kept in safe mode
ERROR_MAX = 300
LOG_TEXT = 120  # characters of model text in a console line
HIDDEN = "[hidden]"
log = logging.getLogger("app.agent")


@dataclass
class State:
    job_id: str
    full: bool  # DEBUG_TRACE
    turns: int = 0
    tokens: dict = field(default_factory=lambda: {"in": 0, "out": 0})


TRACE: ContextVar[State | None] = ContextVar("trace", default=None)
TOOL: ContextVar[int | None] = ContextVar("trace_tool", default=None)  # activity seq of the tool call running


def start(job_id: str) -> Token:
    return TRACE.set(State(job_id, get_settings().debug_trace))


def stop(token: Token) -> None:
    TRACE.reset(token)


def full() -> bool:
    state = TRACE.get()
    return bool(state and state.full)


def clean(value, cut: int | None):
    """Strings (nested too) with secrets hidden and, if `cut`, cut to that length with their size."""
    if isinstance(value, str):
        if memory.looks_secret(value, url=value.startswith(("http://", "https://"))):
            return HIDDEN
        return value if cut is None or len(value) <= cut else f"{value[:cut]}… [{len(value)} chars]"
    if isinstance(value, dict):
        return {str(k): clean(v, cut) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v, cut) for v in value]
    return value if value is None or isinstance(value, bool | int | float) else clean(str(value), cut)


def error(e: BaseException) -> dict:
    """An exception for the trace: type and message; the traceback in debug mode only."""
    out = {"type": type(e).__name__, "message": str(e)[:ERROR_MAX]}
    if full():
        out["traceback"] = "".join(traceback.format_exception(e))
    return out


def record(kind: str, agent: str | None = None, /, *, cut: int | None = None, **data) -> None:
    """Save one event of the running job (no-op outside a job); `cut` applies in safe mode only."""
    state = TRACE.get()
    if state is None:
        return
    data = clean(data, None if state.full else cut) | {"debug": state.full}
    try:
        store.add_event(state.job_id, kind, agent, TOOL.get(), data)
    except Exception:  # the trace must never break the run
        log.exception("%s trace event %s not saved", state.job_id, kind)
        return
    if line := summary(kind, agent, data):
        log.info("%s %-10s %s", state.job_id, kind, line)


def summary(kind: str, agent: str | None, d: dict) -> str | None:
    """The console line; tool calls are already logged by the activity rows (store.py)."""
    if kind == "model_turn":
        calls = ", ".join(c["name"] for c in d.get("tool_calls") or [])
        text = " ".join((d.get("text") or "").split())[:LOG_TEXT]
        tokens = d.get("tokens") or {}
        return f"{agent} tokens {tokens.get('in')}/{tokens.get('out')}" + (f" calls: {calls}" if calls else "") \
            + (f" says: {text}" if text else "")
    if kind == "finding":
        return f"{d.get('kind')} {d.get('confidence')} {str(d.get('title'))[:LOG_TEXT]} {d.get('url')}"
    if kind == "gap":
        return f"{d.get('step')}: {d.get('reason')}"
    if kind in ("run_start", "run_end"):
        return " ".join(f"{k}={v}" for k, v in d.items() if k in ("model", "stopped", "turns", "tokens", "error"))
    return None


def model_turn(agent: str, event) -> None:
    """One model answer inside the running job: its text, reasoning, tool calls and tokens."""
    state = TRACE.get()
    if state is None or not event.content:
        return
    parts = event.content.parts or []
    calls = [{"name": p.function_call.name, "args": dict(p.function_call.args or {})}
             for p in parts if p.function_call]
    text = "".join(p.text for p in parts if p.text and not p.thought)
    thought = "".join(p.text for p in parts if p.text and p.thought)
    usage = event.usage_metadata
    tokens = {"in": usage.prompt_token_count or 0, "out": usage.candidates_token_count or 0} if usage else None
    if not (calls or text or thought or tokens):
        return
    state.turns += 1
    if tokens:
        state.tokens["in"] += tokens["in"]
        state.tokens["out"] += tokens["out"]
    nested = TOOL.get() is not None  # a model call inside a tool reads page or file text
    record("model_turn", agent, cut=PREVIEW if nested else None, text=text, thought=thought, tool_calls=calls,
           tokens=tokens, final=event.is_final_response(),
           error=getattr(event, "error_message", None) or None)


def tool_errors(agent: str, event) -> None:
    """Error replies the model got for its tool calls. Most are our own refusals (also in tool_end);
    this also catches calls ADK rejects before any tool runs, such as a missing argument."""
    for response in event.get_function_responses() or []:
        reply = response.response
        if isinstance(reply, dict) and "error" in reply:
            record("tool_error", agent, cut=PREVIEW, tool=response.name, error=reply["error"])


def totals() -> dict:
    state = TRACE.get()
    return {"turns": state.turns, "tokens": dict(state.tokens)} if state else {}
