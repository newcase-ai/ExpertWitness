"""The main agent (DESIGN 4B.5): one ADK agent with every registered tool researches the picked
expert until it replies DONE or hits the step or time limit. Its final text is thrown away; only
what the tools saved counts."""

from app import config, llm, trace
from app.config import get_settings
from app.evidence import Run
from app.models import Candidate
from app.tools import REGISTRY, Ctx
from app.tools.courtlistener import gap_unjudged

INSTRUCTIONS = """You research one expert witness for a lawyer who will depose or cross-examine them,
or who may hire them. Find as much as you can about their work as an expert: every case and
deposition they testified in, which side hired them (plaintiff or defense, and how much of their
work is each), how much they are paid (hourly rates, fees for reports, depositions and trial),
rulings on their testimony, and red flags (testimony excluded or limited, discipline, licence
problems, exclusions, retractions, lawsuits about their work). Also their publications, licences
and credentials. Use only your tools; every fact comes from a tool. Start with publications, the
exclusion check and web search, then federal court records (below) and the retraction check, then
follow leads. Professional records
only: never private life, family, home address or health. When a tool says a limit is reached, stop
using it. When a tool fails, read its error: it says what happened and whether calling again can
help (a slow or busy source). If it can, call that tool again once a little later; if not, move on.

A profile of what we know so far is kept up to date automatically after every tool that saves
findings. Read it (read_profile) before asking a file or choosing what to search next, and follow its
open leads. When you are not sure a result is about this expert, check it (check_against_profile):
a shared name is not proof.

Federal court records (CourtListener): court_research does this work for you. Its helper searches,
sorts out people with the same name, reads the filings and saves each case it judges; it replies
with the cases, the files it downloaded and leads to follow. Call it once early with no focus, and
again with `focus` naming federal cases you found elsewhere (a CV, a web page), giving in `context`
what you know that is not in the profile yet (e.g. the CV's case list and the expert's side in each).

Download each PDF worth reading (a CV, a deposition or trial transcript, an expert report, a
court filing, a fee schedule) with download_file. Every new file then gets read with ask_pdf, about
100 pages at a time from page 1. In each call's context, give what we know so far (from
read_profile): the expert's name, field and affiliations, the cases, employers, licences and dates
already found, and what earlier pages of this file held. Ask: is there anything new in these pages
about the expert's professional work (cases, depositions, which side hired them, fees, red flags);
does it confirm, add to or contradict what we have; and are there clues for something new to search
for (case names and numbers, courts, states, dates, parties, lawyers, other forms of the name,
employers, licences)? Go on to the next pages while the file keeps giving something new.

Use every new piece of information, from a file or any other tool, to find more. For a case name
or number, look the case up: court_research for a federal case, the state's court sites (below) for
a state case. Then search the web and the court's own site (firecrawl_search, web_search) for where
and when it was heard, the parties, the expert's side and role, rulings on their testimony, fees,
transcripts and news. For a state where the expert testified, worked or holds a licence, find that
state's court, licence and discipline sites (sites_for_state), learn how to search each one you
need (site_guide, with its `state`; wait for its reply before searching that site) and search them
for other cases and records about the expert (firecrawl_search with `sites`). For another form of the name, an employer, a law firm, a lawyer or a party, search
with it. Reply DONE when nothing new is likely."""


def stop_detail(stopped: str | None) -> str:
    """The step detail when the agent stops: why it stopped."""
    if stopped == "time_limit":
        return f"time limit ({config.RESEARCH_SECONDS / 60:g} min)"
    return "step limit" if stopped == "step_limit" else "agent finished"


def first_message(expert: Candidate, testimony_list: str | None) -> str:
    """The expert block, the user's pasted case list (in memory only, never stored) and the tools."""
    lines = [f"Expert: {expert.display_name}"]
    lines += [f"{label}: {value}" for label, value in (
        ("Field", expert.field), ("Location", expert.location),
        ("Affiliations", "; ".join(expert.affiliations)),
        ("OpenAlex ids", ", ".join(expert.identifiers.openalex)), ("ORCID", expert.identifiers.orcid),
    ) if value]
    lines += ["", "Evidence from the candidate search:"]
    lines += [f"- {e.title} ({e.url})" for e in expert.evidence]
    if testimony_list:
        lines += ["", "Cases the user listed (pasted text, not checked):", testimony_list]
    lines += ["", "Tools:"] + [f"- {name}: {t.label}" for name, t in REGISTRY.items() if t.main]
    return "\n".join(lines)


async def research(job_id: str, dossier_id: str, expert: Candidate,
                   testimony_list: str | None = None) -> llm.AgentRun:
    """Run the main agent once. Raises llm.ModelError when the model fails."""
    caps = {name: t.cap * config.TOOL_CAP_SCALE for name, t in REGISTRY.items()}
    run = Run(seconds=config.RESEARCH_SECONDS, caps=caps)
    ctx = Ctx(job_id=job_id, dossier_id=dossier_id, expert=expert, run=run)
    agent = llm.make_agent("main_agent", INSTRUCTIONS,
                           tools=[ctx.adk_tool(name) for name, t in REGISTRY.items() if t.main])
    message = first_message(expert, testimony_list)
    # the pasted testimony list is never stored, except in a DEBUG_TRACE run
    shown = message if trace.full() or not testimony_list else first_message(
        expert, f"[testimony list: {len(testimony_list)} characters, not stored]")
    trace.record("run_start", "main_agent", model=get_settings().model_name, seconds=config.RESEARCH_SECONDS,
                 max_model_calls=config.MAX_AGENT_CALLS, caps=caps, tools=list(REGISTRY),
                 instructions=INSTRUCTIONS, message=shown)
    try:
        return await llm.run_agent(agent, message, max_llm_calls=config.MAX_AGENT_CALLS, timeout=run.left())
    finally:  # DONE, a limit or a model error: report court cases read but never judged
        gap_unjudged(ctx)
