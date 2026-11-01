"""The expert profile for the main agent (app/profile.py keeps it up to date): read it, and check a
result against it ("is this about our expert, and what does it add?"). Nothing is saved."""

from app import llm, profile, store
from app.evidence import refuse
from app.tools import Ctx, tool

REPLY_MAX = 7_000  # characters of the profile in read_profile's reply
ITEM_MAX = 4_000  # characters of the item checked
ANSWER_MAX = 1_000
FITS = ("new", "confirms", "contradicts", "unrelated")

CHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["same_person", "fit", "answer"],
    "properties": {"same_person": {"type": "integer"}, "fit": {"type": "string"}, "answer": {"type": "string"}},
}
CHECK_INSTRUCTIONS = f"""You help vet one expert witness. You get what the research knows about the
expert and one item (a search result, a passage, a finding) plus a question. Decide from that only:
same_person, how likely (0-100) the item is about this expert and not someone else with the same
name (a shared name is not evidence; a matching profession, specialty, employer, place or kind of
case is); fit, one of {", ".join(FITS)} (unrelated when it is not about the expert); answer, the
answer to the question in at most 3 sentences of your own words, naming what matched or conflicted.
Never invent facts. Professional facts only."""


@tool(label="Read the profile", cap=20)
async def read_profile(ctx: Ctx) -> dict:
    """What we know so far about the expert, kept up to date automatically after every tool that
    saves findings: identity, expertise, credentials, cases, publications, red flags, people with the
    same name ruled out ("not him"), conflicts and open leads. Each item ends with the finding refs
    it rests on. Read it before asking a file (ask_pdf) or choosing what to search next, and use
    open leads. Takes no arguments."""
    current = store.get_profile(ctx.dossier_id)
    ctx.log(f"version {current.version}")
    return {"version": current.version, "profile": profile.render(current)[:REPLY_MAX] or "empty: nothing found yet"}


@tool(label="Check against the profile", cap=30)
async def check_against_profile(ctx: Ctx, item: str, question: str = "") -> dict:
    """Is this about our expert, and does it fit what we know? `item`: a finding ref (fnd_...) or
    text you have (a search result's title, URL and snippet, or a passage). `question`: what you want
    to know (optional). Reply: `same_person` 0-100 (a shared name alone is not evidence), `fit`
    (new, confirms, contradicts or unrelated) and `answer`. Nothing is saved."""
    item = item.strip()
    if item.startswith("fnd_"):
        found = next((f for f in ctx.findings() if f.finding_id == item), None)
        if found is None:
            return refuse("unknown finding ref")
        item = profile.finding_line(found) + (f" | {found.url}" if found.url else "")
    if not item:
        return refuse("nothing to check: give a finding ref or the text")
    prompt = (f"What the research knows:\n{profile.known_so_far(ctx)}\n\nItem:\n{item[:ITEM_MAX]}\n\n"
              f"Question: {question.strip() or 'Is this about the expert, and what does it add?'}")
    try:
        data = (await llm.call_model(CHECK_INSTRUCTIONS, prompt, schema=CHECK_SCHEMA)).data
        same = max(0, min(100, int(data["same_person"])))
    except llm.ModelError as e:
        return refuse(f"{e}; try again")
    except (KeyError, TypeError, ValueError):
        return refuse("the model gave no usable answer; try again")
    fit = data.get("fit") if data.get("fit") in FITS else "unrelated"
    ctx.log(f"same person {same}, {fit} · checked against {profile.context_label(ctx)}")
    return {"same_person": same, "fit": fit, "answer": str(data.get("answer") or "")[:ANSWER_MAX]}
