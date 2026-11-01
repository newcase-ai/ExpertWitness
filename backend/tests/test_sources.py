import asyncio
import time

import httpx
import pytest

from app import http, llm, store
from app.sources import (
    SourceFailed,
    SourceSkipped,
    courtlistener,
    given_family,
    is_blocked,
    is_expert_directory,
    is_listing,
    openalex,
    same_person_name,
    state_of,
    websearch,
)
from tests.fakes import (  # noqa: F401 - fixture
    COURTLISTENER_SEARCH,
    NAME,
    WEB_NAME_RESULTS,
    WEB_RESULTS,
    FakeModel,
    sources_fixture,
)

# Shared helpers


@pytest.mark.parametrize("url, blocked", [
    ("https://www.linkedin.com/in/x", True),
    ("https://linkedin.com/in/x", True),
    ("https://scholar.google.co.uk/citations?user=x", True),
    ("https://brokercheck.finra.org/individual/1", True),
    ("https://www.abms.org/verify", True),
    ("https://www.linkedin.com./in/a", True),  # trailing dot
    ("https://WWW.LinkedIn.com:443/in/a", True),  # case and port
    ("https://www.dauberttracker.com./x", True),
    ("https://lnkd.in/abc", True),
    ("https://linkedin.cn/in/a", True),
    ("https://uk.linkedin.co.uk/in/a", True),
    ("https://scholar.googleusercontent.com/scholar?q=x", True),
    ("https://user@linkedin.com/in/a", True),
    ("https://notlinkedin.com/page", False),
    ("https://example.edu/faculty/rivera", False),
    ("https://www.google.com/search?q=x", False),
])
def test_blocked_domains(url, blocked):
    assert is_blocked(url) is blocked


@pytest.mark.parametrize("url, directory", [
    ("https://www.seakexperts.com/members/1", True),
    ("https://jurispro.com/expert/x", True),
    ("https://WWW.Experts.com./x", True),
    ("https://notexperts.com/x", False),
    ("https://example.edu/faculty/rivera", False),
    ("http://[bad/x", False),
    ("https://www.imenet.com/members/x", True),
    ("https://expertsinmed.com/x", True),
])
def test_expert_directories(url, directory):
    assert is_expert_directory(url) is directory


def test_merge_name_rule():
    assert same_person_name("Jordan K. Avery", "Avery Jordan")
    assert same_person_name("Alex J. Rivera", "Rivera, Alex")
    assert not same_person_name("Sam Doe", "Sam Lee Doe")
    assert not same_person_name("Rivera", "Rivera")  # surname alone never counts


def test_names_compare_without_accents():
    assert same_person_name("José García", "Jose Garcia")
    assert same_person_name("Søren Ødegård", "Soren Odegard")
    assert same_person_name("Łukasz Strauß", "Lukasz Strauss")
    assert same_person_name("Ægir Đorđević", "Aegir Dordevic")
    assert same_person_name("Dr. Zoë Müller", "Muller, Zoe")
    assert given_family("JOSÉ M. GARCÍA") == ("jose", "garcia")
    assert openalex.name_matches("Jose Garcia", {"display_name": "José García"})


def test_titles_are_not_given_names():
    assert given_family("Dr. Avery") == ("avery", "avery")  # surname only
    assert given_family("Prof. Dr. Jordan K. Avery, PhD, Jr.") == ("jordan", "avery")
    assert not same_person_name("Dr. Avery", "Avery")
    assert not same_person_name("Dr. Avery", "Mr Avery")
    assert same_person_name("Dr. Jordan Avery", "Avery, Jordan MD")
    assert not openalex.name_matches("Dr. Rivera", {"display_name": "Dr. Rivera"})
    assert courtlistener.name_variants("Dr. Rivera") == []
    assert courtlistener.name_variants("Dr. Alex J. Rivera, PhD") == ["Alex J. Rivera", "Alex Rivera"]


# OpenAlex


def test_openalex_filters_by_given_and_family_name(sources):
    _, seen = sources()
    hits = asyncio.run(openalex.search_authors(NAME))
    assert [h["openalex_id"] for h in hits] == ["A0000000001", "A0000000002", "A0000000003"]
    first = hits[0]
    assert first["url"] == "https://api.openalex.org/authors/A0000000001"
    assert first["orcid"] == "0000-0000-0000-0001"
    assert first["affiliations"] == ["Example State University"]
    assert seen[0].url.params["search"] == NAME


def test_openalex_surname_only_matches_nothing(sources):
    sources()
    assert asyncio.run(openalex.search_authors("Rivera")) == []


def test_openalex_low_budget_skips_next_call(sources, monkeypatch):
    monkeypatch.setattr(openalex, "_budget", None)
    low = lambda r: httpx.Response(200, json={"results": []}, headers={"x-ratelimit-remaining-usd": "0.001"})
    _, seen = sources({"api.openalex.org": low})
    asyncio.run(openalex.search_authors(NAME))
    with pytest.raises(SourceSkipped, match="budget"):
        asyncio.run(openalex.search_authors(NAME))
    assert len(seen) == 1


def test_openalex_http_error_raises(sources):
    sources({"api.openalex.org": (503, {})})
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(openalex.search_authors(NAME))


# CourtListener


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


@pytest.mark.parametrize("name", [
    "Rev. Rivera", "Det. Rivera", "Sgt. Rivera", "Capt. Rivera", "Col. Rivera", "Fr. Rivera",
    "Sister Rivera", "Hon. Rivera", "Judge Rivera", "Atty. Rivera", "Officer Rivera", "Drs. Rivera",
])
def test_more_titles_are_not_given_names(name):
    assert courtlistener.name_variants(name) == []
    assert not openalex.name_matches(name, {"display_name": name})


def test_malformed_url_is_dropped_not_raised():
    assert is_blocked("http://[bad/x") is True
    hits = websearch.to_hits([{"url": "http://[bad/x", "title": "bad"}, WEB_RESULTS[0]])
    assert [h["url"] for h in hits] == [WEB_RESULTS[0]["url"]]


def test_web_titles_are_capped():
    hits = websearch.to_hits([WEB_RESULTS[0] | {"title": "T" * 5000}])
    assert len(hits[0]["title"]) == websearch.TITLE_MAX


def test_courtlistener_daily_cap(sources, monkeypatch):
    _, seen = sources()
    monkeypatch.setattr(store, "calls_since", lambda source, seconds: 125)
    with pytest.raises(SourceSkipped, match="limit"):
        asyncio.run(courtlistener.search_opinions(NAME))
    assert seen == []


# Meta web search


def test_websearch_runs_expert_bare_hint_and_field_location_queries(sources):
    model, _ = sources()
    hints = {"field": "toxicology", "location": "Ohio", "affiliation": "Example State University"}
    hits = asyncio.run(websearch.search_people(NAME, hints))
    assert [(h["url"], h["query"]) for h in hits] == [
        ("https://example.edu/faculty/rivera", "expert"),  # blocked and repeated URLs dropped
        ("https://www.jurispro.com/profile/0000-alex-rivera", "bare"),
        ("https://example.org/clinic/alex-rivera", "bare"),
    ]
    assert hits[0]["source"] == "web" and hits[0]["title"] == "Toxicology expert witness"
    assert all(c["web_search"] is True and c["schema"] is None for c in model.calls)
    assert [c["input"] for c in model.calls] == [
        'Search query: "Alex Rivera" expert witness',
        'Search query: "Alex Rivera"',
        'Search query: "Alex Rivera" expert witness toxicology Ohio Example State University',
        'Search query: "Alex Rivera" toxicology Ohio',  # no "expert witness"
    ]


def test_websearch_field_location_query_needs_field_or_location():
    queries = websearch.build_queries(NAME, {"field": None, "location": None, "affiliation": "Example Lab"})
    assert [k for k, _ in queries] == ["expert", "bare", "hint"]
    queries = websearch.build_queries(NAME, {"location": 'Gilbert, "AZ"'})
    assert queries[-1] == ("field-location", '"Alex Rivera" Gilbert, AZ')


@pytest.mark.parametrize("url, title, listing", [
    ("https://doctor.example.com/providers/specialty/x/city", "Best Rheumatologists in Exampleton, CA (2026)", True),
    ("https://example.com/top", "Top 10 Toxicologists in Ohio", True),
    ("https://example.com/top", "The 12 Best Expert Witnesses in Ohio", True),
    ("https://example.com/x", "Top-Rated Neurologists Near Me", True),
    ("https://example.com/search/?term=Psychiatrist&page=13", "Find a doctor", True),
    ("https://example.com/providers?pagenumber=2", "Providers", True),
    ("https://example.com/x", "Search for the Best Doctors - Results", True),
    ("https://example.edu/faculty/rivera", "Alex Rivera, Toxicology Expert Witness", False),
    ("https://www.jurispro.com/profile/0000-alex-rivera", "Alex Rivera - Economics", False),
    ("https://example.com/rivera", "Alex Rivera on best practices in toxicology", False),
    # QA round 3: single-person pages that must be kept
    ("https://doc.example/avery", "Top-rated neurologist Dr Alex Rivera", False),
    ("https://doc.example/avery", "Top Doctors: Alex Rivera, MD", False),
    ("https://doc.example/avery", "Best, Robert - Forensic Engineer", False),
    ("https://doc.example/avery", "The Best Expert Witness for Slip-and-Fall: Alex Rivera", False),
    ("https://doc.example/avery", "Alex Rivera | Top 1% Expert Witness", False),
    ("https://doc.example/avery", "Best Expert Witnesses in Ohio: Alex Rivera", False),  # holds the name
    ("https://firm.example/index.php?page=attorney-bio&id=4", "Alex Rivera, PE", False),
    ("https://firm.example/profile?term=2024&id=4", "Alex Rivera, PE", False),
    ("https://firm.example/people/alex-rivera?sort=asc", "Alex Rivera", False),
    ("https://firm.example/search/alex-rivera", "Alex Rivera", False),
    ("https://firm.example/case-results/rivera", "Alex Rivera", False),
    # clear aggregates
    ("https://doc.example/x", "Search results for Alex Rivera", True),
    ("https://doc.example/x", "Find a Doctor", True),
    ("https://doc.example/x", "12 Doctors in Austin", True),
    ("https://doc.example/x", "Top 20 Neurologists", True),
    ("https://www.imenet.com/search?q=rivera", "IMENet", True),
    ("https://doc.example/find-a-doctor/ohio", "Ohio", True),
    ("https://doc.example/find-a-doctor/search", "Alex Rivera, MD", True),
    ("https://doc.example/find-a-physician/orthopedics", "Orthopedic Surgeons | Example Health", True),
    # a one-person hospital profile under /find-a-doctor/ is kept when its title holds the name
    ("https://health.example/find-a-doctor/alex-rivera", "Alex Rivera, MD | Example Health", False),
    ("https://health.example/find-a-physician/alex-j-rivera-md", "Dr. Alex J. Rivera - Neurology", False),
])
def test_listing_pages_are_detected(url, title, listing):
    assert is_listing(url, title, NAME) is listing


@pytest.mark.parametrize("location, state", [
    ("Gilbert, Arizona", "AZ"), ("Concord, MA", "MA"), ("Columbus OH", "OH"), ("Akron, OHIO", "OH"),
    ("Washington, DC", "DC"), ("Washington DC", "DC"), ("District of Columbia", "DC"), ("Washington", "WA"),
    ("St. Louis, Missouri", "MO"), ("Saint Louis, MO", "MO"), ("Dallas-Fort Worth, TX", "TX"),
    ("Charleston, West Virginia", "WV"), ("Little Rock, Arkansas", "AR"), ("Indiana, PA", "PA"),
    ("Ohio, US", "OH"), ("Austin, TX 78701", "TX"), ("Austin, TX, USA", "TX"),
    # no state at the end: no state
    ("San Jose", None), ("Bogotá", None), ("Toronto, ON", None), ("Dallas, TX office", None),
    ("Concord, ma", None), ("", None), (None, None),
])
def test_state_is_read_only_from_the_end_of_a_location(location, state):
    assert state_of(location) == state


def test_listing_pages_are_dropped_before_the_per_query_cap():
    listings = [{"url": f"https://example.com/search?q=x&page={i}", "title": "Results"} for i in range(15)]
    person = {"url": "https://example.edu/faculty/rivera", "title": "Faculty page"}
    hits = websearch.to_hits(listings + [person], "bare")
    assert [h["url"] for h in hits] == [person["url"]]


def test_websearch_without_hints_sends_two_queries(sources):
    model, _ = sources()
    asyncio.run(websearch.search_people('Alex "Rivera"', None))
    assert [c["input"] for c in model.calls] == [
        'Search query: "Alex Rivera" expert witness', 'Search query: "Alex Rivera"']


def test_websearch_page_found_by_name_and_hint_query_counts_as_expert(sources):
    sources(model=FakeModel(web=[[], WEB_NAME_RESULTS, WEB_NAME_RESULTS[:1]]))
    hits = asyncio.run(websearch.search_people(NAME, {"field": "economics"}))
    assert [h["query"] for h in hits] == ["hint", "bare", "bare"]
    assert hits[0]["url"] == WEB_NAME_RESULTS[0]["url"]


def test_websearch_one_failed_query_is_reported_all_failed_raises(sources):
    sources(model=FakeModel(web=[llm.ModelError("down"), WEB_NAME_RESULTS]))
    failed = []
    hits = asyncio.run(websearch.search_people(NAME, None, failed))
    assert [h["query"] for h in hits] == ["bare", "bare", "bare"]
    assert failed == ["expert query failed: down"]
    down = llm.ModelError("down")
    sources(model=FakeModel(web=[[], down, down, down]))
    failed = []
    asyncio.run(websearch.search_people(NAME, {"field": "toxicology"}, failed))
    assert failed == ["bare query failed: down", "hint query failed: down",
                      "field-location query failed: down"]
    sources(model=FakeModel(web=[llm.ModelError("down"), llm.ModelError("down too")]))
    with pytest.raises(llm.ModelError, match="^down$"):
        asyncio.run(websearch.search_people(NAME, None))


def test_websearch_falls_back_to_citations(sources, monkeypatch):
    sources()
    result = llm.ModelResult(citations=[{"type": "url_citation", "url": "https://example.org/a", "title": "A"}])

    async def fake(*args, **kwargs):
        return result

    monkeypatch.setattr(llm, "call_model", fake)
    hits = asyncio.run(websearch.search_people(NAME, None))
    assert [h["url"] for h in hits] == ["https://example.org/a"]


def test_websearch_one_query_drops_blocked_and_caps_the_query(sources):
    model, _ = sources(model=FakeModel(web=[WEB_RESULTS]))
    hits = asyncio.run(websearch.search_query(NAME, '"Alex Rivera" ' + "x" * 500))
    assert [h["url"] for h in hits] == ["https://example.edu/faculty/rivera"]  # blocked and repeat dropped
    assert hits[0]["query"] == "follow-up"
    assert model.calls[0]["web_search"] is True
    assert len(model.calls[0]["input"]) == len("Search query: ") + websearch.QUERY_MAX


@pytest.mark.parametrize("query, blocked", [
    ('"Alex Rivera" site:linkedin.com', True),
    ('"Alex Rivera" site:www.LinkedIn.com/in', True),
    ("Alex Rivera (site:scholar.google.com)", True),
    ("Alex Rivera dauberttracker.com", True),
    ("Alex J. Rivera Ph.D. ... U.S.", False),  # dots that are not sites
    ("Alex Rivera site:example.edu", False),
    # glued to punctuation or behind other operators (QA bypasses)
    ("Alex Rivera|site:linkedin.com", True),
    ('"Alex Rivera"site:linkedin.com', True),
    ("Alex Rivera inurl:linkedin.com", True),
    ("Alex Rivera related:linkedin.com", True),
    ("Alex Rivera cache:linkedin.com", True),
    ("Alex Rivera intitle:expert site:uk.linkedin.com/in", True),
    ("Alex Rivera lnkd.in/abc", True),
    ("Alex Rivera \uff4c\uff49\uff4e\uff4b\uff45\uff44\uff49\uff4e\uff0e\uff43\uff4f\uff4d", True),  # full-width
    ("Alex Rivera linkedin\u3002com", True),  # ideographic full stop
    ("Alex Rivera linkedin\uff0ecom", True),  # full-width full stop
    ("Alex Rivera linkedin\uff61com", True),  # half-width ideographic full stop
    ("Alex Rivera linked\u200bin.com", True),  # zero-width space
    ("Alex Rivera linked\u200cin.com", True),  # zero-width non-joiner
    ("Alex Rivera linked\u200din.com", True),  # zero-width joiner
    ("Alex Rivera linked\u2060in.com", True),  # word joiner
    ("Alex Rivera linked\ufeffin.com", True),  # BOM
    ("Alex Rivera linked\u00adin.com", True),  # soft hyphen
    ("Alex Rivera site:linkedin.\u200bcom", True),
    ("Gary Stanton expert witness Michigan", False),
    ("Smith v. Jones 2019", False),
    ("Alex Rivera notlinkedin.com", False),
])
def test_websearch_query_naming_a_banned_site(query, blocked):
    assert websearch.names_blocked_site(query) is blocked
