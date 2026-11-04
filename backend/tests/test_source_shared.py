"""Shared helpers in app/sources/__init__.py: names, domains, listing pages, US states.
SYNTHETIC data only (gotcha 8)."""

import pytest

from app.sources import (
    courtlistener,
    given_family,
    is_blocked,
    is_expert_directory,
    is_listing,
    openalex,
    same_person_name,
    state_of,
)
from tests.fakes import NAME


@pytest.mark.parametrize("url, blocked", [
    ("https://www.linkedin.com/in/x", True),
    ("https://linkedin.com/in/x", True),
    ("https://scholar.google.co.uk/citations?user=x", True),
    ("https://brokercheck.finra.org/individual/x", True),
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
    ("https://www.seakexperts.com/members/x", True),
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


@pytest.mark.parametrize("name", [
    "Rev. Rivera", "Det. Rivera", "Sgt. Rivera", "Capt. Rivera", "Col. Rivera", "Fr. Rivera",
    "Sister Rivera", "Hon. Rivera", "Judge Rivera", "Atty. Rivera", "Officer Rivera", "Drs. Rivera",
])
def test_more_titles_are_not_given_names(name):
    assert courtlistener.name_variants(name) == []
    assert not openalex.name_matches(name, {"display_name": name})


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
    ("https://doc.example/avery", "Best, Jordan - Forensic Engineer", False),
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
