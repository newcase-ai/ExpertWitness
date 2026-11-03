"""The structured expert profile (app/expert_profile.py): the final model call, the code checks on
its output, the code-built lists and statuses, the job step and the endpoints. FakeAdkModel replies;
synthetic people only (gotchas 8, 9)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import expert_profile, jobs, store
from app.main import app
from tests.fakes import EXPERT, add_test_file, tool_ctx_fixture  # noqa: F401 - fixture

CV = """# Alex J. Rivera, PhD
1200 Example Parkway, Suite 300
Springfield, OH 45500
Office: (555) 010-4477 | arivera@example-tox.test | www.example-tox.test

## Experience
Example Toxicology LLC, Principal, 2015-Present
Example State University, Professor of Toxicology, 2004-2015
"""


def sourced(value, *refs, **more):
    return {"value": value, "refs": list(refs), "confidence": "high", "source_kind": "self_reported",
            "note": None} | more


def empty_reply(**fields):
    blank = sourced("")
    reply = {
        "headline": blank, "summary": blank, "specialties": [], "expertise_tags": [],
        "contact": {k: blank for k in ("based_in", "business_address", "phone", "email", "website")}
        | {"current_role": {"title": "", "org": "", "refs": [], "confidence": "low"}},
        "highlights": [], "career": [], "education": [], "licenses": [], "board_certifications": [],
        "affiliations": [], "memberships": [], "fees": [], "testimony_summary": [],
        "license_status": {"status": "not_checked", "summary": "", "refs": []},
        "board_discipline": {"status": "not_checked", "summary": "", "refs": []},
        "red_flags": [], "not_him": [],
    }
    return reply | fields


def finish(ctx, tools=()):
    for tool in tools:
        store.start_activity(ctx.job_id, tool, tool, {}, status="done")
    store.finish_job(ctx.job_id, "done")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_build_organizes_the_cv_and_drops_what_no_source_says(tool_ctx, adk_model):
    file_id = add_test_file(tool_ctx, CV)
    fact = tool_ctx.add_finding("web_credential", "SYNTHETIC board page", "https://example.org/board",
                                facts={"facts": ["Ohio license 99999 is active."]})
    contact = empty_reply()["contact"] | {
        "business_address": sourced("1200 Example Parkway, Suite 300, Springfield, OH 45500", file_id),
        "phone": sourced("555-010-4477", file_id),
        "email": sourced("arivera@other.test", file_id),  # not in the CV: invented
        "website": sourced("https://www.example-tox.test/", file_id),
        "based_in": sourced("Springfield, Ohio", file_id),
        "current_role": {"title": "Principal", "org": "Example Toxicology LLC", "refs": [file_id],
                         "confidence": "high"},
    }
    reply = empty_reply(
        headline=sourced("Toxicologist and consultant", file_id),
        summary=sourced("SYNTHETIC uncited", "fnd_nope"),
        contact=contact,
        career=[{"title": "Principal", "org": "Example Toxicology LLC", "location": None, "start_year": 2015,
                 "end_year": None, "current": True, "refs": [file_id], "confidence": "medium"},
                {"title": "", "org": "No title", "location": None, "start_year": None, "end_year": None,
                 "current": False, "refs": [file_id], "confidence": "low"}],
        licenses=[{"kind": "Medical", "state": "OH", "number": "99999", "status": "active", "since": None,
                   "refs": [fact], "confidence": "high"}],
        license_status={"status": "clear", "summary": "Ohio license 99999 active", "refs": [fact]},
    )
    adk_model.script = [json.dumps(reply)]
    facts = asyncio.run(expert_profile.build(tool_ctx.dossier_id, EXPERT))

    prompt = adk_model.seen[0].contents[-1].parts[0].text
    assert f"=== [{file_id}]" in prompt and "1200 Example Parkway" in prompt and f"[{fact}]" in prompt
    assert facts.headline.value == "Toxicologist and consultant"
    assert facts.summary is None  # cites no real finding
    c = facts.contact
    assert c.business_address and c.phone and c.website and c.based_in and c.current_role
    assert c.email is None  # not written in the cited CV
    assert [r.title for r in facts.career] == ["Principal"]  # the role with no title is dropped
    assert facts.career[0].confidence == "low"  # "medium" is not a level: back to low
    assert facts.licenses[0].number == "99999" and facts.license_status.status == "clear"
    assert store.get_profile_facts(tool_ctx.dossier_id)[0]["headline"]["value"] == "Toxicologist and consultant"


@pytest.mark.parametrize(("field", "value", "ok"), [
    ("phone", "+1 555 010 4477", True),
    ("phone", "555-010-9999", False),
    ("phone", "4477", False),
    ("business_address", "1200 Example Parkway Suite 300 Springfield OH", True),
    ("business_address", "1200 Example Parkway, Suite 301", False),
    ("website", "http://example-tox.test", True),
    ("email", "ARIVERA@example-tox.test", True),
])
def test_contact_values_must_be_written_in_the_source(field, value, ok):
    assert expert_profile.appears(field, value, CV) is ok


def test_view_builds_lists_from_findings_and_statuses_from_tools(tool_ctx, client):
    docket = tool_ctx.add_finding(
        "court_docket", "Doe v. Example Co.", "https://www.courtlistener.com/docket/1/doe-v-example-co/",
        facts={"case_name": "Doe v. Example Co.", "court": "District Court, N.D. Example", "docket_number": "1:20-cv-1",
               "date_filed": "2020-03-01", "match": {"level": "high", "role": "expert witness"}}, confidence="high")
    tool_ctx.add_finding("court_docket", "Roe v. Other", "https://www.courtlistener.com/docket/2/roe-v-other/",
                         facts={"case_name": "Roe v. Other", "match": {"level": "ruled out"}})
    tool_ctx.add_finding("work", "SYNTHETIC paper", "https://openalex.org/W1",
                         facts={"year": 2019, "venue": "Example Journal", "cited_by_count": 4}, confidence="high")
    tool_ctx.add_finding("author_stats", "OpenAlex author", "https://openalex.org/A1",
                         facts={"works_count": 3, "cited_by_count": 10, "h_index": 2}, confidence="high")
    tool_ctx.add_finding("author_stats", "OpenAlex author 2", "https://openalex.org/A2",
                         facts={"works_count": 1, "cited_by_count": 1, "h_index": 1}, confidence="high")
    finish(tool_ctx, tools=("court_records", "exclusion_check"))
    assert client.get(f"/dossiers/{tool_ctx.dossier_id}/expert-profile").status_code == 200

    v = expert_profile.view(tool_ctx.dossier_id)
    assert v.built is False and v.career.status == "not_checked"  # never built: not "none found"
    [case] = v.cases.items  # the ruled-out case is left out
    assert (case.name, case.year, case.involvement, case.side, case.refs) == (
        "Doe v. Example Co.", 2020, "expert witness", "unknown", [docket])
    assert v.documents.status == "none_found"  # the court tool ran and found no filings
    assert v.publications.status == "found" and len(v.publications.items) == 1
    assert (v.publications.stats.works, v.publications.stats.citations, v.publications.stats.h_index) == (4, 11, 2)
    assert v.risk.exclusion_lists.status == "clear" and v.risk.retractions.status == "not_checked"
    assert v.risk.license_status.status == "not_checked"
    assert docket in [s.ref for s in v.sources]


def test_endpoints_404_and_409(client, tool_ctx):
    assert client.get("/dossiers/dos_nope/expert-profile").status_code == 404
    assert client.get(f"/dossiers/{tool_ctx.dossier_id}/expert-profile").status_code == 409  # still queued
    assert client.get("/dossiers/dos_nope/files").status_code == 404


def test_files_lists_downloads_without_text(client, tool_ctx):
    file_id = add_test_file(tool_ctx, CV)
    [row] = client.get(f"/dossiers/{tool_ctx.dossier_id}/files").json()
    assert row["file_id"] == file_id and row["has_text"] is True and row["mime"] == "application/pdf"
    assert "path" not in row and "text_path" not in row


def test_a_failed_build_is_a_gap_and_the_run_stays_done(tool_ctx, adk_model, monkeypatch):
    monkeypatch.setattr(expert_profile, "AUTO_BUILD", True)
    tool_ctx.add_finding("web_fact", "SYNTHETIC page", "https://example.org/a", facts={"facts": ["x"]})
    adk_model.script = ["not json", "not json"]
    asyncio.run(jobs.build_expert_profile(tool_ctx.dossier_id, EXPERT))
    gaps = store.get_research(tool_ctx.dossier_id).gaps
    assert [g.step for g in gaps] == ["expert_profile"]
    assert store.get_profile_facts(tool_ctx.dossier_id) is None


def test_a_case_the_model_rules_out_leaves_the_case_list(tool_ctx, adk_model):
    url = "https://www.courtlistener.com/docket/3/poe-v-demo/"
    docket = tool_ctx.add_finding("court_docket", "Poe v. Demo", url,
                                  facts={"case_name": "Poe v. Demo", "match": {"level": "low", "role": "mentioned only"}})
    filing = tool_ctx.add_finding("court_file", "Exhibit 1", url + "1/",
                                  facts={"case_name": "Poe v. Demo", "case_ref": docket})
    adk_model.script = [json.dumps(empty_reply(not_him=[sourced("Poe v. Demo cites a paper by the same name", docket)]))]
    asyncio.run(expert_profile.build(tool_ctx.dossier_id, EXPERT))
    finish(tool_ctx, tools=("court_records",))
    v = expert_profile.view(tool_ctx.dossier_id)
    assert v.cases.items == [] and v.documents.items == [] and v.cases.status == "none_found"
    assert [x.refs for x in v.not_him.items] == [[docket]] and filing not in [s.ref for s in v.sources]
