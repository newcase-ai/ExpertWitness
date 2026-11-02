"""Source client app/sources/courtlistener.py: search, opinion passages, RECAP dockets.
SYNTHETIC data only (gotcha 8)."""

import asyncio
import time

import httpx
import pytest

from app import http, store
from app.sources import SourceFailed, SourceSkipped, courtlistener
from tests.fakes import COURTLISTENER_SEARCH, NAME, sources_fixture  # noqa: F401 - fixture


def test_courtlistener_without_token_is_skipped(sources, monkeypatch):
    _, seen = sources()
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    with pytest.raises(SourceSkipped, match="COURTLISTENER_TOKEN is not set"):
        asyncio.run(courtlistener.search_opinions(NAME))
    assert seen == []


def test_courtlistener_search_uses_full_names_and_token(sources):
    _, seen = sources()
    hits = asyncio.run(courtlistener.search_opinions("Alex J. Rivera"))
    q = seen[0].url.params["q"]
    near = " OR ".join(f'"Alex Rivera {w}"~5' for w in ("expert", "experts", "Daubert"))
    assert q == f'("Alex J. Rivera" OR "Alex Rivera") AND ({near})'
    assert "testif" not in q and "deposition" not in q  # parties and lay witnesses testify too
    assert "702" not in q  # "702" also matches page cites like "F.2d at 702"
    assert hits[0]["query"] == "expert" and hits[0]["opinion_id"] == 2
    assert hits[0]["case"] == ("Doe v. Example Co.", "District Court, N.D. Example", "2019-04-02")
    assert seen[0].headers["Authorization"] == "Token test-token"
    assert hits[1]["url"] == "https://www.courtlistener.com/opinion/1/doe-v-example-co/"
    assert hits[0]["title"] == "Doe v. Example Co."
    assert "<mark>" not in hits[0]["snippet"]


@pytest.mark.parametrize("name", ["Jordan Avery\\", 'Jordan "Avery"', "Jordan* Avery?", "Jordan (Avery)~"])
def test_courtlistener_query_strips_query_breaking_characters(name):
    near = " OR ".join(f'"Jordan Avery {w}"~5' for w in ("expert", "experts", "Daubert"))
    assert courtlistener.build_query(name) == f'("Jordan Avery") AND ({near})'


def test_courtlistener_query_adds_unaccented_name():
    q = courtlistener.build_query("Dr. Søren K. García")
    assert q.startswith('("Søren K. García" OR "Søren García" OR "Soren K. Garcia" OR "Soren Garcia") AND (')
    assert '"Søren García expert"~5' in q and '"Soren Garcia Daubert"~5' in q
    assert q.count('"') % 2 == 0


def test_courtlistener_never_searches_surname_alone(sources):
    _, seen = sources()
    with pytest.raises(SourceSkipped, match="surname"):
        asyncio.run(courtlistener.search_opinions("Rivera"))
    assert seen == []


def test_courtlistener_counts_only_real_requests(sources):
    _, seen = sources()
    asyncio.run(courtlistener.search_opinions(NAME))
    asyncio.run(courtlistener.search_opinions(NAME))  # served from the SQLite cache
    assert len(seen) == 1
    assert store.calls_since("courtlistener", 3600) == 1


@pytest.mark.parametrize("junk", [
    None, [], "maintenance", {"results": "xx"}, {"results": [{"absolute_url": 5}]},
    {"results": [5]}, {"detail": "Throttled"},
])
def test_courtlistener_junk_is_an_error_and_never_cached(sources, junk):
    replies = [httpx.Response(200, json=junk), httpx.Response(200, json=COURTLISTENER_SEARCH)]
    _, seen = sources({"www.courtlistener.com": lambda request: replies.pop(0)})
    with pytest.raises(RuntimeError, match="unexpected response"):
        asyncio.run(courtlistener.search_opinions(NAME))
    hits = asyncio.run(courtlistener.search_opinions(NAME))  # recovered, not stuck on cached junk
    assert hits[0]["title"] == "Doe v. Example Co."
    assert len(seen) == 2
    assert store.calls_since("courtlistener", 3600) == 2


def test_courtlistener_odd_fields_do_not_crash(sources):
    body = {"results": [{"absolute_url": "/opinion/2/x/", "caseName": 7, "opinions": [5, {"snippet": None}]}]}
    sources({"www.courtlistener.com": (200, body)})
    hit = asyncio.run(courtlistener.search_opinions(NAME))[0]
    assert hit["title"] == "Court opinion" and hit["snippet"] == ""


def test_courtlistener_retries_a_timeout_once_and_counts_both(sources):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    _, seen = sources({"www.courtlistener.com": slow})
    with pytest.raises(SourceFailed) as err:
        asyncio.run(courtlistener.search_opinions(NAME))
    assert str(err.value) == courtlistener.TIMED_OUT and "call the tool again" in str(err.value)
    assert len(seen) == 2 and store.calls_since("courtlistener", 3600) == 2
    assert seen[0].extensions["timeout"]["read"] == 60  # longer than the other sources' 20 s


def test_courtlistener_timeout_then_answer_is_fine(sources):
    replies = [httpx.ReadTimeout("timed out"), httpx.Response(200, json=COURTLISTENER_SEARCH)]

    def flaky(request):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    _, seen = sources({"www.courtlistener.com": flaky})
    assert len(asyncio.run(courtlistener.search_opinions(NAME))) == 2 and len(seen) == 2


@pytest.mark.parametrize(("statuses", "requests", "says"), [
    ((503, 200), 2, None),  # a busy server: retried once, then fine
    ((502, 502), 2, "server error"),
    ((429,), 1, "Wait a few minutes"),  # never retried at once
    ((401,), 1, "token was refused"),
    ((404,), 1, "will not help"),
])
def test_courtlistener_http_errors_say_what_to_do(sources, statuses, requests, says):
    replies = [httpx.Response(s, json=COURTLISTENER_SEARCH if s == 200 else {}) for s in statuses]
    _, seen = sources({"www.courtlistener.com": lambda request: replies.pop(0)})
    if says is None:
        assert len(asyncio.run(courtlistener.search_opinions(NAME))) == 2
    else:
        with pytest.raises(SourceFailed, match=says):
            asyncio.run(courtlistener.search_opinions(NAME))
    assert len(seen) == requests


def test_courtlistener_timeout_while_waiting_for_its_slot_is_not_counted(sources, monkeypatch):
    _, seen = sources()
    limiter = http.RateLimiter(1.0)
    limiter.last = time.monotonic() + 60  # next slot is a minute away
    monkeypatch.setattr(http, "limiters", {"courtlistener": limiter})

    async def search():
        async with asyncio.timeout(0.05):
            await courtlistener.search_opinions(NAME)

    with pytest.raises(TimeoutError):
        asyncio.run(search())
    assert seen == [] and store.calls_since("courtlistener", 3600) == 0


def test_courtlistener_daily_cap(sources, monkeypatch):
    _, seen = sources()
    monkeypatch.setattr(store, "calls_since", lambda source, seconds: 125)
    with pytest.raises(SourceSkipped, match="limit"):
        asyncio.run(courtlistener.search_opinions(NAME))
    assert seen == []

FILLER = "The court considered the record at length. " * 20
OPINION_TEXT = (
    FILLER + "Plaintiff offered the testimony of Dr. Alex J. Rivera, a toxicologist. " + FILLER
    + "Dr. Rivera also said many things. " + FILLER
    + "The motion to exclude Rivera, Alex is denied. " + FILLER
)


def opinion_route(body):
    return {"www.courtlistener.com": lambda request: httpx.Response(200, json=body)}


# --- CourtListener opinion passages -------------------------------------------------------------


def test_opinion_passages_need_token(sources, monkeypatch):
    sources()
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.opinion_passages(1, "Alex Rivera"))


def test_opinion_passages_never_use_surname_alone(sources):
    _, seen = sources(routes=opinion_route({"id": 1, "plain_text": OPINION_TEXT}))
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.opinion_passages(1, "Dr. Rivera"))
    assert seen == []


def test_opinion_passages_centre_on_full_name(sources):
    _, seen = sources(routes=opinion_route({"id": 7, "plain_text": OPINION_TEXT, "html_with_citations": ""}))
    found = asyncio.run(courtlistener.opinion_passages(7, "Alex Rivera"))
    assert len(found) == 2  # "Dr. Rivera" alone is not a mention (gotcha 13)
    assert "Alex J. Rivera" in found[0] and "Rivera, Alex" in found[1]
    collapsed = " ".join(OPINION_TEXT.split())
    assert all(p in collapsed and len(p) <= courtlistener.PASSAGE_CHARS for p in found)
    request = seen[0]
    assert request.url.path == "/api/rest/v4/opinions/7/"
    assert request.url.params["fields"] == "id,plain_text,html_with_citations"
    assert request.headers["Authorization"] == "Token test-token"
    assert store.calls_since("courtlistener", 60) == 1
    asyncio.run(courtlistener.opinion_passages(7, "Alex Rivera"))  # cached: no request, no count
    assert len(seen) == 1 and store.calls_since("courtlistener", 60) == 1


def test_opinion_passages_from_html_with_accents_and_cap(sources):
    html = "".join(f"<p>{FILLER}Expert <a href='#'>Álex Rivera</a> &amp; co.</p>" for _ in range(5))
    sources(routes=opinion_route({"id": 8, "plain_text": "", "html_with_citations": html}))
    found = asyncio.run(courtlistener.opinion_passages(8, "Alex Rivera"))
    assert len(found) == 3
    assert all("Álex Rivera & co." in p and "<" not in p for p in found)


def test_opinion_without_name_or_text_gives_no_passages(sources):
    sources(routes=opinion_route({"id": 9, "plain_text": FILLER + "Dr. Rivera testified."}))
    assert asyncio.run(courtlistener.opinion_passages(9, "Alex Rivera")) == []


def test_opinion_passages_middle_initial_optional(sources):
    """PLAN 3l: "Morgan Q. Example" is named "Dr. Morgan Example" or "Morgan Quinn Example"."""
    text = (FILLER + "The plaintiff called Dr. Morgan Example, a neurologist. " + FILLER
            + "Morgan Quinn Example then testified. " + FILLER + "Dr. Example said more. " + FILLER
            + "Morgan Z. Example is someone else. " + FILLER)
    sources(routes=opinion_route({"id": 11, "plain_text": text}))
    found = asyncio.run(courtlistener.opinion_passages(11, "Morgan Q. Example"))
    assert len(found) == 2
    assert "Dr. Morgan Example" in found[0] and "Morgan Quinn Example" in found[1]
    assert not any("Morgan Z. Example" in p for p in found)


def test_search_hit_reads_opinion_id_not_case_id(sources):
    result = {"absolute_url": "/opinion/500/doe-v-example/", "caseName": "Doe v. Example",
              "opinions": [{"id": 701, "snippet": "plain"}, {"id": 702, "snippet": "the <mark>Alex Rivera</mark>"}]}
    assert courtlistener.to_hit(result)["opinion_id"] == 702
    result["opinions"] = [{"id": 701, "snippet": "plain"}]
    assert courtlistener.to_hit(result)["opinion_id"] == 701


# --- CourtListener RECAP dockets (court_records, court_case) -------------------------------------


def docket_result(did, court, date, snippets, party=(), attorney=()):
    return {"docket_absolute_url": f"/docket/{did}/doe-v-example-co/", "caseName": "Doe v. Example Co.",
            "court": court, "dateFiled": date, "docketNumber": f"1:{did}-cv", "suitNature": "791 ERISA",
            "party": list(party), "attorney": list(attorney),
            "recap_documents": [{"absolute_url": f"/docket/{did}/{n}/doe-v-example-co/", "snippet": s,
                                 "short_description": f"Exhibit {n}", "entry_date_filed": date}
                                for n, s in enumerate(snippets, 1)]}


def test_search_dockets_full_name_only_and_fields(sources):
    body = {"results": [docket_result(5, "District Court, D. Example", "2021-02-08",
                                      ["no match here", "SYNTHETIC-CL reviewed by Dr. <mark>Alex J. Rivera</mark>, "
                                       "M.D. &amp; toxicologist " + "y" * 500], party=["Alex Rivera", "Example Co."])]}
    _, seen = sources(routes=opinion_route(body))
    [hit] = asyncio.run(courtlistener.search_dockets("Dr. Alex J. Rivera"))
    params = seen[0].url.params
    assert params["type"] == "r" and params["highlight"] == "on"
    assert params["q"] == '"Alex J. Rivera" OR "Alex Rivera"'  # no expert-word rule, never the surname alone
    assert hit["url"] == "https://www.courtlistener.com/docket/5/doe-v-example-co/"
    assert (hit["case_name"], hit["court"], hit["docket_number"], hit["date_filed"], hit["nature_of_suit"]) == (
        "Doe v. Example Co.", "District Court, D. Example", "1:5-cv", "2021-02-08", "791 ERISA")
    assert hit["named_as"] == ["party"]
    assert hit["documents"][1] == {"description": "Exhibit 2", "date_filed": "2021-02-08",
                                   "url": "https://www.courtlistener.com/docket/5/2/doe-v-example-co/"}
    [passage] = hit["passages"]  # only the snippet that highlights a match
    assert passage.startswith("SYNTHETIC-CL reviewed by Dr. Alex J. Rivera, M.D. & toxicologist")
    assert len(passage) == courtlistener.PASSAGE_CHARS and "<" not in passage
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.search_dockets("Dr. Rivera"))


def test_find_case_query_and_best_match(sources):
    body = {"results": [
        docket_result(1, "District Court, N.D. Example", "2015-03-01", ["no match here"]),
        docket_result(2, "Example Court of Appeals", "2019-06-06",
                      ["SYNTHETIC-CL the plaintiff's expert <mark>Morgan Example</mark>, M.D."]),
    ]}
    _, seen = sources(routes=opinion_route(body))
    found = asyncio.run(courtlistener.find_case('Doe v. "Example" Co.', "Dr. Morgan Q. Example",
                                                court="example court of appeals", year=2019))
    q = seen[0].url.params["q"]
    assert q.startswith('caseName:("Doe" AND "Example") AND (')
    people = q.split(") AND (", 1)[1]
    assert '"Morgan Q. Example"' in people and '"Morgan Example"' in people and '"Example"' not in people
    assert seen[0].url.params["type"] == "r" and seen[0].url.params["highlight"] == "on"
    assert found["url"] == "https://www.courtlistener.com/docket/2/doe-v-example-co/"
    assert (found["case_name"], found["court"], found["date_filed"], found["docket_number"]) == (
        "Doe v. Example Co.", "Example Court of Appeals", "2019-06-06", "1:2-cv")
    assert found["passages"] == ["SYNTHETIC-CL the plaintiff's expert Morgan Example, M.D."]
    assert len(seen) == 1 and store.calls_since("courtlistener", 60) == 1  # no second call


def test_find_case_none_skip_and_first_by_default(sources, monkeypatch):
    sources(routes=opinion_route({"results": []}))
    assert asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Morgan Example")) is None
    body = {"results": [docket_result(3, "A", "2001-01-01", []), docket_result(4, "B", "2002-01-01", [])]}
    sources(routes=opinion_route(body))
    found = asyncio.run(courtlistener.find_case("Roe v. Example Co.", "Morgan Example", year=1990))
    assert found["url"].endswith("/docket/3/doe-v-example-co/") and found["passages"] == []
    _, seen = sources()
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Dr. Example"))
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Morgan Example"))
    assert seen == []


def test_find_case_long_cv_name_uses_party_key_words(sources):
    _, seen = sources(routes=opinion_route({"results": []}))
    asyncio.run(courtlistener.find_case(
        "Jordan Avery vs. Patrol Officer Sam Lindqvist and the Town of Brookfield", "Morgan Example"))
    q = seen[0].url.params["q"]
    assert q.startswith('caseName:("Avery" AND "Lindqvist") AND (') and '"Morgan Example"' in q
    assert "Officer" not in q and "Brookfield" not in q and len(seen) == 1


def test_case_name_query_entities_and_fallback():
    q = courtlistener.case_name_query
    assert q("Quinn Harlow, MD vs. Northwind Lifestyles, Inc., ZZQ Management, Ltd.") == \
        'caseName:("Harlow" AND "Northwind Lifestyles")'
    assert q("Ada Lyle, Ben Orr, and Cy Pratt, et al. v. City of Brookfield") == 'caseName:("Lyle" AND "Brookfield")'
    assert q('Dee (Dr. "Ann") Roe versus Acme* Corp.') == 'caseName:("Roe" AND "Acme")'
    assert q('In re "Estate" of (Lyle)') == 'caseName:"In re Estate of Lyle"'  # no "v.": phrase as before


def test_find_case_strips_highlight_tags(sources):
    result = docket_result(5, "<mark>Example</mark> Court", "2019-06-06", ["<mark>Morgan Example</mark> &amp; co"])
    result["caseName"] = "<mark>Doe</mark> v. <mark>Example</mark> Co. &amp; Sons"
    result["docketNumber"] = "<em>1:5-cv</em>"
    sources(routes=opinion_route({"results": [result]}))
    found = asyncio.run(courtlistener.find_case("Doe v. Example Co.", "Morgan Example"))
    assert (found["case_name"], found["court"], found["docket_number"], found["passages"]) == (
        "Doe v. Example Co. & Sons", "Example Court", "1:5-cv", ["Morgan Example & co"])
    opinion = {"absolute_url": "/opinion/5/doe-v-example-co/", "caseName": result["caseName"],  # candidate search
               "court": result["court"], "dateFiled": "2019-06-06",
               "opinions": [{"id": 1005, "snippet": "<mark>Morgan Example</mark> &amp; co"}]}
    hit = courtlistener.to_hit(opinion)
    assert hit["title"] == "Doe v. Example Co. & Sons" and hit["case"][1] == "Example Court"
    assert "<" not in hit["note"] + hit["snippet"]


def test_opinion_http_error_raises(sources):
    sources(routes={"www.courtlistener.com": (404, {"detail": "Not found."})})
    with pytest.raises(RuntimeError, match="404"):
        asyncio.run(courtlistener.opinion_passages(10, "Alex Rivera"))
