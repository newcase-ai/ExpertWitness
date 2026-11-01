"""The ADK layer and call_model (DESIGN 4A.2, 4A.7). Never the real API: web search uses a fake
google-genai Client (redirects an httpx MockTransport), ADK agents the FakeAdkModel from conftest."""

import asyncio

import httpx
import pytest
from google.adk.models.google_llm import Gemini
from google.adk.tools import FunctionTool
from google.genai import errors, types
from pydantic import BaseModel

from app import evidence, llm
from tests.fakes import HANG, call

# Web search: google-genai call with Google Search grounding (fake Client)

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def grounded(text, *pages):
    chunks = [types.GroundingChunk(web=types.GroundingChunkWeb(uri=uri, title=title)) for uri, title in pages]
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        grounding_metadata=types.GroundingMetadata(grounding_chunks=chunks))])


def search(query="q"):
    return asyncio.run(llm.call_model("i", query, web_search=True))


@pytest.fixture
def gemini(monkeypatch):
    """Fake google-genai Client: `replies` (a response or an Exception per call), `seen` (Client
    kwargs, then generate_content kwargs). Redirects: Location = the path after REDIRECT."""
    replies, seen = [], []

    class Aio:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        class models:  # mirrors client.aio.models
            @staticmethod
            async def generate_content(**kwargs):
                seen.append(kwargs)
                reply = replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply

    class FakeClient:
        def __init__(self, **kwargs):
            seen.append(kwargs)
            self.aio = Aio()

    def redirect(request):
        target = str(request.url).removeprefix(REDIRECT)
        return httpx.Response(302, headers={"location": target}) if target != "broken" else httpx.Response(404)

    monkeypatch.setattr(llm, "Client", FakeClient)
    monkeypatch.setattr(llm, "transport", httpx.MockTransport(redirect))
    return replies, seen


def test_web_search_resolves_google_redirects(gemini, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    replies, seen = gemini
    replies.append(grounded("Done.", (REDIRECT + "https://example.edu/faculty/rivera", "example.edu"),
                            ("https://example.org/direct", "example.org"), (REDIRECT + "broken", "gone.test")))
    out = search()
    assert out.text == "Done."
    assert out.results == [{"url": "https://example.edu/faculty/rivera", "title": "example.edu"},
                           {"url": "https://example.org/direct", "title": "example.org"}]
    client_kwargs, request = seen
    assert client_kwargs["vertexai"] is True and client_kwargs["project"] == "test-project"
    assert request["model"] == "gemini-3.8-flash" and request["contents"] == "q"
    assert request["config"].system_instruction == "i"
    assert request["config"].tools[0].google_search is not None


def test_web_search_without_grounding_is_empty(gemini):
    replies, _ = gemini
    replies += [types.GenerateContentResponse(candidates=[]), grounded("no pages")]
    assert search().results == []
    assert search().text == "no pages"


def test_web_search_errors_never_quote_the_provider(gemini):
    replies, _ = gemini
    replies.append(errors.ClientError(429, {"error": {"message": "SYNTHETIC 12 Elm St"}}))
    with pytest.raises(llm.ModelError) as err:
        search()
    assert str(err.value) == "model call failed (ClientError, HTTP 429)"


def test_refuses_search_plus_schema():
    with pytest.raises(ValueError, match="two calls"):
        asyncio.run(llm.call_model("i", "q", schema={"type": "object"}, web_search=True))


# call_model without web search: a one-shot ADK agent


def test_call_model_text_goes_through_adk(adk_model):
    adk_model.script = ["hello"]
    out = asyncio.run(llm.call_model("Use {braces} as written.", "say hi"))
    assert out.text == "hello" and out.data is None
    request = adk_model.seen[0]
    assert "Use {braces} as written." in request.config.system_instruction
    assert request.contents[-1].parts[0].text == "say hi"


def test_call_model_schema_retries_bad_json_once(adk_model):
    adk_model.script = ["not json", '{"a": 1}']
    assert asyncio.run(llm.call_model("i", "q", schema={"type": "object"})).data == {"a": 1}
    assert len(adk_model.seen) == 2


class Shape(BaseModel):
    name: str


def test_call_model_bad_output_twice_never_quotes_the_model(adk_model):
    for reply in ("nope", '{"wrong": "SYNTHETIC 12 Elm St"}'):
        adk_model.script = [reply, reply]
        with pytest.raises(llm.ModelError, match="invalid output") as err:
            asyncio.run(llm.call_model("i", "q", schema={"type": "object"}, output_type=Shape))
        assert reply not in str(err.value) and "Elm" not in str(err.value)
    adk_model.script = ['{"name": "x"}']
    assert asyncio.run(llm.call_model("i", "q", schema={"type": "object"}, output_type=Shape)).data == Shape(name="x")


def test_schema_is_sent_as_a_json_schema(adk_model):
    adk_model.script = ['{"a": 1}']
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    assert asyncio.run(llm.call_model("i", "q", schema=schema)).data == {"a": 1}
    config = adk_model.seen[0].config
    assert config.response_mime_type == "application/json" and config.response_json_schema == schema


def test_make_model_builds_gemini_on_vertex(monkeypatch):
    monkeypatch.setattr(llm, "fake_model", None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    model = llm.make_model()
    assert isinstance(model, Gemini) and model.model == "gemini-3.8-flash"
    assert model.client_kwargs == {"vertexai": True, "project": "test-project", "location": "global"} and model.retry_options == llm.RETRY


# run_agent: tool loop, step and time limits, errors


class ProviderError(Exception):
    status_code = 401


def test_run_agent_runs_tools_and_returns_the_final_text(adk_model):
    run = evidence.Run(seconds=5, caps={"lookup": 2})

    async def lookup(case_id: str) -> dict:
        """Look up a case by id."""
        return {"ref": run.add({"url": f"https://example.org/{case_id}", "title": "T" * 300})}

    adk_model.script = [call("lookup", case_id="1"), "DONE"]
    agent = llm.make_agent("t", "Use lookup.", tools=[FunctionTool(run.tool(lookup))])
    out = asyncio.run(llm.run_agent(agent, "go", max_llm_calls=5, timeout=5))
    assert (out.text, out.calls, out.stopped) == ("DONE", 2, None)
    assert run.get("E1") == {"url": "https://example.org/1", "title": "T" * evidence.TITLE_MAX}
    assert run.has_url("https://example.org/1") and not run.has_url("https://example.org/2")
    tool_reply = adk_model.seen[1].contents[-1].parts[0].function_response.response
    assert tool_reply == {"ref": "E1"}
    declaration = adk_model.seen[0].config.tools[0].function_declarations[0]
    assert declaration.name == "lookup" and declaration.parameters_json_schema["required"] == ["case_id"]


def test_run_agent_step_limit_keeps_tool_results(adk_model):
    run = evidence.Run(seconds=5, caps={"lookup": 99})

    async def lookup(case_id: str) -> dict:
        """Look up a case by id."""
        return {"ref": run.add({"url": f"https://example.org/{case_id}"})}

    adk_model.script = [call("lookup", case_id=str(i)) for i in range(20)]
    agent = llm.make_agent("t", "x", tools=[FunctionTool(run.tool(lookup))])
    out = asyncio.run(llm.run_agent(agent, "go", max_llm_calls=3, timeout=5))
    assert out.stopped == "step_limit" and len(adk_model.seen) == 3
    assert list(run.evidence) == ["E1", "E2", "E3"]


def test_run_agent_time_limit(adk_model, monkeypatch):
    adk_model.script = [HANG]
    out = asyncio.run(llm.run_agent(llm.make_agent("t", "x"), "go", max_llm_calls=3, timeout=0.2))
    assert out.stopped == "time_limit"
    adk_model.script = [HANG]
    monkeypatch.setattr(llm, "CALL_TIMEOUT", 0.2)
    with pytest.raises(llm.ModelError, match="timed out"):
        asyncio.run(llm.call_model("i", "q"))


def test_run_agent_errors_never_carry_provider_text(adk_model, caplog):
    adk_model.script = [ProviderError("SYNTHETIC provider text 12 Elm St")]
    with pytest.raises(llm.ModelError) as err:
        asyncio.run(llm.run_agent(llm.make_agent("t", "x"), "go", max_llm_calls=3, timeout=5))
    assert str(err.value) == "model call failed (ProviderError, HTTP 401)"
    assert "Elm" not in caplog.text


# Run: tool caps, deadline, failures


def test_run_tool_refuses_over_cap_unknown_tools_and_after_the_deadline():
    run = evidence.Run(seconds=5, caps={"lookup": 1})

    async def lookup() -> dict:
        return {"ok": True}

    async def other() -> dict:
        return {"ok": True}

    wrapped = run.tool(lookup)
    assert asyncio.run(wrapped()) == {"ok": True}
    assert asyncio.run(wrapped()) == {"error": evidence.LIMIT_REACHED}
    assert asyncio.run(run.tool(other)()) == {"error": evidence.LIMIT_REACHED}
    late = evidence.Run(seconds=0, caps={"lookup": 5})
    assert asyncio.run(late.tool(lookup)()) == {"error": evidence.LIMIT_REACHED}


def test_run_tool_refuses_bad_arguments_without_a_source_failure():
    run = evidence.Run(seconds=5, caps={"lookup": 1})
    seen = []

    async def lookup(case_id: str, year: int | None = None) -> dict:
        seen.append((case_id, year))
        raise ValueError("SYNTHETIC source error")

    wrapped = run.tool(lookup)
    for kwargs in ({"case_id": 3}, {}, {"case_id": "1", "zzz": 1}, {"case_id": "1", "year": "soon"}):
        assert asyncio.run(wrapped(**kwargs)) == {"error": evidence.BAD_ARGUMENTS}
    assert seen == [] and run.failures == [] and run.used == {}
    # good arguments (lax: "2019" is an int) reach the source; its own ValueError is a source failure
    assert asyncio.run(wrapped(case_id="1", year="2019")) == {"error": evidence.TOOL_FAILED}
    assert seen == [("1", 2019)] and run.failures == [("lookup", "ValueError")]


def test_run_tool_never_raises_and_stops_at_the_deadline():
    run = evidence.Run(seconds=0.2, caps={"boom": 1, "slow": 1})

    async def boom() -> dict:
        raise RuntimeError("SYNTHETIC provider text")

    async def slow() -> dict:
        await asyncio.sleep(3600)
        return {}

    assert asyncio.run(run.tool(boom)()) == {"error": evidence.TOOL_FAILED}
    assert asyncio.run(run.tool(slow)()) == {"error": evidence.TOOL_FAILED}
    assert run.failures == [("boom", "RuntimeError"), ("slow", "TimeoutError")]


def test_run_ids_and_copy_check():
    run = evidence.Run(seconds=1, prefix="E")
    assert run.add({"url": "a"}) == "E1" and run.add({"url": "b"}, ref="f-9") == "f-9" and run.add({}) == "E2"
    page = evidence.squash("A" * 50)
    assert evidence.copied("a" * 45, page) and not evidence.copied("a" * 30, page)
    assert evidence.words("Orthopedic Surgery") == {"orthopedic"}
