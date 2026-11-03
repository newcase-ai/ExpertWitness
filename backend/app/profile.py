"""The expert profile (user decision 2026-11-01): what one research run knows so far about the expert,
kept up to date by AI. After every tool call that saved findings, the tool wrapper (app/tools)
calls `update` with the new findings: the model gets the current profile plus only those findings
and returns the whole profile again. Code keeps only items that cite real findings of the run. No
copy check (ctx.ask_model would drop every unchanged item): the model rewrites our own stored
findings and profile, never source text. `known_so_far` is the context other AI checks get."""

import asyncio
from collections import defaultdict

from app import llm, store
from app.models import PROFILE_SECTIONS, Candidate, Finding, Profile, ProfileItem

AUTO_UPDATE = True  # tests turn it off (tests/conftest.py) unless a test is about the profile
ITEMS_MAX = 12  # per section
ITEM_MAX = 300  # characters of one item
NEW_MAX = 40  # new findings shown to the model in one update; the rest are counted
LINE_MAX = 300
FACT_LINE_MAX = 2_400  # a file_fact line keeps its ask_pdf answer (2,000 characters) whole
PER_KIND = 8  # findings of one kind in `known_so_far` before the first profile update
CONTEXT_MAX = 8_000

TITLES = {
    "identity": "Identity", "expertise": "Expertise", "credentials": "Credentials", "cases": "Cases",
    "publications": "Publications", "red_flags": "Red flags", "not_him": "Not him (same name, another person)",
    "conflicts": "Conflicts", "open_leads": "Open leads",
}

ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text", "refs", "confidence"],
    "properties": {"text": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}},
                   "confidence": {"type": "string"}},
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [*PROFILE_SECTIONS, "changes"],
    "properties": {**{s: {"type": "array", "items": ITEM_SCHEMA} for s in PROFILE_SECTIONS},
                   "changes": {"type": "string"}},
}

INSTRUCTIONS = f"""You keep the research profile of one expert witness up to date. You get the
current profile (each item ends with the ids of the findings it rests on, in brackets) and new
findings (each line starts with its id). Return the whole updated profile: keep what still holds,
add what the new findings show, merge duplicates, and when a new finding confirms an item add its
id. When findings disagree, put the point under conflicts. A finding about someone else with the
same name (a different profession, place or role) goes under not_him, never in the other sections:
a shared name is not proof. Every item cites the ids it rests on (refs); never add anything no
finding says. Sections: identity (other forms of the name, current role, employers, cities and
states), expertise (field, specialties, typical case types), credentials (degrees, licences,
boards and their status), cases (one item per case: name, court, year, side, his role, what
happened), publications (a short summary and notable papers), red_flags (excluded, challenged or
criticised testimony, retractions, discipline, exclusion lists, large payments from companies such as
royalties or consulting fees: a possible bias), not_him, conflicts, open_leads (concrete
things worth searching next). Short items in your own words, at most {ITEMS_MAX} per section.
confidence: "high" only when a high-confidence finding or two findings agree, else "low".
Professional facts only: no home address, family or health. changes: one short sentence on what
changed."""

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # one update at a time per run


def finding_line(f: Finding, with_id: bool = False) -> str:
    """One finding for the AI: (id,) kind, title, note and short facts (court, date, nature of suit, the
    same-person check's role and reason, facts in our own words)."""
    facts = f.facts or {}
    match = facts.get("match") if isinstance(facts.get("match"), dict) else {}
    reasons = [r for r in match.get("reasons") or [] if isinstance(r, str)]
    bits = [facts.get("court"), facts.get("date_filed"), facts.get("nature_of_suit"),
            match.get("role") and f"his role: {match['role']} ({match.get('level')} match)",
            reasons[0] if reasons else None] + [x for x in facts.get("facts") or [] if isinstance(x, str)]
    line = (f"[{f.finding_id}] " if with_id else "- ") + f"{f.kind}: {f.title}" + (f" ({f.note})" if f.note else "")
    cut = FACT_LINE_MAX if f.kind == "file_fact" else LINE_MAX
    return (line + "".join(f"; {b}" for b in bits if b))[:cut]


def render(profile: Profile) -> str:
    """The profile as text: a heading per section, each item with its refs; "" when empty."""
    lines = []
    for section in PROFILE_SECTIONS:
        if items := getattr(profile, section):
            lines.append(f"{TITLES[section]}:")
            lines += [f"- {i.text} [{', '.join(i.refs)}]" + ("" if i.confidence == "high" else " (unconfirmed)")
                      for i in items]
    return "\n".join(lines)


def known_so_far(ctx) -> str:
    """What the research knows about the expert, for AI checks: the candidate from the search, then
    the profile, or (before the first update) this run's findings, at most PER_KIND per kind."""
    e = ctx.expert
    lines = [f"Name: {e.display_name}", f"Field: {e.field or 'unknown'}", f"Location: {e.location or 'unknown'}",
             f"Affiliations: {'; '.join(e.affiliations) or 'unknown'}", f"Summary: {e.summary}",
             "", "Found when the expert was searched:"]
    lines += [f"- {ev.title}" + (f": {ev.note}" if ev.note else "") for ev in e.evidence]
    profile = store.get_profile(ctx.dossier_id)
    if profile.version:
        lines += ["", "Profile so far (from this research):", render(profile)]
    else:
        by_kind: dict[str, list] = {}
        for f in ctx.findings():
            by_kind.setdefault(f.kind, []).append(f)
        if by_kind:
            lines += ["", "Found so far in this research:"]
            lines += [finding_line(f) for found in by_kind.values() for f in found[:PER_KIND]]
    return "\n".join(lines)[:CONTEXT_MAX]


def context_label(ctx) -> str:
    """What `known_so_far` gives the AI right now, in a few words for logs: "profile v2, 15 items"."""
    profile = store.get_profile(ctx.dossier_id)
    if profile.version:
        return f"profile v{profile.version}, {sum(len(getattr(profile, s)) for s in PROFILE_SECTIONS)} items"
    return f"no profile yet, {len(ctx.findings())} findings"


def items_of(rows: object, known: set[str]) -> list[ProfileItem]:
    """The model's items for one section: refs not in `known` dropped, items left without refs
    dropped, text cut, at most ITEMS_MAX."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip():
            continue
        refs = [r for r in dict.fromkeys(row.get("refs") or []) if r in known]
        if refs:
            out.append(ProfileItem(text=row["text"].strip()[:ITEM_MAX], refs=refs,
                                   confidence="high" if row.get("confidence") == "high" else "low"))
    return out[:ITEMS_MAX]


def bulk(f: Finding) -> bool:
    """Findings that come in bulk and are shown to the model last, so they never crowd out the rest:
    papers, and court cases ruled out as another person (one court_research judges dozens at once)."""
    return f.kind == "work" or (f.kind == "court_docket" and (f.facts.get("match") or {}).get("level") == "ruled out")


async def update(dossier_id: str, expert: Candidate, new: list[Finding], known: set[str]) -> Profile:
    """Merge `new` findings into the run's profile and save it as the next version. `known`: the
    ids of all findings of the run. Raises llm.ModelError when the model fails (the old profile stays)."""
    async with _locks[dossier_id]:
        current = store.get_profile(dossier_id)
        shown = sorted(new, key=bulk)[:NEW_MAX]
        more = len(new) - len(shown)
        prompt = (f"Expert: {expert.display_name} | {expert.field or 'field unknown'} | "
                  f"{expert.location or 'location unknown'}\n\n"
                  f"Current profile:\n{render(current) or '(empty)'}\n\n"
                  f"New findings:\n" + "\n".join(finding_line(f, with_id=True) for f in shown)
                  + (f"\n(and {more} more findings, mostly publications or same-name court cases ruled out)"
                     if more else ""))
        data = (await llm.call_model(INSTRUCTIONS, prompt, schema=SCHEMA)).data
        if not isinstance(data, dict):
            raise llm.ModelError("model returned invalid output")
        profile = Profile(dossier_id=dossier_id, version=current.version + 1, updated_at=store.now(),
                          **{s: items_of(data.get(s), known) for s in PROFILE_SECTIONS})
        store.save_profile(profile)
        return profile
