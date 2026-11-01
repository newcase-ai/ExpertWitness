"""Tool 2: sites for a state (DESIGN tool card 2) with a fake llm.call_model. Synthetic data only."""

import asyncio

import pytest

from app import llm, memory
from app.tools.state_sites import UNKNOWN_STATE, is_official
from tests.fakes import FakeModel, run_tool, tool_ctx_fixture  # noqa: F401 - fixtures


def hit(domain, title="Official site"):
    return {"url": f"https://{domain}/search", "title": title, "snippet": "Search records."}


def label(ref, what="court case lookup", reliability="high", useful=True, people_search=False):
    return {"ref": ref, "what": what, "reliability": reliability, "useful": useful,
            "people_search": people_search}


@pytest.fixture(name="model")
def model_fixture(monkeypatch):
    def setup(web, labels):
        fake = FakeModel(web=web, json=[{"sites": labels}])
        monkeypatch.setattr(llm, "call_model", fake)
        return fake
    return setup


def saved(state="OH"):
    return sorted(r["domain"] for r in memory.sites_in(state))


def test_three_searches_then_memory(tool_ctx, model):
    fake = model([[hit("courts.ohio.gov")], [hit("med.ohio.gov")], [hit("elicense.ohio.gov")]],
                 [label("S1"), label("S2", "physician licence lookup"), label("S3", "licence lookup")])
    first = asyncio.run(run_tool(tool_ctx, "sites_for_state", state="Ohio"))
    assert first["from_memory"] is False
    assert [s["domain"] for s in first["sites"]] == ["courts.ohio.gov", "elicense.ohio.gov", "med.ohio.gov"]
    assert sum(c["web_search"] for c in fake.calls) == 3
    calls = len(fake.calls)
    again = asyncio.run(run_tool(tool_ctx, "sites_for_state", state="oh"))
    assert again["from_memory"] is True and again["sites"] == first["sites"]
    assert len(fake.calls) == calls  # no model call


def test_banned_domain_never_saved(tool_ctx, model):
    fake = model([[hit("linkedin.com"), hit("courts.ohio.gov")], [], []], [label("S1")])
    asyncio.run(run_tool(tool_ctx, "sites_for_state", state="OH"))
    assert saved() == ["courts.ohio.gov"]
    label_input = fake.calls[-1]["input"]
    assert "linkedin" not in label_input


def test_keep_rule(tool_ctx, model):
    model([[hit("records-vendor.com"), hit("old.ohio.gov"), hit("ohiomedboard.org")],
           [hit("people-finder.com"), hit("info.ohio.gov")], []],
          [label("S1", reliability="low"), label("S2", reliability="medium"), label("S3"),
           label("S4", people_search=True), label("S5", useful=False)])
    # official first in each search: S1 old.ohio.gov, S2 records-vendor.com, S3 ohiomedboard.org,
    # S4 info.ohio.gov, S5 people-finder.com. Only S3 is high, useful and not people-search.
    result = asyncio.run(run_tool(tool_ctx, "sites_for_state", state="OH"))
    assert saved() == ["ohiomedboard.org"]
    assert result["sites"] == [{"domain": "ohiomedboard.org", "what": "court case lookup",
                                "reliability": "high", "blocked": None}]


def test_old_junk_hidden_and_searched_again(tool_ctx, model):
    memory.save_state_site("OH", "records-directory.net", "records directory", "low")
    memory.save_state_site("OH", "portal.ohio.gov", "state portal", "medium")
    fake = model([[hit("courts.ohio.gov")], [], []], [label("S1")])
    result = asyncio.run(run_tool(tool_ctx, "sites_for_state", state="OH"))
    assert result["from_memory"] is False and fake.calls
    assert [s["domain"] for s in result["sites"]] == ["courts.ohio.gov"]


def test_same_domain_from_two_searches_once(tool_ctx, model):
    fake = model([[hit("courts.ohio.gov")], [hit("courts.ohio.gov"), hit("med.ohio.gov")], []],
                 [label("S1"), label("S2", "physician licence lookup")])
    asyncio.run(run_tool(tool_ctx, "sites_for_state", state="OH"))
    assert saved() == ["courts.ohio.gov", "med.ohio.gov"]
    assert fake.calls[-1]["input"].count("courts.ohio.gov") == 1


def test_unknown_state_refused(tool_ctx, model):
    fake = model([], [])
    assert asyncio.run(run_tool(tool_ctx, "sites_for_state", state="Narnia")) == {"error": UNKNOWN_STATE}
    assert fake.calls == []


def test_is_official():
    assert is_official("azmd.gov") and is_official("tmb.state.tx.us") and is_official("co.pima.az.us")
    assert not is_official("arizonacourt.us") and not is_official("illinoispeoplesearch.us")
    assert not is_official("ncmedboard.org")
