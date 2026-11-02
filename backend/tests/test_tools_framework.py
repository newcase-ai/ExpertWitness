"""The main agent framework (DESIGN 4B.2-4B.5): registry, wrapper, Ctx and the job's end rules, with
test-only tools (removed after each test) and FakeAdkModel scripts. Synthetic people only."""

import asyncio
import json
from typing import Literal

import pytest
from google.adk.tools import FunctionTool

from app import config, jobs, llm, memory, store, tools
from app.evidence import BAD_ARGUMENTS, LIMIT_REACHED, TOOL_FAILED, refuse
from app.sources import SourceSkipped
from app.tools import REGISTRY, Ctx, tool
from tests.fakes import (  # noqa: F401 - fixtures
    EXPERT,
    HANG,
    PAGE_MARKDOWN,
    PAGE_TEXT,
    FakeModel,
    add_test_file,
    call,
    register_tool_fixture,
    run_tool,
    tool_ctx_fixture,
)


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


def gaps(ctx):
    return [(g.step, g.reason) for g in store.get_research(ctx.dossier_id).gaps]


@pytest.fixture
def echo(register_tool):
    async def echo_text(ctx: Ctx, text: str, mode: Literal["short", "long"] = "short", count: int = 1) -> dict:
        """Echo the text back (test only)."""
        ctx.log(f"{count} echoed")
        return {"text": text, "mode": mode, "count": count}

    return register_tool(echo_text, label="Echo", cap=2)


def test_adk_sees_the_model_arguments_without_ctx(tool_ctx, echo):
    """Gotcha 34, offline: the declaration ADK sends has the tool's name, docstring and arguments only."""
    declaration = FunctionTool(tool_ctx.adk_tool("echo_text"))._get_declaration()
    assert declaration.name == "echo_text" and declaration.description == "Echo the text back (test only)."
    schema = declaration.parameters_json_schema
    assert set(schema["properties"]) == {"text", "mode", "count"} and schema["required"] == ["text"]
    assert schema["properties"]["mode"]["enum"] == ["short", "long"]
    assert "ctx" not in json.dumps(schema)


def test_a_test_tool_runs_through_the_main_agent(tool_ctx, echo, adk_model):
    """7b: a tool registered in a test is offered to the main agent and called through the wrapper."""
    adk_model.script = [call("echo_text", text="hello", count=3), "DONE"]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done" and job.result.id == tool_ctx.dossier_id
    assert (job.steps[0].status, job.steps[0].detail) == ("done", "agent finished")
    assert [(a.seq, a.tool, a.label, a.status, a.args, a.detail) for a in job.activity] == [
        (1, "echo_text", "Echo", "done", {"text": "hello", "count": 3}, "3 echoed")]
    first = adk_model.seen[0]
    assert "echo_text" in first.tools_dict and "Alex Rivera" in first.contents[0].parts[0].text
    reply = adk_model.seen[1].contents[-1].parts[0].function_response.response
    assert reply == {"text": "hello", "mode": "short", "count": 3}


def test_test_tools_do_not_stay_registered():
    assert all(t.fn.__module__.startswith("app.tools.") for t in REGISTRY.values())


def test_duplicate_name_and_name_argument_are_refused(echo):
    async def echo_text(ctx: Ctx) -> dict:
        return {}

    with pytest.raises(ValueError, match="duplicate"):
        tool(label="Again", cap=1)(echo_text)

    async def lookup(ctx: Ctx, name: str) -> dict:
        return {}

    with pytest.raises(ValueError, match="gotcha 36"):
        tool(label="Lookup", cap=1)(lookup)
    assert "lookup" not in REGISTRY


def test_cap(tool_ctx, echo):
    assert asyncio.run(run_tool(tool_ctx, "echo_text", text="a"))["text"] == "a"
    assert asyncio.run(run_tool(tool_ctx, "echo_text", text="b"))["text"] == "b"
    assert asyncio.run(run_tool(tool_ctx, "echo_text", text="c")) == refuse(LIMIT_REACHED)
    assert rows(tool_ctx)[-1] == ("echo_text", "refused", "cap used up")


def test_bad_arguments_are_refused_and_do_not_use_the_cap(tool_ctx, echo):
    for args in ({"text": 5}, {}, {"text": "a", "mode": "medium"}, {"text": "a", "extra": 1}):
        assert asyncio.run(run_tool(tool_ctx, "echo_text", **args)) == refuse(BAD_ARGUMENTS)
    assert tool_ctx.run.used == {}
    assert rows(tool_ctx) == [("echo_text", "refused", "bad arguments")] * 4
    assert asyncio.run(run_tool(tool_ctx, "echo_text", text="a", count="2"))["count"] == 2  # lax: "2" is 2


def test_deadline_stops_a_call_and_refuses_the_next(tool_ctx, register_tool):
    async def slow(ctx: Ctx) -> dict:
        """Sleep (test only)."""
        await asyncio.sleep(5)
        return {}

    register_tool(slow)
    tool_ctx.run.seconds = 0.05
    assert asyncio.run(run_tool(tool_ctx, "slow")) == refuse(LIMIT_REACHED)
    assert asyncio.run(run_tool(tool_ctx, "slow")) == refuse(LIMIT_REACHED)
    assert rows(tool_ctx) == [("slow", "failed", "time limit"), ("slow", "refused", "time limit")]


def test_a_raising_tool_never_raises(tool_ctx, register_tool):
    async def broken(ctx: Ctx) -> dict:
        """Fail (test only)."""
        raise RuntimeError(f"SECRET source text {PAGE_TEXT}")

    register_tool(broken, label="Broken source")
    assert asyncio.run(run_tool(tool_ctx, "broken")) == refuse(TOOL_FAILED)
    assert rows(tool_ctx) == [("broken", "failed", "RuntimeError")]
    assert gaps(tool_ctx) == [("broken", "Broken source failed (RuntimeError)")]
    assert tool_ctx.run.failures == [("broken", "RuntimeError")]


def test_source_skipped_is_a_skip_and_a_gap(tool_ctx, register_tool):
    async def keyless(ctx: Ctx) -> dict:
        """Skip (test only)."""
        raise SourceSkipped("EXAMPLE_TOKEN is not set")

    register_tool(keyless)
    assert asyncio.run(run_tool(tool_ctx, "keyless")) == {"error": "source not available: EXAMPLE_TOKEN is not set"}
    assert rows(tool_ctx) == [("keyless", "skipped", "EXAMPLE_TOKEN is not set")]
    assert gaps(tool_ctx) == [("keyless", "EXAMPLE_TOKEN is not set")]


def test_refusal_reply_too_long_and_not_a_dict(tool_ctx, register_tool):
    async def picky(ctx: Ctx, kind: Literal["refuse", "long", "list"]) -> dict:
        """Misbehave (test only)."""
        return {"refuse": refuse("not allowed here"), "long": {"x": "a" * 9_000}, "list": ["a"]}[kind]

    register_tool(picky, cap=5)
    assert asyncio.run(run_tool(tool_ctx, "picky", kind="refuse")) == {"error": "not allowed here"}
    assert asyncio.run(run_tool(tool_ctx, "picky", kind="long")) == {"error": "tool reply too long"}
    assert asyncio.run(run_tool(tool_ctx, "picky", kind="list")) == refuse(TOOL_FAILED)
    assert [r[1:] for r in rows(tool_ctx)] == [
        ("refused", "not allowed here"), ("failed", "reply too long"), ("failed", "TypeError")]
    assert tool_ctx.run.used == {"picky": 3}  # all three count against the cap


def test_step_limit_ends_the_job_done(tool_ctx, echo, adk_model, monkeypatch):
    monkeypatch.setattr(config, "MAX_AGENT_CALLS", 2)
    adk_model.script = [call("echo_text", text="a"), call("echo_text", text="b"), call("echo_text", text="c")]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done" and (job.steps[0].status, job.steps[0].detail) == ("done", "step limit")


def test_time_limit_ends_the_job_done(tool_ctx, register_tool, adk_model, monkeypatch):
    async def wait(ctx: Ctx) -> dict:
        """Wait (test only)."""
        await asyncio.sleep(5)
        return {}

    register_tool(wait)
    monkeypatch.setattr(config, "RESEARCH_SECONDS", 0.3)
    adk_model.script = [call("wait"), HANG]
    asyncio.run(jobs.run_research(tool_ctx.job_id, tool_ctx.dossier_id, EXPERT))
    job = store.get_job(tool_ctx.job_id)
    assert job.status == "done" and job.steps[0].detail.startswith("time limit")
    assert [(a.status, a.detail) for a in job.activity] == [("failed", "time limit")]


def test_fetch_ok_refuses_banned_api_and_blocked_sites(tool_ctx):
    memory.block_site("www.Records.Example.gov", "captcha", "https://records.example.gov/search")
    refused = {
        "https://www.linkedin.com/in/alex-rivera": tools.BANNED_SITE,
        "https://scholar.google.com/citations?user=x": tools.BANNED_SITE,
        "https://www.dauberttracker.com/x": tools.BANNED_SITE,
        "https://www.courtlistener.com/opinion/1/x/": tools.API_SITE,
        "https://api.openalex.org/works": tools.API_SITE,
        "ftp://example.org/file": tools.NOT_HTTP,
        "not a url": tools.NOT_HTTP,
        "https://records.example.gov/case/1": "blocked site: captcha",
        "https://county.records.example.gov/case/1": "blocked site: captcha",
    }
    for url, text in refused.items():
        assert tool_ctx.fetch_ok(url) == text, url
        with pytest.raises(ValueError):
            tool_ctx.save_file(url, b"x", "application/pdf")
    assert tool_ctx.fetch_ok("https://example.gov/case/1") is None
    assert memory.files(tool_ctx.dossier_id) == []


def test_add_finding(tool_ctx):
    assert tool_ctx.add_finding("web_testimony", "Profile", "https://www.linkedin.com/in/alex-rivera") is None
    assert tool_ctx.add_finding("web_testimony", "Page", "javascript:alert(1)") is None
    ref = tool_ctx.add_finding("web_testimony", "T" * 300, "https://example.org/a", facts={"year": 2019})
    assert ref.startswith("fnd_")
    assert tool_ctx.add_finding("web_testimony", "Again", "https://example.org/a") == ref  # same kind + URL
    other = tool_ctx.add_finding("court_opinion", "Doe", "https://www.courtlistener.com/opinion/1/doe/",
                                 confidence="high")
    with pytest.raises(ValueError):
        tool_ctx.add_finding("web_testimony", "Big", "https://example.org/b", facts={"x": "a" * 5_000})
    found = tool_ctx.findings()
    assert [f.finding_id for f in found] == [ref, other]
    assert len(found[0].title) == 200 and found[0].collector == "main_agent"
    assert found[1].source_type == "courtlistener_opinion" and found[1].confidence == "high"
    assert [f.finding_id for f in tool_ctx.findings("court_opinion")] == [other]


def test_files_on_disk_with_limits(tool_ctx, monkeypatch, tmp_path):
    file_id = add_test_file(tool_ctx, PAGE_MARKDOWN)
    row = memory.file(file_id)
    assert row["dossier_id"] == tool_ctx.dossier_id and row["path"].endswith(".pdf")
    assert row["path"].startswith(str(tmp_path / "data" / "files" / tool_ctx.dossier_id))
    assert tool_ctx.file_text(file_id) == PAGE_MARKDOWN and tool_ctx.file_text(file_id, 10) == PAGE_MARKDOWN[:10]
    other = Ctx(job_id="job_x", dossier_id="dos_other", expert=EXPERT, run=tool_ctx.run)
    assert other.file_text(file_id) == "" and tool_ctx.file_text("file_nope") == ""
    monkeypatch.setattr(tools, "FILE_MAX", 10)
    with pytest.raises(ValueError, match="too large"):
        tool_ctx.save_file("https://example.edu/big.pdf", b"x" * 11, "application/pdf")


def test_page_text_never_stored(tool_ctx, register_tool, tmp_path):
    """7f: page text may be in a tool's reply (memory only) and in files on disk (D1), never in the
    database: not in findings, gaps, memories or activity rows."""

    async def read_page_test(ctx: Ctx, url: str) -> dict:
        """Read a page (test only)."""
        ref = ctx.add_finding("page_credential", "Board certification", url, facts={"credential": "toxicology"})
        ctx.save_file(url, PAGE_MARKDOWN.encode(), "text/html", text=PAGE_MARKDOWN)
        memory.save_manual("example.edu", "Faculty pages list a CV link.")
        ctx.log("1 credential saved")
        return {"refs": [ref], "page": PAGE_MARKDOWN}

    register_tool(read_page_test)
    reply = asyncio.run(run_tool(tool_ctx, "read_page_test", url="https://example.edu/faculty/rivera"))
    assert PAGE_TEXT in reply["page"]
    assert rows(tool_ctx) == [("read_page_test", "done", "1 credential saved")]
    db = (tmp_path / "data" / "test.db").read_bytes()
    assert PAGE_TEXT.encode() not in db
    assert PAGE_TEXT not in store.get_job(tool_ctx.job_id).model_dump_json()


def test_ask_model_drops_copied_text_and_cuts(tool_ctx, monkeypatch):
    schema = {"type": "object", "properties": {"credential": {"type": "string", "maxLength": 20},
                                               "quotes": {"type": "array", "items": {"type": "string"}}}}
    fake = FakeModel(json=[{"credential": "Board-certified in toxicology", "quotes": [PAGE_MARKDOWN[:80], "own words"]}])
    monkeypatch.setattr(llm, "call_model", fake)
    answer = asyncio.run(tool_ctx.ask_model("Extract credentials.", PAGE_MARKDOWN, schema))
    assert answer == {"credential": "Board-certified in t", "quotes": ["own words"]}


def test_sub_agent_tools_go_through_the_wrapper(tool_ctx, echo, register_tool, adk_model):
    async def helper(ctx: Ctx) -> dict:
        """Run a small agent (test only)."""
        run = await ctx.sub_agent("Echo once.", "Go.", tools=["echo_text"])
        ctx.log(f"{run.calls} helper calls")
        return {"text": run.text}

    register_tool(helper, label="Helper")
    adk_model.script = [call("echo_text", text="inner"), "helper done"]
    assert asyncio.run(run_tool(tool_ctx, "helper")) == {"text": "helper done"}
    assert rows(tool_ctx) == [("helper", "done", "2 helper calls"), ("echo_text", "done", "1 echoed")]


def test_call_tool_from_a_tool(tool_ctx, echo, register_tool):
    async def outer(ctx: Ctx) -> dict:
        """Call another tool (test only)."""
        return await ctx.call_tool("echo_text", text="nested")

    register_tool(outer)
    assert asyncio.run(run_tool(tool_ctx, "outer"))["text"] == "nested"
    assert asyncio.run(run_tool(tool_ctx, "no_such_tool")) == {"error": "unknown tool"}
    assert [r[0] for r in rows(tool_ctx)] == ["outer", "echo_text"]


def test_finding_with_a_secret_is_not_saved(tool_ctx, tmp_path):
    """Gotcha 45: QA probe, a password inside finding facts (nested), a title or a note."""
    url = "https://example.org/a"
    assert tool_ctx.add_finding("web_testimony", "Page", url, facts={"login": {"notes": ["password: hunter2"]}}) is None
    assert tool_ctx.add_finding("web_testimony", "token=abc123def", url) is None
    assert tool_ctx.add_finding("web_testimony", "Page", url, note="card 4111-1111-1111-1111") is None
    assert tool_ctx.findings() == []
    assert gaps(tool_ctx) == [("main_agent", tools.SECRET_DROPPED)] * 3
    assert tool_ctx.add_finding("web_testimony", "Page", url, facts={"orcid": "0000-0002-1825-0097"})
    db = (tmp_path / "data" / "test.db").read_bytes()
    assert b"hunter2" not in db and b"abc123" not in db and b"4111-1111" not in db and b"4111 1111" not in db


def test_activity_hides_secrets_and_cuts_nested_strings(tool_ctx, register_tool, tmp_path):
    """Gotcha 45: QA probe, a token in a tool argument; nested strings cut to 100 chars."""

    async def lookup_site(ctx: Ctx, query: str, terms: list[str], log: str = "ok") -> dict:
        """Look up a site (test only)."""
        ctx.log(log)
        return {"ok": True}

    register_tool(lookup_site)
    asyncio.run(run_tool(tool_ctx, "lookup_site", query="token=abc123secretvalue", terms=["x" * 300, "plain"],
                         log="password: hunter2"))
    asyncio.run(run_tool(tool_ctx, "lookup_site", query="https://example.gov/?q=1", terms=["4111 1111 1111 1111"]))
    first, second = store.activity(tool_ctx.job_id)
    assert first.args == {"query": "[hidden]", "terms": ["x" * 100, "plain"], "log": "[hidden]"}
    assert first.detail == "[hidden]"
    assert second.args == {"query": "https://example.gov/?q=1", "terms": ["[hidden]"]} and second.detail == "ok"
    db = (tmp_path / "data" / "test.db").read_bytes()
    assert b"hunter2" not in db and b"abc123" not in db and b"4111-1111" not in db and b"4111 1111" not in db
