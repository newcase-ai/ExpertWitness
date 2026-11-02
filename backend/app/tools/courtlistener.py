"""Tool 1: court records (CourtListener; DESIGN 4B.9). Gotchas 5, 11, 13, 23: rate limits, cache and
the token check stay in app/sources/courtlistener.py; surname-only matches are never kept.

Federal dockets and filings (RECAP, CourtListener's archive of PACER), not opinions: most cases never
get an opinion, while experts show up in filings (user decision 2026-11-01). Same person or only the
same name: the AI compares each case with what the research knows so far about the expert (the
candidate and the profile) and gives a 0-100 likelihood, the person's role in the case and a reason.
The code-made score below is the fallback when the model fails."""

import json
import re

from app import llm
from app.evidence import refuse, words
from app.profile import context_label, known_so_far
from app.sources import US_STATES, courtlistener, fold, has_full_name, state_of
from app.tools import RESULT_MAX, Ctx, tool
from app.tools.court_files import check_case_files

MAX_CASES = 10
STEM = 6  # ponytail: crude stemming, a shared 6-letter prefix counts ("chiropractic" ~ "chiropractor")
KEEP_SCORE = 25  # full name alone: kept as level "low" (user decision 2026-10-29)
MEDIUM_SCORE = 40
HIGH_SCORE = 75
CASE_NAME_MAX = 200
# Same-person score (0-100): points and the code-made reason shown with them.
EVIDENCE = (50, "case link is in the candidate's evidence")
FULL_NAME = (25, "full name in the filings")
FIELD_WORD = (15, "field word near the name")
STATE_COURT = (10, "court in the expert's state")
SKIPPED_GAP = "{} same-name court cases skipped: not tied to this person"
NONE_GAP = "no federal court filings in CourtListener (RECAP) name this person"
FOUND_NOTE = "found in federal court records (CourtListener RECAP)"
LOW_NOTE = "full name only; verify it is the same person"
AI_LOW_NOTE = "may be another person with the same name; verify it is the same person"

# The AI same-person check (user decision 2026-11-01): context from app/profile.py (known_so_far).
REASON_MAX = 300
ROLES = ("expert witness", "consultant or reviewer", "party", "judge", "lawyer", "other witness",
         "mentioned only", "unclear")
CHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cases"],
    "properties": {"cases": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "same_person", "role", "reason"],
        "properties": {"id": {"type": "string"}, "same_person": {"type": "integer"},
                       "role": {"type": "string"}, "reason": {"type": "string"}},
    }}},
}
CHECK_INSTRUCTIONS = f"""You check whether federal court cases are about one specific expert witness
or about someone else with the same name. You get what the research knows about the expert so far
and, for each case (C1, C2, ...), its name, court, date, docket details and passages from the filings
that name the person. For each case give: same_person, how likely (0-100) the person named in it is
this expert; role, that person's role in the case, one of: {", ".join(ROLES)}; reason, one sentence
in your own words naming the details that match or conflict. A shared name is not evidence: many
people share a name. Raise the likelihood for concrete matches (same profession or specialty, same
kind of cases, same employer, institution, credentials, state or city). Lower it for conflicts (a
different profession, or the person is a party, an officer or a judge while the expert is, say, a
physician). If the passages do not say enough, say "unclear" and stay near 50. Use only what you are
given; never invent facts. Professional facts only."""


def plain_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def newest_cases(hits: list[dict], evidence: set[str]) -> list[dict]:
    """Up to MAX_CASES distinct cases: those in the candidate's evidence first, then newest filing date first."""
    seen, picked = set(), []
    for hit in sorted(hits, key=lambda h: h.get("date_filed") or "", reverse=True):
        if (key := plain_url(hit["url"])) not in seen:
            seen.add(key)
            picked.append(hit)
    picked.sort(key=lambda h: plain_url(h["url"]) not in evidence)  # stable: dates stay in order
    return picked[:MAX_CASES]


def mentions_field(passages: list[str], field: str | None) -> bool:
    """True if a passage around the name shares a specialty word (or its STEM prefix) with the field."""
    wanted = {w[:STEM] for w in words(field)}
    return bool(wanted) and any(w[:STEM] in wanted for w in words(" ".join(passages)))


def court_in_state(court: str, state: str | None) -> bool:
    """True if the court's name holds the state's name ("District Court, N.D. Ohio" for OH).
    Longer state names holding it are removed first ("West Virginia" is not Virginia)."""
    text = fold(court)
    for wanted in (n for n, code in US_STATES.items() if code == state):
        for other in US_STATES:
            if wanted in other and other != wanted:
                text = text.replace(other, " ")
        if re.search(rf"\b{wanted}\b", text):
            return True
    return False


def level_of(score: int) -> str:
    return "high" if score >= HIGH_SCORE else "medium" if score >= MEDIUM_SCORE else "low"


def match(in_evidence: bool, passages: list[str], court: str, ctx: Ctx) -> dict | None:
    """Code-made same-person score, or None for a surname-only case (gotcha 13): no full name in
    the passages and not in the candidate's evidence."""
    expert = ctx.expert
    full_name = has_full_name(" ".join(passages), expert.display_name)
    if not in_evidence and not full_name:
        return None
    checks = [(in_evidence, EVIDENCE), (full_name, FULL_NAME),
              (full_name and mentions_field(passages, expert.field), FIELD_WORD),
              (court_in_state(court, state_of(expert.location)), STATE_COURT)]
    score = sum(points for ok, (points, _) in checks if ok)
    return {"score": score, "level": level_of(score), "reasons": [reason for ok, (_, reason) in checks if ok],
            "checked_by": "code"}


def ai_lines(hit: dict, name: str) -> list[str]:
    """What the AI reads about one case besides its name, court and date: docket details, the filings
    that name the person (description and search passage) and whether the docket lists them as a party
    or lawyer. Code-made; the passages stay in memory (gotcha 15)."""
    details = "; ".join(x for x in (hit["docket_number"] and f"docket {hit['docket_number']}",
                                    hit["nature_of_suit"] and f"nature of suit: {hit['nature_of_suit']}") if x)
    lines = [details] if details else []
    lines += [f"{name} is named as a {role} on the docket" for role in hit["named_as"]]
    descriptions = [d["description"] for d in hit["documents"]]
    for n, passage in enumerate(hit["passages"]):
        lines.append(f"{descriptions[n]}: {passage}" if n < len(descriptions) and descriptions[n] else passage)
    return lines


async def same_person_check(ctx: Ctx, cases: list[dict]) -> tuple[dict[str, dict] | None, str]:
    """(the AI's verdict per case id ("C1", ...): {score, level, role, reasons, checked_by, context}, what
    it was checked against for the log). `cases`: {id, case_name, court, date_filed, lines}. No
    verdicts (and a gap) when the model fails."""
    against = context_label(ctx)
    blocks = [f"{c['id']}: {c['case_name'] or 'unnamed case'} | {c['court'] or 'court unknown'} | filed "
              f"{c['date_filed'] or 'unknown'}\n" + ("\n".join(f"- {line}" for line in c["lines"])
                                                    or "- (no text around the name)") for c in cases]
    prompt = (f"The expert:\n{known_so_far(ctx)}\n\nFederal court cases naming {ctx.expert.display_name}:\n\n"
              + "\n\n".join(blocks))
    try:
        data = await ctx.ask_model(CHECK_INSTRUCTIONS, prompt, schema=CHECK_SCHEMA)
    except llm.ModelError as e:
        ctx.add_gap(f"AI same-person check failed ({e}); code score used")
        return None, "AI check failed, code score used"
    verdicts = {}
    for row in data.get("cases", []) if isinstance(data, dict) else []:
        try:
            score = max(0, min(100, int(row["same_person"])))
        except (KeyError, TypeError, ValueError):
            continue  # this case keeps its code score
        role = row.get("role") if row.get("role") in ROLES else "unclear"
        reason = (row.get("reason") or "")[:REASON_MAX]  # dropped by ask_model if it copied the passages
        verdicts[row.get("id")] = {"score": score, "level": level_of(score), "role": role,
                                   "reasons": [reason] if reason else [], "checked_by": "ai", "context": against}
    return verdicts, f"checked against {against}"


def case_facts(hit: dict, same: dict) -> dict:
    """A case finding's facts: fields copied by code from the docket, and the same-person verdict."""
    return {k: hit[k] for k in ("case_name", "court", "docket_number", "date_filed", "nature_of_suit",
                                "named_as", "documents")} | {"match": same}


def shown(same: dict) -> dict:
    return {"score": same["score"], "level": same["level"], "role": same.get("role"), "why": "; ".join(same["reasons"])}


@tool(label="Court records", cap=1)
async def court_records(ctx: Ctx) -> dict:
    """Find federal court cases whose filings name the expert in full (CourtListener's RECAP archive
    of PACER dockets: expert reports, declarations, reviewing physicians' letters, motions about
    experts), or list them as a party or lawyer. Each case is checked against what you have found so
    far (the profile) and scored 0-100 as the same person, with the person's `role` and `why`: 75+ is
    level "high", 40-74 "medium", 25-39 "low" (it may be someone else with the same name; say so if
    you use it). Lower scores are not kept. Federal cases only, and only filings someone has bought
    from PACER: an empty result does not mean the expert never testified. Use it once, after
    publications and web search; call it again only if its error says that can help (CourtListener
    is sometimes slow). Takes no arguments. Each case comes with one passage around the name;
    `skipped_same_name` counts cases about someone else with the same name. For high and medium cases
    the case's public filings are checked too: those that are the expert's own work (report,
    declaration, deposition, CV) or about their testimony (a motion or ruling to admit, limit or
    exclude them) are saved; `files` lists them (file_id, title, pages): read one with ask_pdf."""
    name = ctx.expert.display_name
    hits = await courtlistener.search_dockets(name)
    if not hits:
        ctx.add_gap(NONE_GAP)
    evidence = {plain_url(e.url) for e in ctx.expert.evidence}
    cases, checked, skipped, files = [], [], 0, []
    for hit in newest_cases(hits, evidence):
        lines = ai_lines(hit, name)
        code = match(plain_url(hit["url"]) in evidence, lines, hit["court"], ctx)
        if code is None:  # surname only (gotcha 13): never kept, never sent to the AI
            skipped += 1
            continue
        checked.append((hit, lines, code))
    verdicts, against = await same_person_check(ctx, [
        {"id": f"C{i}", "case_name": hit["case_name"], "court": hit["court"], "date_filed": hit["date_filed"],
         "lines": lines} for i, (hit, lines, _) in enumerate(checked, 1)]) if checked else ({}, "")
    for i, (hit, _, code) in enumerate(checked, 1):
        same = (verdicts or {}).get(f"C{i}") or code
        if same["score"] < KEEP_SCORE:
            skipped += 1
            continue
        note = None
        if same["level"] == "low":
            note = AI_LOW_NOTE if same["checked_by"] == "ai" else LOW_NOTE
        # The search passages are never stored (gotcha 15): only fields copied by code and the verdict.
        ref = ctx.add_finding("court_docket", hit["case_name"], hit["url"], facts=case_facts(hit, same),
                              confidence="high" if same["level"] == "high" else "low", note=note,
                              source_type="courtlistener_docket")
        if ref:
            cases.append({"ref": ref, "case_name": hit["case_name"], "court": hit["court"],
                          "docket_number": hit["docket_number"], "date_filed": hit["date_filed"], **shown(same),
                          "passage": hit["passages"][0] if hit["passages"] else ""})
            files += await check_case_files(ctx, hit, same, ref)
    if skipped:
        ctx.add_gap(SKIPPED_GAP.format(skipped))
    ctx.log(f"{len(hits)} cases found, {len(cases)} kept, {skipped} same-name skipped"
            + (f" · {against}" if against else "") + (f", {len(files)} files saved" if files else ""))
    return fit({"cases": cases, "skipped_same_name": skipped, "files": files})


# Cuts that make a worst-case reply (10 long cases, many saved files) fit, in order: (list, key, size;
# None removes the key). The wrapper drops a longer reply whole; every file is listed if at all possible.
FIT_CUTS = (("files", "title", 30), ("cases", "passage", 200), ("cases", "why", 150), ("files", "title", None),
            ("cases", "passage", None), ("cases", "why", None), ("cases", "case_name", 60),
            ("cases", "court", None), ("cases", "docket_number", None))


def fit(reply: dict) -> dict:
    """`reply` cut in FIT_CUTS order until it fits RESULT_MAX; as a last resort the last files are left
    out (`files_not_listed`; ask_pdf's unknown-file error lists every file_id)."""
    for where, key, size in FIT_CUTS:
        if len(json.dumps(reply)) <= RESULT_MAX:
            return reply
        for item in reply.get(where) or []:
            if size is None:
                item.pop(key, None)
            elif key in item:
                item[key] = (item[key] or "")[:size]
    while len(json.dumps(reply)) > RESULT_MAX and reply.get("files"):
        reply["files"].pop()
        reply["files_not_listed"] = reply.get("files_not_listed", 0) + 1
    return reply


def same_case_name(a: str, b: str) -> bool:
    return re.findall(r"[a-z0-9]+", fold(a)) == re.findall(r"[a-z0-9]+", fold(b))


def saved_case(ctx: Ctx, case_name: str = "", url: str = ""):
    """This run's court finding for the case (by case name or docket URL), if any."""
    for f in ctx.findings():
        if f.kind in ("court_docket", "case_check") and (
                (url and plain_url(f.url) == plain_url(url))
                or (case_name and same_case_name(f.facts.get("case_name") or f.title, case_name))):
            return f
    return None


def already_found(ctx: Ctx, found) -> dict:
    """The reply for a case this run already saved: its verdict, not a new search or AI check."""
    record = found.facts.get("court_record") or found.facts
    match = found.facts.get("match")
    ctx.log(f"already found ({found.finding_id}); not checked again")
    return {"found": True, "ref": found.finding_id, "already_found": True,
            **{k: record.get(k) for k in ("case_name", "court", "date_filed", "docket_number")},
            "same_person": shown(match) if match else None}


@tool(label="Court case lookup", cap=6)
async def court_case(ctx: Ctx, case_name: str, court: str = "", year: int = 0) -> dict:
    """Check whether one named federal court case (for example "Doe v. Example Co.") is in
    CourtListener's RECAP archive of PACER dockets and its filings name the expert. Use it for a case
    some finding or the testimony list names, never a guessed one. `court` and `year` (filing year)
    are optional and pick the best of several hits. The case is checked against what you have found
    so far: `same_person` (score, level, role, why); a case about someone else with the same name is
    not saved. Not found -> {"found": false}. If it fails, its error says whether calling again can help.
    Not for cases court_records already returned: they are checked and saved (a repeat call only
    returns the saved verdict, `already_found`). A saved high or medium case also gets its filings
    checked: `files` lists the saved ones (the expert's own work or about their testimony;
    file_id, title, pages): read one with ask_pdf."""
    if len(case_name) > CASE_NAME_MAX:
        return refuse("case name too long")
    if found := saved_case(ctx, case_name=case_name):
        return already_found(ctx, found)
    hit = await courtlistener.find_case(case_name, ctx.expert.display_name, court or None, year or None)
    if not hit:
        ctx.log("not found")
        return {"found": False}
    if found := saved_case(ctx, url=hit["url"]):  # another name for a saved case
        return already_found(ctx, found)
    record = {k: hit.get(k) for k in ("case_name", "court", "date_filed", "docket_number")}
    # The passages come from search snippets: to the AI and in the reply only, never stored (gotcha 15).
    verdicts, against = await same_person_check(ctx, [{"id": "C1", "case_name": hit["case_name"],
                                                       "court": hit["court"], "date_filed": hit["date_filed"],
                                                       "lines": ai_lines(hit, ctx.expert.display_name)}])
    same = (verdicts or {}).get("C1") or {"score": KEEP_SCORE, "level": "low", "role": None,
                                          "reasons": ["full name in the case"], "checked_by": "code"}
    if same["score"] < KEEP_SCORE:
        ctx.log(f"found, another person · {against}")
        return {"found": True, "ref": None, **record, "same_person": shown(same), "passages": hit["passages"]}
    low = same["level"] != "high"
    ref = ctx.add_finding(
        "case_check", hit.get("case_name") or case_name, hit["url"],
        facts={"case_name": case_name, "court": court or None, "year": str(year) if year else None,
               "status": "found", "how": "courtlistener", "link": hit["url"], "court_record": record,
               "documents": hit["documents"], "match": same},
        confidence="low" if low else "high",
        note=f"{FOUND_NOTE}; {AI_LOW_NOTE if same['checked_by'] == 'ai' else LOW_NOTE}" if low else FOUND_NOTE,
        source_type="courtlistener_docket")
    files = await check_case_files(ctx, hit, same, ref) if ref else []
    ctx.log(f"found · {against}" + (f", {len(files)} files saved" if files else ""))
    return fit({"found": True, "ref": ref, **record, "same_person": shown(same), "passages": hit["passages"],
                "files": files})
