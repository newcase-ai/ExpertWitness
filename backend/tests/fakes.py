"""Test fakes. SYNTHETIC people only (gotcha 8): never paste real API responses about real people."""

import asyncio
import copy
import inspect

import httpx
import pytest
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types
from pydantic import Field

from app import http, jobs, llm, store
from app.evidence import Run
from app.models import Candidate
from app.sources import courtlistener, crossref, firecrawl, leie, openalex, websearch
from app.tools import REGISTRY, Ctx, tool

NAME = "Alex Rivera"


def author(aid, name, orcid=None, alternatives=(), institution=None, topic=None, works=1):
    return {
        "id": f"https://openalex.org/{aid}",
        "display_name": name,
        "display_name_alternatives": list(alternatives),
        "orcid": f"https://orcid.org/{orcid}" if orcid else None,
        "last_known_institutions": [{"display_name": institution}] if institution else [],
        "topics": [{"display_name": topic}] if topic else [],
        "works_count": works,
    }


OPENALEX_AUTHORS = {"results": [
    author("A0000000001", "Alex J. Rivera", "0000-0000-0000-0001", ["A. J. Rivera"],
           "Example State University", "Toxicology", 92),
    author("A0000000002", "Rivera Alex", None, [], None, "Toxicology", 2),
    author("A0000000003", "Alex Rivera", "0000-0000-0000-0003", [], "Example Institute of Economics",
           "Labor Economics", 40),
    author("A0000000009", "Morgan Q. Example", None, ["M. Example"], "Example College", "Botany", 5),
]}

def opinion(oid):
    return {
        "caseName": "Doe v. Example Co.",
        "court": "District Court, N.D. Example",
        "dateFiled": "2019-04-02",
        "absolute_url": f"/opinion/{oid}/doe-v-example-co/",
        "opinions": [{"id": oid, "snippet": "SYNTHETIC-CL-SNIPPET the plaintiff's expert <mark>Alex Rivera</mark>"}],
    }


# The same case twice (two opinion records): the candidate finder keeps only opinion 1.
COURTLISTENER_SEARCH = {"results": [opinion(2), opinion(1)]}

WEB_RESULTS = [
    {"type": "text_result", "url": "https://example.edu/faculty/rivera", "title": "Toxicology expert witness",
     "snippet": "SYNTHETIC-WEB-SNIPPET Alex J. Rivera, professor of toxicology"},
    {"type": "text_result", "url": "https://www.linkedin.com/in/alex-rivera", "title": "LinkedIn",
     "snippet": "SYNTHETIC-WEB-SNIPPET blocked"},
    {"type": "text_result", "url": "https://scholar.google.co.uk/citations?user=x", "title": "Scholar",
     "snippet": "SYNTHETIC-WEB-SNIPPET blocked"},
    {"type": "text_result", "url": "https://example.edu/faculty/rivera", "title": "Faculty page (dup)",
     "snippet": "SYNTHETIC-WEB-SNIPPET duplicate"},
]

# Returned for the bare-name query: an expert-directory page, a non-expert page, a repeat.
WEB_NAME_RESULTS = [
    {"type": "text_result", "url": "https://www.jurispro.com/profile/0000-alex-rivera",
     "title": "Alex Rivera - Economics", "snippet": "SYNTHETIC-WEB-SNIPPET directory profile"},
    {"type": "text_result", "url": "https://example.org/clinic/alex-rivera",
     "title": "Alex Rivera, DC - Example Clinic", "snippet": "SYNTHETIC-WEB-SNIPPET chiropractor"},
    {"type": "text_result", "url": "https://example.edu/faculty/rivera", "title": "Faculty page",
     "snippet": "SYNTHETIC-WEB-SNIPPET repeat"},
]

# Hit order seen by the AI: E1-E3 OpenAlex (A..9 is filtered out by name), E4 CourtListener,
# E5 web (expert query), E6 directory, E7 clinic (name-only query).
GROUPING = {"people": [
    {"display_name": "Alex J. Rivera", "summary": "Toxicologist at Example State University.",
     "field": "toxicology", "affiliations": ["Example State University"], "location": "Ohio, US",
     "evidence": [{"ref": "E1", "note": "92 works in toxicology"}, {"ref": "E2", "note": None},
                  {"ref": "E4", "note": "named as plaintiff's expert"},
                  {"ref": "E5", "note": "faculty page lists toxicology"}, {"ref": "E99", "note": "made up"}],
     "match_notes": "Two OpenAlex records look alike."},
    {"display_name": "Alex Rivera", "summary": "Labor economist.", "field": "economics",
     "affiliations": [], "location": None, "evidence": [{"ref": "E3", "note": None},
                                                       {"ref": "E6", "note": "directory profile"}],
     "match_notes": None},
    {"display_name": "Alex Rivera", "summary": "Chiropractor.", "field": "chiropractic",
     "affiliations": [], "location": None, "evidence": [{"ref": "E7", "note": "clinic page"}],
     "match_notes": None},
    {"display_name": "Ghost Person", "summary": "No real evidence.", "field": None, "affiliations": [],
     "location": None, "evidence": [{"ref": "E42", "note": None}], "match_notes": None},
]}


class FakeModel:
    """Stands in for llm.call_model. Web-search calls and JSON calls each pop from their own queue;
    web search calls go out in order expert query, name query, hint query, and get no results once
    their queue is empty. An item that is an Exception is raised; `hang=True` makes web search hang."""

    def __init__(self, web=None, json=None, hang=False):
        self.web = list(web or [])
        self.json = list(json or [])
        self.hang = hang
        self.calls = []

    async def __call__(self, instructions, input, schema=None, web_search=False, output_type=None):
        self.calls.append({"instructions": instructions, "input": input, "schema": schema,
                           "web_search": web_search})
        if web_search and self.hang:
            await asyncio.sleep(3600)
        if web_search and not self.web:
            return llm.ModelResult()
        item = (self.web if web_search else self.json).pop(0)
        if isinstance(item, Exception):
            raise item
        if web_search:
            return llm.ModelResult(results=item)
        return llm.ModelResult(data=output_type.model_validate(item) if output_type else item)


HANG = object()  # a FakeAdkModel turn that never answers (time-limit tests)


def call(name, **args):
    """A FakeAdkModel turn: the model calls tool `name` with `args`."""
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name=name, args=args))]))


class FakeAdkModel(BaseLlm):
    """Stands in for the ADK model (tests/conftest.py installs one per test via `llm.fake_model`).
    Replays `script` one turn per model call: a str is a text reply, `call(...)` a tool call, an
    Exception is raised, HANG never answers. Once the script is empty it replies "DONE".
    Every request is kept in `seen`."""

    model: str = "fake"
    script: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self.seen.append(llm_request)
        turn = self.script.pop(0) if self.script else "DONE"
        if turn is HANG:
            await asyncio.sleep(3600)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            turn = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=turn)]))
        yield turn


def router(routes: dict):
    """MockTransport handler: host -> (status, json) or a function(request) -> Response."""
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        route = routes.get(request.url.host)
        if route is None:
            return httpx.Response(599, json={"detail": "unexpected host in test"})
        if callable(route):
            return route(request)
        status, body = route
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle), seen


DEFAULT_ROUTES = {
    "api.openalex.org": (200, OPENALEX_AUTHORS),
    "www.courtlistener.com": (200, COURTLISTENER_SEARCH),
}


@pytest.fixture(name="sources")
def sources_fixture(monkeypatch):
    """Install fake HTTP routes and a FakeModel. Returns a setup function(routes=..., model=...)."""
    monkeypatch.setattr(http, "limiters", {})  # no rate-limit waits in tests
    monkeypatch.setenv("COURTLISTENER_TOKEN", "test-token")

    def setup(routes=None, model=None):
        transport, seen = router(DEFAULT_ROUTES | (routes or {}))
        http.set_client(http.make_client(transport))
        fake = model or FakeModel(web=[WEB_RESULTS, WEB_NAME_RESULTS], json=[GROUPING])
        monkeypatch.setattr(llm, "call_model", fake)
        return fake, seen

    yield setup
    http.set_client(None)


# Research tools: fake return values of the source functions, keyed "module.function".


def cl_filing(did, n=2, passage="Exhibit 2: the claim was reviewed by Dr. Alex J. Rivera, a toxicologist",
              description="Exhibit 2, Appeal Decision Letter", available=True, pages=3):
    """A RECAP filing as courtlistener.to_document gives it, with its search passage (synthetic)."""
    return {"id": did * 100 + n, "description": description, "document_number": str(n), "attachment_number": None,
            "date_filed": "2021-05-01", "page_count": pages, "available": available,
            "filepath_local": f"recap/synthetic.{did}.{n}.pdf" if available else None,
            "url": f"https://www.courtlistener.com/docket/{did}/{n}/doe-v-example/", "passage": passage}


def cl_docket(did, date, court="District Court, N.D. Example", named_as=(), documents=None):
    """A RECAP docket as courtlistener.search_dockets returns it (synthetic)."""
    return {"url": f"https://www.courtlistener.com/docket/{did}/doe-v-example/", "docket_id": did,
            "case_name": f"Doe v. Example Co. {did}",
            "court": court, "docket_number": f"1:{did}-cv-0001", "date_filed": date, "nature_of_suit": "791 ERISA",
            "named_as": list(named_as), "more_filings": False,
            "documents": [cl_filing(did)] if documents is None else documents}


def work(n, year, doi=True, retracted=False):
    doi_value = f"10.5555/synthetic.{n}" if doi else None
    return {"id": f"W{n}", "title": f"Synthetic toxicology study {n}", "year": year, "venue": "Example Journal",
            "doi": doi_value, "url": f"https://doi.org/{doi_value}" if doi else f"https://openalex.org/W{n}",
            "is_retracted": retracted, "cited_by_count": n}


def crossref_hit(doi):
    """Only synthetic work 2 is retracted in Crossref."""
    if doi != "10.5555/synthetic.2":
        return None
    return {"doi": doi, "date": "2022-03-01", "source": "retraction-watch",
            "notice_url": "https://doi.org/10.5555/notice.2", "url": f"https://doi.org/{doi}"}


PAGE_TEXT = "SYNTHETIC-PAGE-TEXT"  # marker: page markdown must never reach a finding or the database
PAGE_MARKDOWN = (f"{PAGE_TEXT} Alex J. Rivera, PhD, board-certified toxicologist. Testimony: Doe v. Example Co.,"
                 " N.D. Example 2019 (deposition, plaintiff); Roe v. Sample Corp. (trial).")
def scrape_page(url):
    return {"url": url, "title": "Alex J. Rivera - CV", "markdown": PAGE_MARKDOWN}


def model_reply(instructions, input, schema=None, web_search=False, output_type=None):
    """Default AI call in research tests: none is expected unless a test sets its own."""
    raise AssertionError("unexpected AI call in a research test")


RESEARCH_SOURCES = {
    "courtlistener.search_dockets": [cl_docket(12, "2018-01-02"), cl_docket(11, "2021-05-01")],
    "websearch.search_facts": [
        {"url": "https://example.org/news/rivera", "title": "Expert testifies in example trial",
         "facts": ["Testified for the plaintiff in a 2020 example trial"], "snippet": "SYNTHETIC-WEB-SNIPPET"},
        {"url": "https://www.linkedin.com/in/alex-rivera", "title": "LinkedIn", "facts": ["blocked"]},
    ],
    "openalex.author": {"id": "A0000000001", "url": "https://openalex.org/A0000000001",
                        "display_name": "Alex J. Rivera", "works_count": 3, "cited_by_count": 40, "h_index": 2,
                        "orcid": None, "last_institution": "Example State University"},
    # work 1 listed twice (dedupe by DOI); work 3 has no DOI but OpenAlex flags it retracted
    "openalex.works": [work(1, 2021), work(2, 2020), work(1, 2021), work(3, 2015, doi=False, retracted=True)],
    "crossref.retraction": crossref_hit,
    "leie.possible_matches": [{"general": "SYNTHETIC GENERAL", "specialty": "SYNTHETIC SPECIALTY",
                               "excltype": "1128a1", "excldate": "20200101", "reindate": "", "state": "OH",
                               "url": "https://exclusions.oig.hhs.gov/"}],
    "firecrawl.scrape": scrape_page,
    # Court filings (court_filings, court_file): none by default; tests set their own.
    "courtlistener.docket_documents": ([], False),  # (filings, more left unlisted)
    "courtlistener.document_text": "",
    "courtlistener.download_document": AssertionError("unexpected download in a research test"),
    "llm.call_model": model_reply,
}
MODULES = {"courtlistener": courtlistener, "crossref": crossref, "firecrawl": firecrawl, "leie": leie,
           "llm": llm, "openalex": openalex, "websearch": websearch}


@pytest.fixture(name="research_sources")
def research_sources_fixture(monkeypatch):
    """Replace the research source functions. setup({"module.func": value}) overrides defaults:
    a callable is called with the arguments, an Exception is raised, anything else is returned.
    Returns a list of (key, args, kwargs) calls."""
    calls = []

    def setup(overrides=None):
        for key, value in (RESEARCH_SOURCES | (overrides or {})).items():
            module, func = key.split(".")

            async def fake(*args, _key=key, _value=value, **kwargs):
                calls.append((_key, args, kwargs))
                if isinstance(_value, Exception):
                    raise _value
                result = _value(*args, **kwargs) if callable(_value) else copy.deepcopy(_value)
                return await result if inspect.isawaitable(result) else result

            monkeypatch.setattr(MODULES[module], func, fake, raising=False)
        return calls

    return setup


# Main agent tools (DESIGN 4B.8): a Ctx on the test's temp SQLite, and helpers.

EXPERT = Candidate(
    display_name=NAME, summary="Synthetic toxicologist.", field="toxicology", location="Ohio, US",
    affiliations=["Example State University"],
    evidence=[{"source": "web", "url": "https://example.edu/faculty/rivera", "title": "Faculty page"}],
)
TEST_FILE_URL = "https://example.edu/files/rivera-cv.pdf"


@pytest.fixture(name="tool_ctx")
def tool_ctx_fixture():
    """A Ctx for the synthetic expert (Alex Rivera) with a research job, a dossier, 60 s on the
    clock and the registry's caps. Tools run through `run_tool`, so caps and activity rows are real."""
    job_id = store.create_job("research", jobs.RESEARCH_STEPS)
    dossier_id = store.create_research("cand_test", job_id)
    run = Run(seconds=60, caps={tool_name: t.cap for tool_name, t in REGISTRY.items()})
    return Ctx(job_id=job_id, dossier_id=dossier_id, expert=EXPERT, run=run)


async def run_tool(ctx: Ctx, tool_name: str, **args) -> dict:
    """Call a registered tool through the wrapper, as the main agent does."""
    return await ctx.call_tool(tool_name, **args)


def add_test_file(ctx: Ctx, text: str, mime: str = "application/pdf") -> str:
    """Save a synthetic downloaded file with `text` as its text version; returns the file_id."""
    return ctx.save_file(TEST_FILE_URL, b"%PDF-1.4 synthetic test file", mime, text=text)


@pytest.fixture(name="register_tool")
def register_tool_fixture():
    """register(fn, label=..., cap=...) adds a test-only tool; it is removed after the test."""
    added = []

    def register(fn, label="Test tool", cap=3):
        tool(label=label, cap=cap)(fn)
        added.append(fn.__name__)
        return fn

    yield register
    for tool_name in added:
        REGISTRY.pop(tool_name, None)
