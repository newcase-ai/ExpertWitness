"""The structured expert profile for the profile page (docs/expert-profile-spec.md). When research
ends, `build` makes one model call that organizes the run's findings and the text of its CV and
practice-site files into typed fields (`ProfileFacts`). Code drops every value that cites nothing of
the run, and contact values (address, phone, email, website) not found in the cited text. `view`
adds what code builds straight from findings (cases, court documents, publications, links, the
exclusion and retraction checks) and each section's status from the tools that ran."""

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import unquote

from pydantic import BaseModel, ValidationError

from app import llm, memory, store
from app.models import (
    AuthorStats,
    BoardCert,
    Candidate,
    Case,
    CaseDocument,
    CurrentRole,
    Education,
    ExpertProfileView,
    Fee,
    Finding,
    Highlight,
    License,
    OnlinePresence,
    ProfileFacts,
    ProfileSource,
    Publication,
    Risk,
    RiskCheck,
    RiskNote,
    Role,
    Sourced,
)
from app.profile import finding_line
from app.sources import host_of, on_domain

AUTO_BUILD = True  # tests turn it off (tests/conftest.py) unless a test is about this step
FILE_CHARS = 30_000  # of one file's text shown to the model (CVs put contact and career first)
FILES_CHARS = 60_000  # of all files' text
FINDINGS_MAX = 150  # finding lines shown to the model
ITEMS_MAX = 40  # per list
COURT_HOSTS = ("courtlistener.com",)
CONTACT = ("business_address", "phone", "email", "website")  # must appear in the cited text
# Tools whose successful run means a section was really looked for ("none found" vs "not checked").
COURT_TOOLS = {"court_records", "court_research", "court_search", "court_case"}
READ_TOOLS = {"note_file", "ask_pdf", "read_web_page", "web_search"}
DIRECTORIES = ("seakexperts", "expertsinmed", "jurispro", "almexperts", "expertinstitute", "expertpages",
               "forensisgroup", "sea-experts", "expertwitness")
DOCTOR_SITES = ("webmd", "doximity", "healthgrades", "vitals", "usnews", "medifind", "npino", "npiprofile",
                "zocdoc", "castleconnolly", "sharecare")

# Strict JSON schema for the model call: every key required; "" or null means unknown.
STR = {"type": "string"}
NSTR = {"type": ["string", "null"]}
NINT = {"type": ["integer", "null"]}
REFS = {"type": "array", "items": STR}
CONF = {"type": "string", "enum": ["high", "low"]}
KIND = {"type": "string", "enum": ["records", "self_reported", "third_party"]}


def obj(**props) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


SOURCED = obj(value=STR, refs=REFS, confidence=CONF, source_kind=KIND, note=NSTR)
RISK_NOTE = obj(status={"type": "string", "enum": ["clear", "flagged", "not_checked"]}, summary=STR, refs=REFS)
SCHEMA = obj(
    headline=SOURCED, specialties=arr(SOURCED), expertise_tags=arr(SOURCED), summary=SOURCED,
    contact=obj(based_in=SOURCED, business_address=SOURCED, phone=SOURCED, email=SOURCED, website=SOURCED,
                current_role=obj(title=STR, org=STR, refs=REFS, confidence=CONF)),
    highlights=arr(obj(label=STR, value=STR, refs=REFS, confidence=CONF, source_kind=KIND, note=NSTR)),
    career=arr(obj(title=STR, org=STR, location=NSTR, start_year=NINT, end_year=NINT,
                   current={"type": "boolean"}, refs=REFS, confidence=CONF)),
    education=arr(obj(degree=STR, field=NSTR, institution=STR, year=NINT, refs=REFS, confidence=CONF)),
    licenses=arr(obj(kind=STR, state=NSTR, number=NSTR,
                     status={"type": "string", "enum": ["active", "expired", "unknown"]}, since=NINT,
                     refs=REFS, confidence=CONF)),
    board_certifications=arr(obj(board=NSTR, specialty=STR, since=NINT, refs=REFS, confidence=CONF)),
    affiliations=arr(SOURCED), memberships=arr(SOURCED),
    fees=arr(obj(service=STR, amount=STR, refs=REFS, source_kind=KIND)),
    testimony_summary=arr(SOURCED), license_status=RISK_NOTE, board_discipline=RISK_NOTE,
    red_flags=arr(SOURCED), not_him=arr(SOURCED),
)
LISTS: dict[str, type[BaseModel]] = {
    "specialties": Sourced, "expertise_tags": Sourced, "highlights": Highlight, "career": Role,
    "education": Education, "licenses": License, "board_certifications": BoardCert, "affiliations": Sourced,
    "memberships": Sourced, "fees": Fee, "testimony_summary": Sourced, "red_flags": Sourced, "not_him": Sourced,
}

INSTRUCTIONS = """You organize what a research run found about one expert witness into a structured
profile for an attorney. You get findings (each line starts with its id in brackets) and the text of
downloaded files such as CVs and practice websites (each starts with its file id in brackets).

Rules:
- Every value cites the ids it comes from in `refs` (finding ids or file ids). Use only what those
  findings or files say: never general knowledge, never a guess. Unknown: value "" (or null, or []).
- A finding about another person with the same name goes under not_him, nowhere else.
- contact: business contact only, as the expert published it for professional contact (CV, practice
  website, expert directory). Copy address, phone, email and website exactly as written in the cited
  file or finding. Never a home address or personal accounts. If the source labels a phone as a cell,
  say so in `note`. based_in: city and state. current_role: the main current job title and employer.
- headline: one line, e.g. "Geriatrician and internist, former hospital chief medical officer".
  specialties: the expert's fields (2-5). expertise_tags: 3-8 short topic tags for case work.
  summary: 3-5 plain sentences on qualifications, each backed by its refs.
- highlights: key numbers as label and short value, e.g. "Experience": "40+ years",
  "Board certification": "Internal medicine (1983)", "Licensed": "Arizona", "Depositions (4 yrs)": "5",
  "Work split": "Plaintiff and defense", "Publications": "250+ (self-reported)".
- career: one row per role with years; current=true for roles to the present. education, licenses
  (status "active" or "expired" only when a source says so), board_certifications, affiliations
  (hospital and academic appointments), memberships, fees (as written, e.g. "$650/hr record review").
- testimony_summary: what the expert reports about their expert work (counts, sides, case types).
- source_kind: "self_reported" for the expert's own CV, website or directory profile; "records" for
  court or official records; "third_party" for other sites.
- license_status and board_discipline: "clear" or "flagged" only when a cited source (a licensing
  board, an official record) shows it; otherwise "not_checked" with no refs.
- red_flags: anything opposing counsel could use (excluded or criticized testimony, discipline,
  disputed opinions, lapsed licenses), each with refs.
- confidence: "high" when an official record or two sources agree, else "low".
- Plain, short, factual text. Use the name or "the expert", not "he" or "she"."""


# Building (the final model call)


def court_file_ids(findings: list[Finding]) -> set[str]:
    return {f.facts.get("file_id") for f in findings if f.kind == "court_file"} - {None}


def source_files(dossier_id: str, findings: list[Finding]) -> list[dict]:
    """The run's downloaded files with text that are not court filings: CVs, practice and directory pages."""
    court = court_file_ids(findings)
    return [r for r in memory.files(dossier_id)
            if r["text_path"] and r["file_id"] not in court and not on_domain(host_of(r["source_url"]), COURT_HOSTS)]


def read_texts(files: list[dict]) -> dict[str, str]:
    texts = {}
    for r in files:
        try:
            texts[r["file_id"]] = Path(r["text_path"]).read_text(errors="replace")
        except OSError:
            continue
    return texts


def finding_text(f: Finding) -> str:
    """What a finding says, for the contact copy check."""
    return " ".join([f.title, f.url, f.note or "", json.dumps(f.facts, ensure_ascii=False)])


def shown(f: Finding) -> bool:
    """Findings the model reads: papers and same-name court cases ruled out are built by code."""
    return f.kind != "work" and not (f.kind == "court_docket" and (f.facts.get("match") or {}).get("level") == "ruled out")


def prompt_of(expert: Candidate, findings: list[Finding], files: list[dict], texts: dict[str, str]) -> str:
    lines = [f"{finding_line(f, with_id=True)} <{f.url}>" for f in findings if shown(f)][:FINDINGS_MAX]
    parts, left = [], FILES_CHARS
    for r in files:
        text = texts.get(r["file_id"], "")[:min(FILE_CHARS, left)]
        if not text:
            continue
        left -= len(text)
        parts.append(f"=== [{r['file_id']}] {r['source_url']}\n{text}")
    return (f"Expert: {expert.display_name} | {expert.field or 'field unknown'} | "
            f"{expert.location or 'location unknown'}\n\nFindings:\n" + ("\n".join(lines) or "(none)")
            + "\n\nFiles:\n" + ("\n\n".join(parts) or "(none)"))


async def build(dossier_id: str, expert: Candidate) -> ProfileFacts:
    """Organize the run into ProfileFacts with one model call, check them and save them.
    Raises llm.ModelError when the model fails (nothing is saved)."""
    findings = store.findings(dossier_id)
    files = source_files(dossier_id, findings)
    texts = await asyncio.to_thread(read_texts, files)
    data = (await llm.call_model(INSTRUCTIONS, prompt_of(expert, findings, files, texts), schema=SCHEMA)).data
    if not isinstance(data, dict):
        raise llm.ModelError("model returned invalid output")
    by_ref = {f.finding_id: finding_text(f) for f in findings}
    by_ref |= {r["file_id"]: f"{r['source_url']}\n{texts[r['file_id']]}" for r in files if r["file_id"] in texts}
    facts = clean(data, by_ref)
    store.save_profile_facts(dossier_id, facts.model_dump())
    return facts


# Code checks on the model's output


def keep[M: BaseModel](model: type[M], raw: object, known: set[str]) -> M | None:
    """`raw` as `model`, with refs cut to real refs of the run; None when it cites none or is invalid.
    Empty strings count as missing, so a required field left "" drops the value."""
    if not isinstance(raw, dict):
        return None
    raw = {k: v for k, v in raw.items() if v not in ("", None)}
    refs = raw.get("refs") if isinstance(raw.get("refs"), list) else []
    raw["refs"] = [r for r in dict.fromkeys(refs) if isinstance(r, str) and r in known]
    if raw.get("confidence") not in ("high", "low"):
        raw.pop("confidence", None)
    if not raw["refs"]:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError:
        return None


def words(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def phones(text: str) -> set[str]:
    return {re.sub(r"\D", "", m) for m in re.findall(r"\+?\(?\d[\d\s().-]{5,}\d", text)}


def appears(field: str, value: str, text: str) -> bool:
    """Is a contact value written in `text`? Punctuation, case and spacing aside; a phone by its digits
    (a leading country code may differ); a website without its scheme and www."""
    if field == "phone":
        digits = re.sub(r"\D", "", value)
        return len(digits) >= 7 and any(d.endswith(digits) or digits.endswith(d) for d in phones(text) if len(d) >= 7)
    if field == "website":
        value = re.sub(r"^(https?://)?(www\.)?", "", value.strip().lower()).rstrip("/")
    needle = words(value)
    return needle.strip() != "" and needle in words(text)


def clean(raw: dict, by_ref: dict[str, str]) -> ProfileFacts:
    known = set(by_ref)

    def items(name: str) -> list:
        rows = raw.get(name) if isinstance(raw.get(name), list) else []
        return [x for r in rows if (x := keep(LISTS[name], r, known))][:ITEMS_MAX]

    contact_in = raw.get("contact") if isinstance(raw.get("contact"), dict) else {}
    contact = {"current_role": keep(CurrentRole, contact_in.get("current_role"), known)}
    for name in ("based_in", *CONTACT):
        x = keep(Sourced, contact_in.get(name), known)
        if x and name in CONTACT and not any(appears(name, x.value, by_ref[r]) for r in x.refs):
            x = None  # not written in any cited source: never show an invented phone or address
        contact[name] = x
    risk = {}
    for name in ("license_status", "board_discipline"):
        note = keep(RiskNote, raw.get(name), known)
        risk[name] = note if note and note.summary else RiskNote()
    return ProfileFacts(
        headline=keep(Sourced, raw.get("headline"), known), summary=keep(Sourced, raw.get("summary"), known),
        contact=contact, **risk, **{name: items(name) for name in LISTS},
    )


# The view (code-built parts and statuses)


def year_of(date: object) -> int | None:
    s = str(date or "")[:4]
    return int(s) if s.isdigit() else None


def cases_of(findings: list[Finding]) -> list[Case]:
    cases = []
    for f in findings:
        if f.kind != "court_docket":
            continue
        match = f.facts.get("match") if isinstance(f.facts.get("match"), dict) else {}
        if match.get("level") == "ruled out":
            continue
        cases.append(Case(
            name=f.facts.get("case_name") or f.title, court=f.facts.get("court") or "",
            docket_number=f.facts.get("docket_number"), year=year_of(f.facts.get("date_filed")), url=f.url,
            nature_of_suit=f.facts.get("nature_of_suit"), involvement=match.get("role"),
            # ponytail: the court tool writes no summary yet; its verdict reasons stand in
            summary=f.facts.get("summary") or " ".join(match.get("reasons") or []) or None,
            refs=[f.finding_id], confidence="high" if match.get("level", "high") == "high" else "low"))
    return sorted(cases, key=lambda c: c.year or 0, reverse=True)


def documents_of(findings: list[Finding]) -> list[CaseDocument]:
    return [CaseDocument(title=f.title, case_name=f.facts.get("case_name"), date=f.facts.get("date_filed"),
                         url=f.url, page_count=f.facts.get("page_count"), file_id=f.facts.get("file_id"),
                         refs=[f.finding_id])
            for f in findings if f.kind == "court_file"]


def publications_of(findings: list[Finding]) -> list[Publication]:
    retracted = {f.title for f in findings if f.kind == "retraction"}
    works = [Publication(title=f.title, venue=f.facts.get("venue"), year=f.facts.get("year"), url=f.url,
                         cited_by=f.facts.get("cited_by_count"),
                         retracted=bool(f.facts.get("is_retracted")) or f.title in retracted, refs=[f.finding_id])
             for f in findings if f.kind == "work"]
    return sorted(works, key=lambda w: w.year or 0, reverse=True)


def stats_of(findings: list[Finding]) -> AuthorStats | None:
    """OpenAlex often splits one person over several author ids (gotcha 12): add them up."""
    authors = [f.facts for f in findings if f.kind == "author_stats"]
    if not authors:
        return None
    h = [a["h_index"] for a in authors if isinstance(a.get("h_index"), int)]
    return AuthorStats(works=sum(a.get("works_count") or 0 for a in authors),
                       citations=sum(a.get("cited_by_count") or 0 for a in authors),
                       h_index=max(h) if h else None, source="OpenAlex")


def online_of(findings: list[Finding], files: list[dict], website: str | None) -> list[OnlinePresence]:
    """Pages the run read (web facts, CV files), one per URL, by kind."""
    site = host_of(website if website and "://" in website else f"https://{website}") if website else None
    rows: dict[str, OnlinePresence] = {}
    pages = [(f.url, f.title, f.finding_id) for f in findings if f.kind.startswith("web_") or f.kind == "file_fact"]
    pages += [(r["source_url"], host_of(r["source_url"]), r["file_id"]) for r in files]
    for url, title, ref in pages:
        url = url.split("#")[0]
        key = unquote(url)  # the same CV is linked both encoded and plain
        if key in rows:
            rows[key].refs.append(ref)
            continue
        host, low = host_of(url), (url + " " + title).lower()
        kind = ("practice_site" if site and on_domain(host, (site,))
                else "cv" if re.search(r"\bcv\b|curriculum|vitae|resume", low)
                else "expert_directory" if any(d in host for d in DIRECTORIES)
                else "physician_directory" if any(d in host for d in DOCTOR_SITES) else "other")
        rows[key] = OnlinePresence(title=title, url=url, kind=kind, refs=[ref])
    return list(rows.values())


def risk_of(findings: list[Finding], ran: set[str], facts: ProfileFacts) -> Risk:
    def sourced(f: Finding) -> Sourced:
        return Sourced(value=f.title, refs=[f.finding_id], confidence=f.confidence, note=f.note)

    def tool_check(kind: str, tool: str, flagged: str, clear: str, source: str) -> RiskCheck:
        hits = [f for f in findings if f.kind == kind]
        if hits:
            return RiskCheck(status="flagged", summary=flagged.format(len(hits)), items=[sourced(f) for f in hits],
                             checked_sources=[source])
        if tool in ran:
            return RiskCheck(status="clear", summary=clear, checked_sources=[source])
        return RiskCheck(status="not_checked", summary="Not checked")

    def noted(note: RiskNote) -> RiskCheck:
        if note.status == "not_checked" or not note.refs:
            return RiskCheck(status="not_checked", summary="Not checked")
        return RiskCheck(status=note.status, summary=note.summary,
                         items=[Sourced(value=note.summary, refs=note.refs)], checked_sources=[])

    return Risk(
        license_status=noted(facts.license_status), board_discipline=noted(facts.board_discipline),
        exclusion_lists=tool_check("leie_possible_match", "exclusion_check",
                                   "{} possible match(es) in the OIG exclusion list: verify",
                                   "No match in the OIG exclusion list (LEIE)", "OIG LEIE"),
        retractions=tool_check("retraction", "retraction_check", "{} retracted paper(s)",
                               "No retracted papers found", "Crossref, OpenAlex"),
    )


def refs_in(data: object) -> list[str]:
    """Every ref cited anywhere in a dumped view, in order."""
    if isinstance(data, dict):
        return [r for k, v in data.items() for r in (v if k == "refs" and isinstance(v, list) else refs_in(v))]
    if isinstance(data, list):
        return [r for v in data for r in refs_in(v)]
    return []


def view(dossier_id: str) -> ExpertProfileView | None:
    """The run's structured profile: the saved model part (empty when not built) plus the code-built
    lists and statuses. None for an unknown dossier."""
    research = store.get_research(dossier_id)
    if research is None:
        return None
    candidate = store.get_candidate(research.candidate_id)
    findings = research.findings
    files = source_files(dossier_id, findings)
    saved = store.get_profile_facts(dossier_id)
    facts = ProfileFacts.model_validate(saved[0]) if saved else ProfileFacts()
    ran = {a.tool for a in store.activity(research.job_id) if a.status == "done"}
    read = bool(saved) and bool(ran & READ_TOOLS or files)

    def section(items: list, looked: bool) -> dict:
        return {"status": "found" if items else "none_found" if looked else "not_checked", "items": items}

    publications = publications_of(findings)
    website = facts.contact.website.value if facts.contact.website else None
    by_id = {f.finding_id: f for f in findings}
    other = {r for x in facts.not_him for r in x.refs}  # court cases the model judged another person
    out = ExpertProfileView(
        dossier_id=dossier_id, status=research.status, updated_at=saved[1] if saved else None, built=bool(saved),
        name=candidate.display_name if candidate else "", headline=facts.headline, specialties=facts.specialties,
        expertise_tags=facts.expertise_tags, summary=facts.summary, contact=facts.contact,
        highlights=facts.highlights,
        **{name: section(getattr(facts, name), read) for name in (
            "career", "education", "licenses", "board_certifications", "affiliations", "memberships", "fees",
            "testimony_summary")},
        cases=section([c for c in cases_of(findings) if c.refs[0] not in other], bool(ran & COURT_TOOLS)),
        documents=section([d for d in documents_of(findings) if not {d.refs[0], by_id[d.refs[0]].facts.get("case_ref")}
                           & other], bool(ran & COURT_TOOLS)),
        risk=risk_of(findings, ran, facts),
        red_flags=section(facts.red_flags, bool(saved)),
        not_him=section(facts.not_him, bool(saved)),
        publications=section(publications, "publications" in ran) | {"stats": stats_of(findings)},
        online=section(online_of(findings, files, website), bool(ran & READ_TOOLS)),
    )
    file_rows = {r["file_id"]: r for r in memory.files(dossier_id)}
    sources = []
    for ref in dict.fromkeys(refs_in(out.model_dump())):
        if f := by_id.get(ref):
            sources.append(ProfileSource(ref=ref, title=f.title, url=f.url, kind=f.kind))
        elif r := file_rows.get(ref):
            sources.append(ProfileSource(ref=ref, title=host_of(r["source_url"]), url=r["source_url"], kind="file"))
    out.sources = sources
    return out
