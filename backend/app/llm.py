"""The one door to the Meta Model API (DESIGN 4A.2): the ADK model factory and agent runner, plus
`call_model`. Web search stays a plain httpx Responses call: ADK drops its results (gotcha 28)."""

import os

# Before litellm is first imported: no cost-map download (gotcha 30), no `.env` autoload (gotcha 31).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field

import httpx
import litellm
from google.adk.agents import LlmAgent, RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, ValidationError

from app import trace
from app.config import get_settings, is_banned_model

BASE_URL = "https://api.meta.ai/v1"
TIMEOUT = httpx.Timeout(10.0, read=120.0)
RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF = (1.0, 3.0)  # seconds before retry 1 and 2 (web search)
MODEL_RETRIES = 2  # ADK calls: the OpenAI client inside LiteLLM retries 429, 5xx and network errors
MODEL_TIMEOUT = 120.0  # seconds per ADK model request
CALL_TIMEOUT = 300.0  # seconds for one call_model, retries included
transport: httpx.AsyncBaseTransport | None = None  # tests put an httpx.MockTransport here
fake_model: BaseLlm | None = None  # tests put a fake here; make_model returns it (DESIGN 4A.7)

litellm.suppress_debug_info = True  # no "Give Feedback" banners on errors


_make_record = logging.getLogRecordFactory()


def _no_adk_traceback(*args, **kwargs) -> logging.LogRecord:
    # ADK logs every failure with a traceback from several loggers, and LiteLLM errors carry provider
    # text (gotcha 29): ADK records keep their message, never the exception.
    record = _make_record(*args, **kwargs)
    if record.name.startswith("google_adk"):
        record.exc_info = record.exc_text = None
    return record


logging.setLogRecordFactory(_no_adk_traceback)


class ModelError(Exception):
    """Every model failure. Messages are fixed text (plus an HTTP status), never provider or model text."""


MALFORMED = "model stream was malformed"


@dataclass
class ModelResult:
    text: str = ""
    data: object = None  # parsed JSON (or the output_type instance) when a schema was given
    results: list[dict] = field(default_factory=list)  # web_search_call results: url, title, snippet
    citations: list[dict] = field(default_factory=list)  # url_citation annotations: url, title


@dataclass
class AgentRun:
    text: str = ""  # the final reply
    calls: int = 0  # model calls made
    stopped: str | None = None  # None, "step_limit" or "time_limit"
    transcript: list[str] = field(default_factory=list)  # the run as text: replies, tool calls, results


def transcript_lines(event) -> list[str]:
    """One ADK event as text: the model's words, its tool calls, and the tools' results."""
    if event.author == "user" or not event.content:
        return []
    text = "".join(p.text or "" for p in event.content.parts or [] if not p.thought)
    return (([f"model: {text}"] if text.strip() else [])
            + [f"tool call {c.name}: {json.dumps(c.args, ensure_ascii=False, default=str)}"
               for c in event.get_function_calls()]
            + [f"tool result {r.name}: {json.dumps(r.response, ensure_ascii=False, default=str)}"
               for r in event.get_function_responses()])


def check_model_name(name: str) -> str:
    # Terms 6.2: no personal information may go to the Contributor Services. One rule, in app/config.py.
    if is_banned_model(name):
        raise ModelError("contributor models must not be used (Meta terms 6.2)")
    return name


# ADK layer


def make_model(**extra) -> BaseLlm:
    """The only place a model is built: chat route `openai/<model>` (the Responses route needs the
    `mcp` package, gotcha 27; the chat route too for tool calls, gotcha 42). `extra` goes to LiteLLM as is, e.g. `response_format`."""
    settings = get_settings()
    name = check_model_name(settings.model_name)
    if not settings.model_api_key:
        raise ModelError("MODEL_API_KEY is not set")
    if fake_model is not None:
        return fake_model
    # ponytail: `_skip_mcp_handler` is a private litellm flag (gotcha 42): without it any call with
    # tools imports litellm's MCP gateway, which needs the `mcp` package. Re-check on a litellm upgrade.
    return LiteLlm(model=f"openai/{name}", api_base=BASE_URL, api_key=settings.model_api_key,
                   max_retries=MODEL_RETRIES, timeout=MODEL_TIMEOUT, _skip_mcp_handler=True, **extra)


def make_agent(name: str, instructions: str, model: BaseLlm | None = None, **kwargs) -> LlmAgent:
    """An LlmAgent whose instructions are sent as written. A plain string would be a template:
    ADK fills `{word}` from session state and fails on unknown words (gotcha 32)."""
    return LlmAgent(name=name, model=model or make_model(), instruction=lambda _ctx: instructions, **kwargs)


def model_error(e: Exception) -> ModelError:
    """Fixed text from the exception type and HTTP status only (gotcha 29)."""
    status = getattr(e, "status_code", None)
    return ModelError(f"model call failed ({type(e).__name__}{f', HTTP {status}' if status else ''})")


async def run_agent(agent: LlmAgent, prompt: str, *, max_llm_calls: int, timeout: float) -> AgentRun:
    """Run `agent` once on `prompt` with a fresh in-memory session (dropped after the run). Hitting
    the step or time limit is not an error: it sets `stopped`. Other failures raise ModelError."""
    sessions = InMemorySessionService()
    runner = Runner(app_name="expert_witness", agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name="expert_witness", user_id="api")
    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    out = AgentRun()
    try:
        async with asyncio.timeout(timeout):
            events = runner.run_async(user_id="api", session_id=session.id, new_message=message,
                                      run_config=RunConfig(max_llm_calls=max_llm_calls))
            async with contextlib.aclosing(events):
                async for event in events:
                    out.transcript += transcript_lines(event)
                    if event.get_function_responses():
                        trace.tool_errors(agent.name, event)  # what the model was told, ADK's own refusals too
                    if event.author == "user" or not event.content or event.get_function_responses():
                        continue
                    trace.model_turn(agent.name, event)  # no-op outside a research job
                    out.calls += 1
                    if event.is_final_response():
                        out.text += "".join(p.text or "" for p in event.content.parts or [] if not p.thought)
    except TimeoutError:
        out.stopped = "time_limit"
    except LlmCallsLimitExceededError:
        out.stopped = "step_limit"
    except ModelError:
        raise
    except Exception as e:  # noqa: BLE001 - any LiteLLM/ADK error; its text may quote the provider
        raise model_error(e) from None
    return out


# Web search: plain httpx Responses call (gotchas 14, 28)


def build_body(instructions: str, input: str) -> dict:
    return {
        "model": check_model_name(get_settings().model_name),
        "instructions": instructions,
        "input": input,
        "store": False,
        "stream": True,
        "tools": [{"type": "web_search"}],
        "include": ["web_search_call.results"],
    }


def parse_response(response: dict) -> ModelResult:
    """Pull text, web results and citations out of a final Responses object."""
    out = ModelResult()
    for item in response.get("output") or []:
        if item.get("type") == "web_search_call":
            out.results += [r for r in item.get("results") or [] if r.get("url")]
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    out.text += part.get("text", "")
                    out.citations += [a for a in part.get("annotations") or [] if a.get("type") == "url_citation"]
    return out


async def _stream(client: httpx.AsyncClient, body: dict) -> dict:
    """POST with stream=true and return the response object from `response.completed`."""
    headers = {"Authorization": f"Bearer {get_settings().model_api_key}"}
    async with client.stream("POST", f"{BASE_URL}/responses", json=body, headers=headers) as r:
        if r.status_code >= 400:
            await r.aread()
            raise httpx.HTTPStatusError(f"Meta API HTTP {r.status_code}", request=r.request, response=r)
        async for line in r.aiter_lines():
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                raise ModelError(MALFORMED) from None
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "response.completed":
                if not isinstance(event.get("response"), dict):
                    raise ModelError(MALFORMED)
                return event["response"]
            if kind in ("response.failed", "response.incomplete", "error"):
                # Fixed text only: provider error codes and messages may echo the model's reply.
                raise ModelError(f"model call ended with {kind}")
    raise ModelError("stream ended without response.completed")


async def _post(body: dict) -> dict:
    """One call with retries: 429 and 5xx twice with backoff, never on other 4xx."""
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
        for attempt in range(len(BACKOFF) + 1):
            try:
                return await _stream(client, body)
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in RETRY_STATUS or attempt == len(BACKOFF):
                    raise ModelError(str(e)) from e
            except httpx.TransportError as e:
                if attempt == len(BACKOFF):
                    raise ModelError(f"Meta API unreachable ({type(e).__name__})") from e
            await asyncio.sleep(BACKOFF[attempt])
    raise AssertionError("unreachable")


async def _search(instructions: str, input: str) -> ModelResult:
    try:
        return parse_response(await _post(build_body(instructions, input)))
    except ModelError:
        raise
    except Exception as e:  # any odd response must still be a ModelError
        raise ModelError("model call failed: unexpected response") from e


# call_model: the one-shot call every caller (and every test fake) uses


def response_format(schema: dict) -> dict:
    """Structured output on the chat route (DESIGN 4A.2)."""
    return {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}


async def _ask(instructions: str, input: str, schema: dict | None) -> str:
    extra = {"response_format": response_format(schema)} if schema else {}
    agent = make_agent("call_model", instructions, make_model(**extra))
    run = await run_agent(agent, input, max_llm_calls=1, timeout=CALL_TIMEOUT)
    if run.stopped:
        raise ModelError("model call timed out")
    return run.text


async def call_model(
    instructions: str,
    input: str,
    schema: dict | None = None,
    web_search: bool = False,
    output_type: type[BaseModel] | None = None,
) -> ModelResult:
    """Call the model. With `schema`, `data` holds parsed JSON (validated as `output_type` if given);
    bad output gets one retry, then ModelError. `web_search=True` returns results and citations."""
    if not get_settings().model_api_key:
        raise ModelError("MODEL_API_KEY is not set")
    if web_search:
        if schema:
            raise ValueError("web_search and schema in one call is not verified; use two calls")
        return await _search(instructions, input)
    for attempt in range(2):
        result = ModelResult(text=await _ask(instructions, input, schema))
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
