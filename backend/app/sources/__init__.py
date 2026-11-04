"""Source clients: raw API access (HTTP, rate limits, cache) used by the candidate search and the
agent tools in `app/tools`. Shared name, domain and US-state helpers live here."""

import re
import unicodedata
from urllib.parse import parse_qs, urlsplit

# Gotcha 2: never automate these sites. Results from them (and their subdomains) are dropped in code.
BLOCKED_DOMAINS = (
    "linkedin.com",
    "lnkd.in",  # LinkedIn short links
    "dauberttracker.com",
    "trialsmith.com",
    "cpaverify.org",
    "brokercheck.finra.org",
    "abms.org",
    "certificationmatters.org",  # ABMS's public certification lookup
    "docinfo.org",
)

# Titles and suffixes are not names: "Dr. Rivera" has no given name (gotcha 13).
TITLES = frozenset({
    "dr", "drs", "dra", "doctor", "prof", "professor", "mr", "mrs", "ms", "miss", "mx", "sir", "dame",
    "hon", "judge", "justice", "rev", "reverend", "fr", "father", "sister", "det", "detective", "officer",
    "insp", "inspector", "sgt", "cpl", "lt", "capt", "col", "atty", "attorney",
    "md", "phd", "dds", "dmd", "dvm", "mph", "msc", "mba", "jd", "rn", "esq", "facs", "facp", "frcp",
    "jr", "sr", "ii", "iii", "iv",
})


class SourceSkipped(Exception):
    """A source was not called on purpose (no token, budget used up). Shown as a gap."""


class SourceFailed(RuntimeError):
    """A source call failed. The message is fixed text written by code, never provider text: what
    happened and whether calling again can help. The tool wrapper passes it to the model and the log."""


# Public expert-witness directories. A search result on one of these is an expert signal. We only keep
# the URL and title the search returned; these pages are never fetched by us.
EXPERT_DIRECTORIES = (
    "seakexperts.com",
    "jurispro.com",
    "expertinstitute.com",
    "tasanet.com",
    "expertpages.com",
    "experts.com",
    "expertwitness.com",
    "almexperts.com",
    "forensisgroup.com",
    "expert4law.org",
    "imenet.com",
    "expertsinmed.com",
)

# Listing and aggregate pages are about many people, not one: dropped before the per-query cap.
# Only clear aggregates: losing a real expert's page is worse than letting a listing through (the AI
# is also told to skip listing pages). Always a listing: site search, "near me", "Find a ...",
# "<N> doctors".
LISTING_TITLE = re.compile(
    r"^\W*search\s+(results|for)\b|\bnear\s+me\b|^\W*find\s+an?\s"
    r"|^\W*\d+\s+(best\s+|top\s+)?([\w-]+\s+)?(doctors|physicians|lawyers|attorneys|experts|specialists"
    r"|providers|dentists|surgeons)\b",
    re.IGNORECASE,
)
# A listing unless the title holds the searched name: "Best <specialty>s in/near <place>", "Top 10 ...".
RANKING_TITLE = re.compile(
    r"^\W*(the\s+)?(\d+\s+)?best\s+([\w&-]+\s+){0,4}?[\w-]+s\s+(in|near)\s|\btop[\s-]+\d+\s+[a-z]",
    re.IGNORECASE,
)
SEARCH_PAGES = frozenset({"search", "search-results", "find-a-doctor", "find-a-physician", "find-an-expert"})
PAGING_PARAMS = frozenset({"page", "pagenumber", "pagenum"})

# Letters NFKD does not split into base letter + accent.
FOLD_TABLE = str.maketrans({"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "Æ": "Ae",
                            "œ": "oe", "Œ": "Oe", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th",
                            "Þ": "Th", "ı": "i"})

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
STATE_CODES = frozenset(US_STATES.values())
# The state a location field ends with: a full name or an upper-case code ("Gilbert, Arizona",
# "Concord, MA", "Washington, DC"), optionally followed by a ZIP code or "US"/"USA".
STATE_AT_END = re.compile(r"(?:^|[\s,])((?i:" + "|".join(sorted(US_STATES, key=len, reverse=True)) + r")|[A-Z]{2})"
                          r"(?:\s+\d{5}(?:-\d{4})?)?(?:,?\s*(?i:usa?|united states))?\W*$")


def host_of(url: str) -> str | None:
    """Lowercase host without port or trailing dot; None if the URL cannot be read."""
    try:
        return (urlsplit(url.strip()).hostname or "").rstrip(".")
    except ValueError:
        return None


def on_domain(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_blocked(url: str) -> bool:
    """True for gotcha 2 sites, incl. subdomains, trailing-dot hosts, any linkedin.* country domain
    and Google Scholar hosts (scholar.google.*, scholar.googleusercontent.com)."""
    host = host_of(url)
    if host is None:
        return True  # a URL we cannot read is dropped, never an error
    labels = host.split(".")
    if "linkedin" in labels:
        return True
    if "scholar" in labels and any(label.startswith("google") for label in labels):
        return True
    return on_domain(host, BLOCKED_DOMAINS)


def is_listing(url: str, title: str, name: str | None = None) -> bool:
    """True only for a clear aggregate page: a listing title (a ranking title counts only if it does
    not hold the searched name), a site-search URL, a find-a-doctor page whose title lacks the
    searched name, or a numeric paging parameter."""
    if LISTING_TITLE.search(title):
        return True
    if RANKING_TITLE.search(title) and not (name and has_full_name(title, name)):
        return True
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    segments = [s.lower() for s in parts.path.split("/") if s]
    if segments and segments[-1] in SEARCH_PAGES:
        return True
    if {"find-a-doctor", "find-a-physician"} & set(segments) and not (name and has_full_name(title, name)):
        return True  # /find-a-doctor/<name> is a one-person profile when the title holds the name
    params = parse_qs(parts.query)
    return any(v.isdigit() for k in PAGING_PARAMS & {p.lower() for p in params} for v in params.get(k, []))


def strip_accents(text: str) -> str:
    """Accents and special letters removed, case kept: "Søren García" -> "Soren Garcia"."""
    decomposed = unicodedata.normalize("NFKD", text.translate(FOLD_TABLE))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).translate(FOLD_TABLE)


def fold(text: str) -> str:
    """Lowercase without accents: "José García" -> "jose garcia", "Søren" -> "soren"."""
    return strip_accents(text).lower()


def state_of(location: str | None) -> str | None:
    """US state code a location field ends with, else None. Only location fields are read: never
    titles, fields, firms or credentials ("Jordan Avery, MD" is not Maryland)."""
    m = STATE_AT_END.search(location or "")
    code = m and US_STATES.get(m.group(1).lower(), m.group(1))
    return code if code in STATE_CODES else None


def is_expert_directory(url: str) -> bool:
    host = host_of(url)
    return bool(host) and on_domain(host, EXPERT_DIRECTORIES)


def name_words(name: str) -> list[str]:
    """Words of a name, original case, without titles and suffixes ("Dr. Alex Rivera, PhD")."""
    words = re.findall(r"[^\W\d_][^\s,]*", name)
    return [w for w in words if w.lower().strip(".") not in TITLES]


def name_tokens(name: str) -> list[str]:
    """Lowercase, accent-free name parts without punctuation, titles, suffixes or single-letter initials."""
    tokens = re.findall(r"[^\W\d_]+", fold(name))
    return [t for t in tokens if len(t) > 1 and t not in TITLES]


def given_family(name: str) -> tuple[str, str]:
    """First and last real name part, e.g. "Jordan K. Avery" -> ("jordan", "avery").
    A name with one real part (e.g. "Dr. Avery") gives ("avery", "avery"): surname only, never a match."""
    tokens = name_tokens(name)
    if not tokens:
        return "", ""
    return tokens[0], tokens[-1]


def has_full_name(text: str, name: str) -> bool:
    """True if `text` holds the given and family name of `name` (any order, accents ignored)."""
    given, family = given_family(name)
    return bool(given) and given != family and {given, family} <= set(name_tokens(text))


def same_person_name(a: str, b: str) -> bool:
    """DESIGN 4.1 merge rule (b): same given and family name in any order, middle initials ignored."""
    ta, tb = name_tokens(a), name_tokens(b)
    return len(ta) >= 2 and sorted(ta) == sorted(tb)
