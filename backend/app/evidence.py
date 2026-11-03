"""Shared by the agents (DESIGN 4A.4-4A.5): copy-check helpers and the per-run evidence store.
Imports only `app.sources`, so candidate_agent, tools and websearch can all import it (no cycle)."""

import asyncio
import functools
import inspect
import re
import time
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import TypeAdapter

from app.sources import fold

COPY_RUN = 40  # an AI field sharing this many characters in a row with a snippet is dropped
TITLE_MAX = 200  # characters of a title kept in the evidence store
# Generic words never count as a shared field word ("Orthopedic Surgery" must not match "Plastic Surgery").
STOPWORDS = frozenset({
    "and", "the", "for", "with", "usa", "of", "inc", "llc", "llp", "pllc", "ltd", "pc", "md", "university",
    "medicine", "medical", "center", "centre", "clinic", "hospital", "health", "care", "group",
    "institute", "college", "school", "department", "associates", "services", "practice", "company",
    "new", "saint", "st", "fort", "surgery",
})
# Fixed tool replies to the model (never source or provider text).
LIMIT_REACHED = "limit reached: give your final answer"
TOOL_FAILED = "source call failed"
BAD_ARGUMENTS = "bad arguments: check the tool's parameters and types"


# AI-written text: hard length caps, and nothing copied from a source snippet (gotcha 15).


def squash(text: str) -> str:
    return " ".join(text.lower().split())


def copied(text: str, snippets: str, run: int = COPY_RUN) -> bool:
    """True if `text` holds a run of `run`+ characters found word for word in a snippet."""
    t = squash(text)
    return any(t[i:i + run] in snippets for i in range(len(t) - run + 1))


def words(text: str | None) -> set[str]:
    """Specialty words: folded, 3+ letters, no generic words."""
    return {w for w in re.findall(r"[a-z0-9]+", fold(text or "")) if len(w) >= 3 and w not in STOPWORDS}


def checked_args(fn: Callable, args: tuple, kwargs: dict) -> dict:
    """The model's arguments bound to `fn` and checked against its type hints (pydantic lax mode:
    "2019" passes as int, 3 does not pass as str). Raises TypeError or ValueError when wrong."""
    hints = typing.get_type_hints(fn)
    bound = inspect.signature(fn).bind(*args, **kwargs).arguments
    return {name: TypeAdapter(hints[name]).validate_python(value) if name in hints else value
            for name, value in bound.items()}


def refuse(text: str) -> dict:
    """A tool's answer when it will not run: fixed text the model can read."""
    return {"error": text}


@dataclass
class Run:
    """One agent run, in memory only: the evidence store, the time budget, per-tool caps and the
    tool failures. A tool not named in `caps` is never run."""

    seconds: float
    caps: dict[str, int] = field(default_factory=dict)
    prefix: str = "E"
    started: float = field(default_factory=time.monotonic)
    evidence: dict[str, dict] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (tool, exception type)
    count: int = 0
    # CourtListener cases (docket_id -> case) and filings (document id -> filing, its docket_id and the
    # file_id once downloaded) the court tools showed the agent: their ids are valid in later calls.
    cases: dict[int, dict] = field(default_factory=dict)
    filings: dict[int, dict] = field(default_factory=dict)
    # court_search paging: (words, case_name) -> {"queue": hits not shown yet, "next": link, "total": n}
    searches: dict[tuple[str, str], dict] = field(default_factory=dict)

    def left(self) -> float:
        """Seconds left in the budget (0 or less: stop)."""
        return self.seconds - (time.monotonic() - self.started)

    def add(self, hit: dict, ref: str | None = None) -> str:
        """Store a hit under `ref`, or a code-made id (E1, E2, ...); the title is capped."""
        if ref is None:
            self.count += 1
            ref = f"{self.prefix}{self.count}"
        self.evidence[ref] = hit | {"title": (hit.get("title") or "")[:TITLE_MAX]}
        return ref

    def get(self, ref: str) -> dict | None:
        return self.evidence.get(ref)

    def has_url(self, url: str) -> bool:
        return any(hit.get("url") == url for hit in self.evidence.values())

    def check(self, tool: str) -> dict | None:
        """None if `tool` may run now (the call is counted); otherwise the refusal for the model."""
        if self.left() <= 0 or self.used.get(tool, 0) >= self.caps.get(tool, 0):
            return refuse(LIMIT_REACHED)
        self.used[tool] = self.used.get(tool, 0) + 1
        return None

    def tool(self, fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        """Wrap an async tool for ADK (`FunctionTool(run.tool(fn))`): checks the arguments, its cap and
        the time left, stops at the deadline, and never raises to the model. Bad arguments are the
        model's mistake (refused, cap not used); any other error is a source failure in `failures`."""

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> dict:
            try:
                arguments = checked_args(fn, args, kwargs)
            except (TypeError, ValueError):
                return refuse(BAD_ARGUMENTS)
            if refusal := self.check(fn.__name__):
                return refusal
            try:
                async with asyncio.timeout(self.left()):
                    return await fn(**arguments)
            except Exception as e:  # noqa: BLE001 - a tool never raises to the model; the type is kept
                self.failures.append((fn.__name__, type(e).__name__))
                return refuse(TOOL_FAILED)

        return wrapper
