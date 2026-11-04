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


# --- CourtListener RECAP dockets (court_search) ----------------------------------------------------


def docket_result(did, court, date, snippets, party=(), attorney=(), more_docs=False):
    return {"docket_absolute_url": f"/docket/{did}/doe-v-example-co/", "docket_id": did, "caseName": "Doe v. Example Co.",
            "court": court, "dateFiled": date, "docketNumber": f"1:{did}-cv", "suitNature": "791 ERISA",
            "party": list(party), "attorney": list(attorney), "meta": {"more_docs": more_docs},
            "recap_documents": [{"absolute_url": f"/docket/{did}/{n}/doe-v-example-co/", "id": did * 100 + n,
                                 "snippet": s, "short_description": f"Exhibit {n}", "entry_date_filed": date,
                                 "document_number": n, "page_count": 4, "is_available": n == 2,
                                 "filepath_local": f"recap/gov.uscourts.exd.{did}/gov.uscourts.exd.{did}.{n}.0.pdf"}
                                for n, s in enumerate(snippets, 1)]}


def test_search_dockets_full_name_only_and_fields(sources):
    body = {"results": [docket_result(5, "District Court, D. Example", "2021-02-08",
                                      ["no match here", "SYNTHETIC-CL reviewed by Dr. <mark>Alex J. Rivera</mark>, "
                                       "M.D. &amp; toxicologist " + "y" * 500], party=["Alex Rivera", "Example Co."],
                                      more_docs=True)]}
    _, seen = sources(routes=opinion_route(body))
    [hit], total, nxt = asyncio.run(courtlistener.search_dockets("Dr. Alex J. Rivera"))
    assert (total, nxt) == (1, None)  # no count in the body: the hits on the page
    params = seen[0].url.params
    assert params["type"] == "r" and params["highlight"] == "on"
    assert params["q"] == '"Alex J. Rivera" OR "Alex Rivera"'  # no expert-word rule, never the surname alone
    assert hit["url"] == "https://www.courtlistener.com/docket/5/doe-v-example-co/"
    assert (hit["case_name"], hit["court"], hit["docket_number"], hit["date_filed"], hit["nature_of_suit"]) == (
        "Doe v. Example Co.", "District Court, D. Example", "1:5-cv", "2021-02-08", "791 ERISA")
    assert hit["named_as"] == ["party"] and hit["docket_id"] == 5 and hit["more_filings"] is True
    assert courtlistener.to_docket({"docket_absolute_url": "/docket/5/x/", "docket_id": "5"}, NAME)["docket_id"] is None
    first, second = hit["documents"]  # every matched filing, each with its own passage
    assert (first["id"], first["available"], first["filepath_local"], first["passage"]) == (501, False, None,
                                                                                          "no match here")
    assert second == {"id": 502, "description": "Exhibit 2", "entry": "", "document_number": "2", "attachment_number": None,
                      "date_filed": "2021-02-08", "page_count": 4, "available": True,
                      "filepath_local": "recap/gov.uscourts.exd.5/gov.uscourts.exd.5.2.0.pdf",
                      "url": "https://www.courtlistener.com/docket/5/2/doe-v-example-co/", "passage": second["passage"]}
    assert second["passage"].startswith("SYNTHETIC-CL reviewed by Dr. Alex J. Rivera, M.D. & toxicologist")
    assert len(second["passage"]) == courtlistener.PASSAGE_CHARS and "<" not in second["passage"]
    with pytest.raises(SourceSkipped):
        asyncio.run(courtlistener.search_dockets("Dr. Rivera"))


def test_search_dockets_words_and_case_name(sources):
    _, seen = sources(routes=opinion_route({"results": []}))
    asyncio.run(courtlistener.search_dockets("Dr. Morgan Q. Example", '"motion to exclude" (x:y)* OR Daubert~2',
                                             'Doe v. "Example" Co.'))
    assert seen[0].url.params["q"] == ('caseName:("Doe" AND "Example") AND ("motion to exclude" x y OR Daubert 2)'
                                       ' AND ("Morgan Q. Example" OR "Morgan Example")')
    asyncio.run(courtlistener.search_dockets("Morgan Example", 'unbalanced "quote'))
    assert seen[1].url.params["q"] == '(unbalanced quote) AND ("Morgan Example")'
    asyncio.run(courtlistener.search_dockets("Morgan Example", "  ", "()"))  # nothing left: the name only
    assert seen[2].url.params["q"] == '"Morgan Example"'


def test_search_dockets_pages(sources):
    nxt = f"{courtlistener.SEARCH}?type=r&q=x&cursor=c2"
    replies = [{"count": 27, "next": nxt, "results": [docket_result(1, "A", "2001-01-01", [])]},
               {"count": 27, "next": None, "results": [docket_result(2, "B", "2002-01-01", [])]}]
    _, seen = sources(routes={"www.courtlistener.com": lambda request: httpx.Response(200, json=replies.pop(0))})
    hits, total, link = asyncio.run(courtlistener.search_dockets("Morgan Example"))
    assert ([h["docket_id"] for h in hits], total, link) == ([1], 27, nxt)
    hits, total, link = asyncio.run(courtlistener.search_dockets("Morgan Example", next_url=link))
    assert ([h["docket_id"] for h in hits], total, link) == ([2], 27, None)
    assert seen[1].url.params["cursor"] == "c2" and seen[1].headers["Authorization"] == "Token test-token"
    odd = {"count": 1, "next": "https://elsewhere.example/api/?cursor=x", "results": []}  # would get the token
    sources(routes=opinion_route(odd))
    with pytest.raises(SourceFailed, match="unexpected response"):
        asyncio.run(courtlistener.search_dockets("Jordan Example"))  # another search: not the cached page


def test_search_dockets_long_cv_name_uses_party_key_words(sources):
    _, seen = sources(routes=opinion_route({"results": []}))
    asyncio.run(courtlistener.search_dockets(
        "Morgan Example", case_name="Jordan Avery vs. Patrol Officer Sam Lindqvist and the Town of Brookfield"))
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


def test_search_dockets_strips_highlight_tags(sources):
    result = docket_result(5, "<mark>Example</mark> Court", "2019-06-06", ["<mark>Morgan Example</mark> &amp; co"])
    result["caseName"] = "<mark>Doe</mark> v. <mark>Example</mark> Co. &amp; Sons"
    result["docketNumber"] = "<em>1:5-cv</em>"
    sources(routes=opinion_route({"results": [result]}))
    [found], _, _ = asyncio.run(courtlistener.search_dockets("Morgan Example", case_name="Doe v. Example Co."))
    assert (found["case_name"], found["court"], found["docket_number"], found["documents"][0]["passage"]) == (
        "Doe v. Example Co. & Sons", "Example Court", "1:5-cv", "Morgan Example & co")
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


# --- Filings of one docket: list, CourtListener's text, PDF download -----------------------------


def filing(n, docket=7, **changes):
    path = f"recap/gov.uscourts.exd.{docket}/gov.uscourts.exd.{docket}.{n}.0.pdf"
    return {"absolute_url": f"/docket/{docket}/{n}/doe-v-example-co/", "id": 900 + n, "attachment_number": None,
            "description": "", "short_description": f"SYNTHETIC <mark>Expert</mark> report {n} &amp; CV",
            "document_number": str(n), "entry_date_filed": "2021-02-08", "is_available": True,
            "page_count": 12, "filepath_local": path, "snippet": ""} | changes


def filings_page(results, cursor=None):
    nxt = f"{courtlistener.SEARCH}?type=rd&q=docket_id%3A7&cursor={cursor}" if cursor else None
    return {"count": 99, "next": nxt, "previous": None, "results": results}


def test_docket_documents_query_shape_and_filter(sources):
    results = [filing(1), filing(2, is_available=False), filing(3, filepath_local=None), filing(4, id="904"),
               filing(5, filepath_local="/etc/passwd"), filing(6, filepath_local="recap/../../secret.pdf"),
               filing(7, filepath_local="https://elsewhere.example/x.pdf"), filing(8, filepath_local="recap/a b.pdf"),
               filing(9, short_description="", description="Long description " + "z" * 300, page_count=None,
                      document_number=None, attachment_number=2)]
    _, seen = sources(routes=opinion_route(filings_page(results)))
    docs, more_left = asyncio.run(courtlistener.docket_documents(7))
    assert more_left is False
    params = seen[0].url.params
    assert params["type"] == "rd" and params["q"] == "docket_id:7 AND is_available:true"
    assert seen[0].headers["Authorization"] == "Token test-token"
    assert [d["id"] for d in docs] == [901, 909]
    assert docs[0] == {"id": 901, "description": "SYNTHETIC Expert report 1 & CV", "entry": "", "document_number": "1",
                       "attachment_number": None, "date_filed": "2021-02-08", "page_count": 12, "available": True,
                       "filepath_local": "recap/gov.uscourts.exd.7/gov.uscourts.exd.7.1.0.pdf",
                       "url": "https://www.courtlistener.com/docket/7/1/doe-v-example-co/"}
    assert len(docs[1]["description"]) == 200 and docs[1]["page_count"] is None
    assert docs[1]["document_number"] is None and docs[1]["attachment_number"] == 2
    assert len(seen) == 1 and store.calls_since("courtlistener", 3600) == 1


def test_docket_documents_follows_next_up_to_the_cap(sources):
    def pages(request):
        n = len(seen)  # one page per request, each with a next link
        return httpx.Response(200, json=filings_page([filing(n)], cursor=f"c{n}"))

    _, seen = sources(routes={"www.courtlistener.com": pages})
    docs, more_left = asyncio.run(courtlistener.docket_documents(7))
    assert len(seen) == courtlistener.MAX_DOC_PAGES == len(docs) and more_left is True
    assert store.calls_since("courtlistener", 3600) == courtlistener.MAX_DOC_PAGES  # every page is a call
    assert seen[1].url.params["cursor"] == "c1" and seen[1].url.params["type"] == "rd"  # next link as given
    assert str(seen[2].url).startswith(courtlistener.SEARCH + "?") and "cursor=c2" in str(seen[2].url)


def test_docket_documents_stops_at_the_last_page_and_refuses_odd_next(sources):
    replies = [filings_page([filing(1)], cursor="c1"), filings_page([filing(2)])]
    _, seen = sources(routes={"www.courtlistener.com": lambda request: httpx.Response(200, json=replies.pop(0))})
    docs, more_left = asyncio.run(courtlistener.docket_documents(7))
    assert [d["id"] for d in docs] == [901, 902] and more_left is False
    assert len(seen) == 2
    odd = filings_page([filing(1)]) | {"next": "https://elsewhere.example/api/?cursor=x"}  # would get the token
    sources(routes=opinion_route(odd))
    with pytest.raises(SourceFailed, match="unexpected response"):
        asyncio.run(courtlistener.docket_documents(8))


def test_refused_token_falls_back_once(sources, monkeypatch):
    """HTTP 401/403 with the main token: one retry with COURTLISTENER_TOKEN_FALLBACK."""
    def route(request):
        ok = request.headers["Authorization"] == "Token backup-token"
        return httpx.Response(200 if ok else 401, json={"plain_text": "SYNTHETIC text"} if ok else {})

    _, seen = sources(routes={"www.courtlistener.com": route})
    with pytest.raises(SourceFailed, match="token was refused"):  # no fallback set
        asyncio.run(courtlistener.document_text(55))
    monkeypatch.setenv("COURTLISTENER_TOKEN_FALLBACK", "backup-token")
    assert asyncio.run(courtlistener.document_text(56)) == "SYNTHETIC text"
    assert [r.headers["Authorization"] for r in seen[1:]] == ["Token test-token", "Token backup-token"]


def test_rate_limit_never_uses_the_fallback(sources, monkeypatch):
    """Gotcha 5: one account per project; a 429 is not a reason to switch accounts."""
    monkeypatch.setenv("COURTLISTENER_TOKEN_FALLBACK", "backup-token")
    _, seen = sources(routes={"www.courtlistener.com": (429, {})})
    with pytest.raises(SourceFailed, match="429"):
        asyncio.run(courtlistener.document_text(57))
    assert {r.headers["Authorization"] for r in seen} == {"Token test-token"}


def test_fallback_alone_is_used_when_the_main_token_is_unset(sources, monkeypatch):
    monkeypatch.delenv("COURTLISTENER_TOKEN")
    monkeypatch.setenv("COURTLISTENER_TOKEN_FALLBACK", "backup-token")
    _, seen = sources(routes=opinion_route({"plain_text": "SYNTHETIC", "ocr_status": 1}))
    asyncio.run(courtlistener.document_text(58))
    assert seen[0].headers["Authorization"] == "Token backup-token"


def test_document_text(sources):
    _, seen = sources(routes=opinion_route({"plain_text": "SYNTHETIC page one\fpage two", "ocr_status": 1}))
    assert asyncio.run(courtlistener.document_text(55)) == "SYNTHETIC page one\fpage two"
    assert seen[0].url.path == "/api/rest/v4/recap-documents/55/"
    assert seen[0].url.params["fields"] == "plain_text,ocr_status"
    sources(routes=opinion_route({"plain_text": None, "ocr_status": None}))
    assert asyncio.run(courtlistener.document_text(56)) == ""
    sources(routes=opinion_route({"detail": "Not found."}))
    with pytest.raises(SourceFailed, match="unexpected response"):
        asyncio.run(courtlistener.document_text(57))


PDF_BYTES = b"%PDF-1.4 SYNTHETIC " + b"x" * 25


def test_download_document_streams_in_chunks_without_token(sources, monkeypatch, tmp_path):
    monkeypatch.setattr(courtlistener, "CHUNK", 10)
    writes = []
    real_open = courtlistener.Path.open

    class Spy:  # records every write
        def __init__(self, f):
            self.f = f

        def write(self, chunk):
            writes.append(len(chunk))
            return self.f.write(chunk)

        def close(self):
            self.f.close()

    def spy_open(self, *args, **kwargs):
        return Spy(real_open(self, *args, **kwargs))

    monkeypatch.setattr(courtlistener.Path, "open", spy_open)
    _, seen = sources(routes={"storage.courtlistener.com": lambda request: httpx.Response(200, content=PDF_BYTES)})
    dest = tmp_path / "filing.pdf"
    path = filing(1)["filepath_local"]
    assert asyncio.run(courtlistener.download_document(path, dest)) == len(PDF_BYTES)
    monkeypatch.setattr(courtlistener.Path, "open", real_open)
    assert dest.read_bytes() == PDF_BYTES and writes == [10, 10, 10, 10, 4]
    assert str(seen[0].url) == courtlistener.STORAGE + path
    assert "Authorization" not in seen[0].headers
    assert store.calls_since("courtlistener", 3600) == 0  # not an API call
    assert "courtlistener_storage" in http.RATES


@pytest.mark.parametrize("path", ["/etc/passwd", "recap/../x.pdf", "https://elsewhere.example/x.pdf",
                                  "recap/a b.pdf", "", None, ".hidden/x.pdf"])
def test_download_document_refuses_odd_paths(sources, tmp_path, path):
    _, seen = sources()
    with pytest.raises(SourceFailed, match="unexpected file path"):
        asyncio.run(courtlistener.download_document(path, tmp_path / "x.pdf"))
    assert seen == [] and not (tmp_path / "x.pdf").exists()


async def broken_body():
    yield b"%PDF-1.4 partial"
    raise httpx.ReadError("connection reset")


@pytest.mark.parametrize(("route", "says"), [
    (lambda request: httpx.Response(404, content=b"missing"), "storage HTTP 404"),
    (lambda request: httpx.Response(200, content=broken_body()), "network error or timeout"),
    (lambda request: httpx.Response(302, headers={"Location": "https://elsewhere.example/x.pdf"}), "redirected"),
])
def test_download_document_failures_leave_no_file(sources, tmp_path, route, says):
    elsewhere = {"elsewhere.example": lambda request: httpx.Response(200, content=PDF_BYTES)}
    sources(routes={"storage.courtlistener.com": route} | elsewhere)
    dest = tmp_path / "filing.pdf"
    with pytest.raises(SourceFailed, match=says):
        asyncio.run(courtlistener.download_document(filing(1)["filepath_local"], dest))
    assert not dest.exists()
