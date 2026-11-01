"""Tool 5: file note (DESIGN 4B.9, memory 5). A short markdown note per saved file so nobody rereads
it. The model writes short items in its own words (each copy-checked by ctx.ask_model); code builds
the markdown and its header. No findings: tool 8 turns notes into findings."""

from app import memory
from app.evidence import refuse
from app.tools import Ctx, tool

UNKNOWN_FILE = "unknown file"
NO_TEXT = "this file has no text version"
ITEMS_MAX = 10  # per list

INSTRUCTIONS = (
    "You write a short note about one downloaded file for a legal research tool researching one "
    "expert witness. Say in one sentence what the file is. Then list short points, in your own words, "
    "on what it says about the expert's professional work: cases and their role in them, dates, "
    "employers, licences, board certifications, publications. Then list things worth following up "
    "(a named case, another name form, a website or licence number to check). Professional records "
    "only: never private life, family, home address or health. Do not copy text; never add facts."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["what_it_is", "professional_work", "follow_up"],
    "properties": {
        "what_it_is": {"type": "string"},
        "professional_work": {"type": "array", "items": {"type": "string"}},
        "follow_up": {"type": "array", "items": {"type": "string"}},
    },
}


def bullets(items: list) -> list[str]:
    return [f"- {' '.join(str(i).split())}" for i in items[:ITEMS_MAX]] or ["- none"]


def note_md(row: dict, answer: dict) -> str:
    """The note: code-made header (source URL, retrieved date), then the model's items."""
    lines = [f"# File note: {row['file_id']}", f"Source: {row['source_url']}",
             f"Retrieved: {row['retrieved_at'][:10]}", "",
             f"What it is: {' '.join(str(answer.get('what_it_is') or 'not stated').split())}", "",
             "## Professional work", *bullets(answer.get("professional_work") or []), "",
             "## Follow up", *bullets(answer.get("follow_up") or [])]
    return "\n".join(lines)[:memory.NOTE_MAX]


@tool(label="Write a file note", cap=10)
async def note_file(ctx: Ctx, file_id: str) -> dict:
    """Write (and remember) a short markdown note on one downloaded file: what it is, what it says
    about the expert's professional work, and leads to follow up. Use it once per file, after
    download_file; a file that already has a note returns it without rereading."""
    row = memory.file(file_id)
    if not row or row["dossier_id"] != ctx.dossier_id:
        return refuse(UNKNOWN_FILE)
    if row["note_md"]:
        ctx.log("note from memory")
        return {"file_id": file_id, "note_md": row["note_md"]}
    text = ctx.file_text(file_id)
    if not text.strip():
        return refuse(NO_TEXT)
    answer = await ctx.ask_model(INSTRUCTIONS, f"Expert: {ctx.expert.display_name}\n\nFile text:\n{text}", SCHEMA)
    note = note_md(row, answer if isinstance(answer, dict) else {})
    memory.set_file_note(file_id, note)
    ctx.log(f"note saved, {len(note)} characters")
    return {"file_id": file_id, "note_md": note}
