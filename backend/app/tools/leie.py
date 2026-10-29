"""Tool 11: OIG exclusion list (LEIE; DESIGN 4B.9). A name match is only a possible match; the CSV
download and the kept columns stay in app/sources/leie.py."""

from app.sources import SourceSkipped, given_family, leie, name_words, state_of
from app.tools import Ctx, tool

LEIE_NOTE = "possible match by name only; verify at the OIG online search"
FACT_KEYS = ("general", "specialty", "excltype", "excldate", "reindate", "state")
OTHER_STATE_GAP = "{} OIG exclusion list name matches skipped: listed in another state than {}"


def middle_initial(full_name: str) -> str | None:
    words = name_words(full_name)
    return words[1][0].upper() if len(words) > 2 else None


@tool(label="OIG exclusion list", cap=1)
async def exclusion_check(ctx: Ctx) -> dict:
    """Check the federal OIG exclusion list (LEIE) for the expert by name. A name match is only a
    possible match, never proof that this person is excluded; say so if you use it. Matches listed
    in another state than the expert's are dropped. Use it once. Takes no arguments."""
    full_name = ctx.expert.display_name
    first, last = given_family(full_name)
    if not first or first == last:
        raise SourceSkipped("need a given and a family name for the LEIE check")
    matches = await leie.possible_matches(first, last, middle_initial=middle_initial(full_name))
    if state := state_of(ctx.expert.location):  # unknown state: every name match stays possible
        other = [m for m in matches if m.get("state") and str(m["state"]).strip().upper() != state]
        matches = [m for m in matches if m not in other]
        if other:
            ctx.add_gap(OTHER_STATE_GAP.format(len(other), state))
    refs = []
    for i, m in enumerate(matches, 1):
        # Every row links the same OIG search page: a #fragment keeps each match its own finding,
        # since the same kind + URL returns the existing ref (PLAN gotcha 49).
        ref = ctx.add_finding(
            "leie_possible_match", f"OIG exclusion list: possible match ({m.get('general') or 'unknown category'})",
            f"{m['url']}#possible-match-{i}", facts={k: m.get(k) for k in FACT_KEYS}, confidence="low",
            note=LEIE_NOTE, source_type="oig_leie")
        if ref:
            refs.append(ref)
    ctx.log(f"{len(refs)} possible matches")
    return {"possible_matches": len(refs), "refs": refs}
