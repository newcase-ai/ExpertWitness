"""The ADK layer and call_model (DESIGN 4A.2, 4A.7). Never the real API: web search uses an httpx
MockTransport, ADK agents the FakeAdkModel from conftest, LiteLLM its offline `mock_response`."""

import asyncio
import json

import httpx
import pytest
from google.adk.models.lite_llm import LiteLlm, LiteLLMClient
from google.adk.tools import FunctionTool
from pydantic import BaseModel

from app import evidence, llm
from tests.fakes import HANG, WEB_RESULTS, call

# Web search: plain httpx Responses call (MockTransport)


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def completed(output):
    return {"type": "response.completed", "response": {"status": "completed", "output": output}}


def message(text, annotations=()):
    return {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": list(annotations)}]}


def search(query="q"):
    return asyncio.run(llm.call_model("i", query, web_search=True))


@pytest.fixture
def meta(monkeypatch):
    """Queue of (status, body) replies for the Meta endpoint; returns (queue, seen requests)."""
    queue, seen = [], []

    def handle(request):
        seen.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(handle))
    monkeypatch.setattr(llm, "BACKOFF", (0, 0))
    return queue, seen


def test_web_search_parses_results_and_citations(meta):
    queue, seen = meta
    found = {"type": "web_search_call", "id": "ws_1", "status": "completed", "results": WEB_RESULTS[:1]}
    cite = {"type": "url_citation", "url": "https://example.edu/faculty/rivera", "title": "Faculty page",
            "start_index": 0, "end_index": 4}
    queue.append((200, sse({"type": "response.created"}, completed([found, message("Done.", [cite])]))))
    out = search()
    assert out.text == "Done."
    assert out.results[0]["url"] == "https://example.edu/faculty/rivera"
    assert out.citations[0]["title"] == "Faculty page"
    body = json.loads(seen[0].content)
    assert body["tools"] == [{"type": "web_search"}]
    assert body["include"] == ["web_search_call.results"]
    assert body["store"] is False and body["stream"] is True and body["model"] == "muse-spark-1.3"
    assert seen[0].headers["Authorization"] == "Bearer test-key"
    assert str(seen[0].url) == "https://api.meta.ai/v1/responses"


def test_web_search_errors_never_quote_the_provider(meta):
    queue, _ = meta
    for kind in ("response.failed", "error"):
        bad = {"code": "SYNTHETIC 12 Elm St copied reply", "message": "SYNTHETIC 12 Elm St"}
        queue.append((200, sse({"type": kind, "response": {"error": bad}, "error": bad})))
        with pytest.raises(llm.ModelError, match=f"ended with {kind}") as err:
            search()
        assert "Elm" not in str(err.value)


@pytest.mark.parametrize("stream", [
    "data: {not json\n\n",  # malformed line
    'data: {"type": "response.completed", "response": "x\n\n',  # unterminated string
    sse({"type": "response.completed"}),  # no response object
    sse({"type": "response.completed", "response": None}),
    sse({"type": "response.completed", "response": {"output": ["not a dict"]}}),
    sse({"type": "response.completed", "response": {"output": 5}}),
    "data: [1, 2]\n\n",
    "",  # stream ends without response.completed
])
def test_web_search_every_bad_stream_is_a_model_error(meta, stream):
    queue, _ = meta
    queue.append((200, stream))
    with pytest.raises(llm.ModelError):
        search()


def test_web_search_unreachable_is_a_model_error(monkeypatch):
    def down(request):
        raise httpx.ConnectError("SYNTHETIC provider text")

    monkeypatch.setattr(llm, "transport", httpx.MockTransport(down))
    monkeypatch.setattr(llm, "BACKOFF", (0, 0))
    with pytest.raises(llm.ModelError, match=r"unreachable \(ConnectError\)") as err:
        search()
    assert "SYNTHETIC" not in str(err.value)


def test_web_search_retries_5xx_but_not_400(meta):
    queue, seen = meta
    queue += [(503, ""), (429, ""), (200, sse(completed([message("ok")])))]
    assert search().text == "ok"
    assert len(seen) == 3
    queue.append((400, '{"error": "bad"}'))
    with pytest.raises(llm.ModelError, match="400"):
        search()
    assert len(seen) == 4


def test_refuses_missing_key_contributor_and_search_plus_schema(monkeypatch):
    for name in ("muse-spark-1.3-contributor", "MUSE-SPARK-1.3-CONTRIBUTOR", "muse-spark-contributor-1.3 "):
        with pytest.raises(llm.ModelError, match="contributor"):
            llm.check_model_name(name)
    with pytest.raises(ValueError, match="two calls"):
        asyncio.run(llm.call_model("i", "q", schema={"type": "object"}, web_search=True))
    monkeypatch.delenv("MODEL_API_KEY")
    with pytest.raises(llm.ModelError, match="MODEL_API_KEY is not set"):
        asyncio.run(llm.call_model("i", "q"))
    with pytest.raises(llm.ModelError, match="MODEL_API_KEY is not set"):
        llm.make_model()


def test_make_model_refuses_a_contributor_name_even_in_tests(monkeypatch):
    # get_settings already refuses MODEL_NAME; make_model checks again before the fake hook.
    monkeypatch.setattr(llm, "get_settings", lambda: type("S", (), {
        "model_name": "muse-spark-1.3-contributor", "model_api_key": "k"})())
    with pytest.raises(llm.ModelError, match="contributor"):
        llm.make_model()


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


@pytest.fixture
def litellm_offline(monkeypatch):
    """The real LiteLlm model, answered by LiteLLM's offline `mock_response`. Returns (replies, sent)."""
    monkeypatch.setattr(llm, "fake_model", None)
    replies, sent = [], []
    original = LiteLLMClient.acompletion

    async def acompletion(self, model, messages, tools, **kwargs):
        sent.append({"model": model, "messages": messages, "tools": tools, **kwargs})
        return await original(self, model, messages, tools, mock_response=replies.pop(0), **kwargs)

    monkeypatch.setattr(LiteLLMClient, "acompletion", acompletion)
    return replies, sent


def test_schema_is_sent_as_response_format_on_the_chat_route(litellm_offline):
    replies, sent = litellm_offline
    replies.append('{"a": 1}')
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    assert asyncio.run(llm.call_model("i", "q", schema=schema)).data == {"a": 1}
    assert sent[0]["response_format"] == {"type": "json_schema",
                                          "json_schema": {"name": "result", "schema": schema, "strict": True}}
    assert "text" not in sent[0]
    assert sent[0]["model"] == "openai/muse-spark-1.3" and sent[0]["api_base"] == "https://api.meta.ai/v1"
    assert sent[0]["max_retries"] == llm.MODEL_RETRIES


@pytest.mark.parametrize("error, status", [("litellm.RateLimitError", "429"),
                                           ("litellm.InternalServerError", "500")])
def test_litellm_errors_become_fixed_text(litellm_offline, error, status):
    replies, _ = litellm_offline
    replies.append(error)
    with pytest.raises(llm.ModelError) as err:
        asyncio.run(llm.call_model("i", "q"))
    assert str(err.value) == f"model call failed ({error.split('.')[1]}, HTTP {status})"


def test_tool_calls_skip_the_litellm_mcp_handler(litellm_offline):
    # Without the flag litellm imports its MCP gateway for any call with tools and fails (no `mcp`).
    replies, sent = litellm_offline
    replies.append("DONE")

    async def lookup(case_id: str) -> dict:
        """Look up a case by id."""
        return {}

    agent = llm.make_agent("t", "x", tools=[FunctionTool(lookup)])
    out = asyncio.run(llm.run_agent(agent, "go", max_llm_calls=2, timeout=5))
    assert out.text == "DONE" and sent[0]["tools"] and sent[0]["_skip_mcp_handler"] is True


def test_make_model_builds_the_chat_route(monkeypatch):
    monkeypatch.setattr(llm, "fake_model", None)
    model = llm.make_model()
    assert isinstance(model, LiteLlm) and model.model == "openai/muse-spark-1.3"


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
