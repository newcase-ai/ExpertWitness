"""The expert profile (app/profile.py): the automatic update after tool calls, the code checks on
the model's items, the agent's read/check tools and GET /dossiers/{id}/profile. FakeAdkModel
replies; synthetic people only."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import profile, store
from app.main import app
from app.models import PROFILE_SECTIONS, Profile, ProfileItem
from tests.fakes import register_tool_fixture, run_tool, tool_ctx_fixture  # noqa: F401 - fixtures

EMPTY = {s: [] for s in PROFILE_SECTIONS}


def reply(changes="SYNTHETIC changes", **sections):
    return json.dumps(EMPTY | sections | {"changes": changes})


def item(text, *refs, confidence="high"):
    return {"text": text, "refs": list(refs), "confidence": confidence}


def rows(ctx):
    return [(a.tool, a.status, a.detail) for a in store.activity(ctx.job_id)]


@pytest.fixture
def auto(monkeypatch):
    monkeypatch.setattr(profile, "AUTO_UPDATE", True)


@pytest.fixture
def saving(register_tool):
    async def save_case(ctx, case: str) -> dict:
        """Save one case."""
        return {"ref": ctx.add_finding("web_testimony", f"SYNTHETIC {case}", "https://example.org/case",
                                       facts={"facts": [f"SYNTHETIC: expert in {case}"]}, confidence="high")}

    async def nothing(ctx) -> dict:
        """Save nothing."""
        return {"ok": True}

    register_tool(save_case, label="Save a case")
    register_tool(nothing, label="Nothing")


def test_a_tool_that_saves_findings_updates_the_profile(tool_ctx, adk_model, auto, saving, monkeypatch):
    new_id = store.new_id
    monkeypatch.setattr(store, "new_id", lambda prefix: "fnd_case" if prefix == "fnd" else new_id(prefix))
    adk_model.script = [reply(cases=[item("SYNTHETIC Doe v. Example Co.: plaintiff's expert", "fnd_case")],
                              not_him=[item("SYNTHETIC uncited", "fnd_nope")])]
    assert asyncio.run(run_tool(tool_ctx, "nothing")) == {"ok": True}  # nothing saved: no update
    assert asyncio.run(run_tool(tool_ctx, "save_case", case="Doe v. Example Co.")) == {"ref": "fnd_case"}
    current = store.get_profile(tool_ctx.dossier_id)
    assert current.version == 1 and current.updated_at
    assert current.cases == [ProfileItem(text="SYNTHETIC Doe v. Example Co.: plaintiff's expert",
                                         refs=["fnd_case"], confidence="high")]
    assert current.not_him == []  # an item citing no real finding is dropped
    [prompt] = [r.contents[-1].parts[0].text for r in adk_model.seen]  # one model call: the update
    assert "[fnd_case] web_testimony: SYNTHETIC Doe v. Example Co.; SYNTHETIC: expert in Doe v. Example Co." in prompt
    assert "Current profile:\n(empty)" in prompt and "Expert: Alex Rivera | toxicology | Ohio, US" in prompt
    assert rows(tool_ctx) == [("nothing", "done", None), ("save_case", "done", None),
                              ("update_profile", "done", "version 1, 1 new findings, 1 items")]


def test_the_model_s_items_are_checked_by_code(tool_ctx):
    a = tool_ctx.add_finding("web_fact", "SYNTHETIC a", "https://example.org/a")
    b = tool_ctx.add_finding("web_fact", "SYNTHETIC b", "https://example.org/b")
    rows_in = [item("SYNTHETIC keep", a, "fnd_nope", a, confidence="maybe"), item("SYNTHETIC no refs"),
               {"text": "", "refs": [b]}, "not an item", *[item(f"SYNTHETIC {n}", b) for n in range(20)]]
    kept = profile.items_of(rows_in, {a, b})
    assert kept[0] == ProfileItem(text="SYNTHETIC keep", refs=[a], confidence="low")
    assert len(kept) == profile.ITEMS_MAX and all(i.refs for i in kept)


def test_a_failed_update_is_a_gap_and_the_old_profile_stays(tool_ctx, adk_model, auto, saving):
    store.save_profile(Profile(dossier_id=tool_ctx.dossier_id, version=3, updated_at="2026-11-01T00:00:00Z",
                               expertise=[ProfileItem(text="SYNTHETIC toxicology", refs=["fnd_x"])]))
    adk_model.script = ["not json", "still not json"]
    assert "ref" in asyncio.run(run_tool(tool_ctx, "save_case", case="Doe"))
    assert store.get_profile(tool_ctx.dossier_id).version == 3
    assert rows(tool_ctx)[-1] == ("update_profile", "failed", "ModelError")
    assert [(g.step, g.reason) for g in store.get_research(tool_ctx.dossier_id).gaps] == [
        ("update_profile", "profile not updated (ModelError)")]


def test_new_findings_shown_to_the_model_are_capped_papers_last(tool_ctx, adk_model):
    works = [tool_ctx.add_finding("work", f"SYNTHETIC paper {n}", f"https://example.org/w{n}") for n in range(45)]
    case = tool_ctx.add_finding("court_opinion", "SYNTHETIC Doe v. Example", "https://example.org/op",
                                facts={"court": "SYNTHETIC court", "date_filed": "2020-01-01"})
    adk_model.script = [reply()]
    asyncio.run(profile.update(tool_ctx.dossier_id, tool_ctx.expert, tool_ctx.findings(), set(works) | {case}))
    prompt = adk_model.seen[-1].contents[-1].parts[0].text
    lines = [line for line in prompt.splitlines() if line.startswith("[fnd_")]
    assert len(lines) == profile.NEW_MAX and lines[0].startswith(f"[{case}] court_opinion")
    assert "SYNTHETIC court; 2020-01-01" in lines[0] and "(and 6 more findings, mostly publications)" in prompt


def test_a_court_finding_line_carries_the_role_and_the_reason(tool_ctx):
    ref = tool_ctx.add_finding("court_docket", "SYNTHETIC Doe v. Example", "https://example.org/d", facts={
        "court": "SYNTHETIC court", "date_filed": "2021-05-01", "nature_of_suit": "791 ERISA",
        "match": {"score": 85, "level": "high", "role": "consultant or reviewer",
                  "reasons": ["SYNTHETIC same specialty"], "checked_by": "ai"}})
    [found] = tool_ctx.findings()
    assert profile.finding_line(found, with_id=True) == (
        f"[{ref}] court_docket: SYNTHETIC Doe v. Example; SYNTHETIC court; 2021-05-01; 791 ERISA; "
        "his role: consultant or reviewer (high match); SYNTHETIC same specialty")


def test_known_so_far_uses_the_profile_once_there_is_one(tool_ctx):
    tool_ctx.add_finding("web_fact", "SYNTHETIC raw finding", "https://example.org/a")
    assert "- web_fact: SYNTHETIC raw finding" in profile.known_so_far(tool_ctx)
    assert profile.context_label(tool_ctx) == "no profile yet, 1 findings"
    store.save_profile(Profile(dossier_id=tool_ctx.dossier_id, version=1, updated_at="t",
                               not_him=[ProfileItem(text="SYNTHETIC a judge in another state", refs=["fnd_1"])]))
    text = profile.known_so_far(tool_ctx)
    assert profile.context_label(tool_ctx) == "profile v1, 1 items"
    assert "Not him (same name, another person):\n- SYNTHETIC a judge in another state [fnd_1] (unconfirmed)" in text
    assert "SYNTHETIC raw finding" not in text and "Field: toxicology" in text


def test_read_profile_and_check_against_it(tool_ctx, adk_model):
    assert asyncio.run(run_tool(tool_ctx, "read_profile")) == {"version": 0, "profile": "empty: nothing found yet"}
    ref = tool_ctx.add_finding("site_hit", "SYNTHETIC Rivera, police chief", "https://example.org/chief")
    store.save_profile(Profile(dossier_id=tool_ctx.dossier_id, version=2, updated_at="t",
                               expertise=[ProfileItem(text="SYNTHETIC toxicologist", refs=[ref], confidence="high")]))
    read = asyncio.run(run_tool(tool_ctx, "read_profile"))
    assert read == {"version": 2, "profile": f"Expertise:\n- SYNTHETIC toxicologist [{ref}]"}

    adk_model.script = ['{"same_person": 140, "fit": "contradicts", "answer": "SYNTHETIC a police chief"}',
                        '{"same_person": 80, "fit": "odd", "answer": "SYNTHETIC same field"}']
    by_ref = asyncio.run(run_tool(tool_ctx, "check_against_profile", item=ref, question="Same person?"))
    assert by_ref == {"same_person": 100, "fit": "contradicts", "answer": "SYNTHETIC a police chief"}
    prompt = adk_model.seen[-1].contents[-1].parts[0].text
    assert "site_hit: SYNTHETIC Rivera, police chief | https://example.org/chief" in prompt
    assert "SYNTHETIC toxicologist" in prompt and "Question: Same person?" in prompt
    by_text = asyncio.run(run_tool(tool_ctx, "check_against_profile", item="SYNTHETIC search result text"))
    assert by_text == {"same_person": 80, "fit": "unrelated", "answer": "SYNTHETIC same field"}
    assert asyncio.run(run_tool(tool_ctx, "check_against_profile", item="fnd_nope")) == {"error": "unknown finding ref"}
    assert tool_ctx.findings() == [tool_ctx.findings()[0]]  # nothing saved by either tool


def test_profile_api(tool_ctx):
    ref = tool_ctx.add_finding("web_fact", "SYNTHETIC page", "https://example.org/a")
    store.save_profile(Profile(dossier_id=tool_ctx.dossier_id, version=1, updated_at="t",
                               cases=[ProfileItem(text="SYNTHETIC case", refs=[ref])]))
    with TestClient(app) as client:
        body = client.get(f"/dossiers/{tool_ctx.dossier_id}/profile").json()
        assert body["version"] == 1 and body["cases"] == [{"text": "SYNTHETIC case", "refs": [ref], "confidence": "low"}]
        assert body["sources"] == {ref: {"url": "https://example.org/a", "title": "SYNTHETIC page"}}
        assert client.get("/dossiers/dos_nope/profile").status_code == 404
