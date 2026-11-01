"""The one door to Gemini on Vertex AI (DESIGN 4A.2): the ADK model factory and agent runner, plus
`call_model`. Web search stays a plain google-genai call with Google Search grounding: ADK drops
its results (gotcha 28)."""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field

import httpx
from google.adk.agents import LlmAgent, RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import Client, types
from pydantic import BaseModel, ValidationError

from app.config import get_settings

LOCATION = "global"
RETRY = types.HttpRetryOptions(attempts=3, initial_delay=1.0)  # genai retries 408, 429 and 5xx
REDIRECT_HOST = "vertexaisearch.cloud.google.com"  # grounding links point here, not at the page
TIMEOUT = httpx.Timeout(10.0)
CALL_TIMEOUT = 300.0  # seconds for one call_model, retries included
transport: httpx.AsyncBaseTransport | None = None  # tests put an httpx.MockTransport here
fake_model: BaseLlm | None = None  # tests put a fake here; make_model returns it (DESIGN 4A.7)


_make_record = logging.getLogRecordFactory()


def _no_adk_traceback(*args, **kwargs) -> logging.LogRecord:
    # ADK logs every failure with a traceback from several loggers, and provider errors carry
    # provider text (gotcha 29): ADK records keep their message, never the exception.
    record = _make_record(*args, **kwargs)
    if record.name.startswith("google_adk"):
        record.exc_info = record.exc_text = None
    return record


logging.setLogRecordFactory(_no_adk_traceback)


class ModelError(Exception):
    """Every model failure. Messages are fixed text (plus an HTTP status), never provider or model text."""


@dataclass
class ModelResult:
    text: str = ""
    data: object = None  # parsed JSON (or the output_type instance) when a schema was given
    results: list[dict] = field(default_factory=list)  # grounding pages: url, title
    citations: list[dict] = field(default_factory=list)  # unused with Gemini; callers fall back to it


@dataclass
class AgentRun:
    text: str = ""  # the final reply
    calls: int = 0  # model calls made
    stopped: str | None = None  # None, "step_limit" or "time_limit"


# ADK layer


def vertex() -> dict:
    """Vertex AI client settings: the project from GOOGLE_CLOUD_PROJECT, auth from Application Default
    Credentials (`gcloud auth application-default login`)."""
    return {"vertexai": True, "project": get_settings().google_cloud_project, "location": LOCATION}


def make_model() -> BaseLlm:
    """The only place a model is built."""
    if fake_model is not None:
        return fake_model
    return Gemini(model=get_settings().model_name, client_kwargs=vertex(), retry_options=RETRY)


def make_agent(name: str, instructions: str, model: BaseLlm | None = None, **kwargs) -> LlmAgent:
    """An LlmAgent whose instructions are sent as written. A plain string would be a template:
    ADK fills `{word}` from session state and fails on unknown words (gotcha 32)."""
    return LlmAgent(name=name, model=model or make_model(), instruction=lambda _ctx: instructions, **kwargs)


def model_error(e: Exception) -> ModelError:
    """Fixed text from the exception type and HTTP status only (gotcha 29)."""
    status = getattr(e, "code", None) or getattr(e, "status_code", None)
    return ModelError(f"model call failed ({type(e).__name__}{f', HTTP {status}' if status else ''})")


async def run_agent(agent: LlmAgent, prompt: str, *, max_llm_calls: int, timeout: float,
                    pdf: bytes | None = None) -> AgentRun:
    """Run `agent` once on `prompt` (and `pdf`, attached after it) with a fresh in-memory session
    (dropped after the run). Hitting the step or time limit is not an error: it sets `stopped`.
    Other failures raise ModelError."""
    sessions = InMemorySessionService()
    runner = Runner(app_name="expert_witness", agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name="expert_witness", user_id="api")
    parts = [types.Part(text=prompt)]
    if pdf:
        parts.append(types.Part.from_bytes(data=pdf, mime_type="application/pdf"))
    message = types.Content(role="user", parts=parts)
    out = AgentRun()
    try:
        async with asyncio.timeout(timeout):
            events = runner.run_async(user_id="api", session_id=session.id, new_message=message,
                                      run_config=RunConfig(max_llm_calls=max_llm_calls))
            async with contextlib.aclosing(events):
                async for event in events:
                    if event.author == "user" or not event.content or event.get_function_responses():
                        continue
                    out.calls += 1
                    if event.is_final_response():
                        out.text += "".join(p.text or "" for p in event.content.parts or [] if not p.thought)
    except TimeoutError:
        out.stopped = "time_limit"
    except LlmCallsLimitExceededError:
        out.stopped = "step_limit"
    except ModelError:
        raise
    except Exception as e:  # noqa: BLE001 - any genai/ADK error; its text may quote the provider
        raise model_error(e) from None
    return out


# Web search: google-genai call with Google Search grounding


async def page_url(http: httpx.AsyncClient, uri: str) -> str | None:
    """Grounding links are Google redirects; the page URL is their Location header. Only Google's
    redirect host is called, never the page. None when it cannot be resolved."""
    if httpx.URL(uri).host != REDIRECT_HOST:
        return uri
    try:
        return (await http.get(uri)).headers.get("location")
    except httpx.HTTPError:
        return None


async def _search(instructions: str, input: str) -> ModelResult:
    config = types.GenerateContentConfig(system_instruction=instructions,
                                         tools=[types.Tool(google_search=types.GoogleSearch())])
    try:
        # ponytail: a client per search (fresh token each time); cache one per event loop if it costs.
        async with Client(**vertex(), http_options=types.HttpOptions(retry_options=RETRY)).aio as aio:
            response = await aio.models.generate_content(model=get_settings().model_name,
                                                         contents=input, config=config)
        metadata = response.candidates[0].grounding_metadata if response.candidates else None
        pages = [c.web for c in (metadata and metadata.grounding_chunks) or [] if c.web and c.web.uri]
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as http:
            urls = await asyncio.gather(*(page_url(http, p.uri) for p in pages))
    except Exception as e:  # noqa: BLE001 - its text may quote the provider
        raise model_error(e) from None
    results = [{"url": url, "title": page.title} for url, page in zip(urls, pages) if url]
    return ModelResult(text=response.text or "", results=results)


# call_model: the one-shot call every caller (and every test fake) uses


async def _ask(instructions: str, input: str, schema: dict | None, pdf: bytes | None) -> str:
    config = types.GenerateContentConfig(response_mime_type="application/json",
                                         response_json_schema=schema) if schema else None
    agent = make_agent("call_model", instructions, generate_content_config=config)
    run = await run_agent(agent, input, max_llm_calls=1, timeout=CALL_TIMEOUT, pdf=pdf)
    if run.stopped:
        raise ModelError("model call timed out")
    return run.text


async def call_model(
    instructions: str,
    input: str,
    schema: dict | None = None,
    web_search: bool = False,
    output_type: type[BaseModel] | None = None,
    pdf: bytes | None = None,
) -> ModelResult:
    """Call the model. With `schema`, `data` holds parsed JSON (validated as `output_type` if given);
    bad output gets one retry, then ModelError. `web_search=True` returns the grounding pages.
    `pdf` (PDF bytes) is attached after `input`; not with web search."""
    if web_search:
        if schema:
            raise ValueError("web_search and schema in one call is not verified; use two calls")
        return await _search(instructions, input)
    for attempt in range(2):
        result = ModelResult(text=await _ask(instructions, input, schema, pdf))
        if not schema:
            return result
        try:
            data = json.loads(result.text)
            result.data = output_type.model_validate(data) if output_type else data
            return result
        except (json.JSONDecodeError, ValidationError) as e:
            if attempt == 1:  # generic message: the parser's text would quote the model's reply
                raise ModelError("model returned invalid output after 1 retry") from e
    raise AssertionError("unreachable")
