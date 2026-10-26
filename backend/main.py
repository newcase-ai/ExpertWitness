# Mock backend: returns canned expert candidates. Replace search() with real agents later; the
# frontend only depends on the request/response shape.
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ExpertWitness")


class SearchRequest(BaseModel):
    name: str
    specialty: str = ""
    location: str = ""


class Source(BaseModel):
    label: str
    url: str


class ExpertCandidate(BaseModel):
    id: str  # research id the frontend opens at /experts/:id
    name: str  # display name, with title and credentials
    specialty: str
    location: str
    summary: str
    sources: list[Source]
    match: Literal["strong", "partial"]
    matchReason: str


class SearchResponse(BaseModel):
    candidates: list[ExpertCandidate]


def candidate(id, name, specialty, location, summary, sources):
    return dict(
        id=id, name=name, specialty=specialty, location=location, summary=summary,
        sources=[Source(label=l, url=u) for l, u in sources],
    )


CANDIDATES = [
    candidate(
        "demo-1", "Dr. Margaret Chen, MD", "Orthopedic surgery", "Columbus, OH",
        "Board-certified orthopedic surgeon; reviewed 120+ cases as an expert, mostly for the defense.",
        [("mchen-ortho.com", "https://www.mchen-ortho.com"), ("SEAK directory", "https://www.seak.com"),
         ("Ohio medical board", "https://med.ohio.gov")],
    ),
    candidate(
        "chen-pe", "Margaret Chen, PE", "Structural engineering", "Seattle, WA",
        "Licensed structural engineer; testifies on construction defects and collapse causation.",
        [("Firm bio", "https://example.com/chen-pe"), ("Washington PE license lookup", "https://example.com/wa-pe")],
    ),
    candidate(
        "chen-md-cleveland", "Peter Chen, MD", "Orthopedic surgery", "Cleveland, OH",
        "Hand and upper-extremity surgeon; occasional defense expert in workers' compensation cases.",
        [("Hospital staff page", "https://example.com/peter-chen")],
    ),
    candidate(
        "chen-cpa", "Margaret L. Chen, CPA", "Forensic accounting", "New York, NY",
        "Calculates lost profits and economic damages in commercial disputes.",
        [("Directory listing", "https://example.com/chen-cpa")],
    ),
    candidate(
        "demo-2", "Dr. Robert Alvarez", "Accident reconstruction", "Phoenix, AZ",
        "Reconstructs vehicle and trucking collisions from event data recorders and scene evidence.",
        [("alvarezcollision.com", "https://www.alvarezcollision.com")],
    ),
]


def first_last(name: str) -> tuple[str, str]:
    words = name.split(",")[0].lower().replace("dr.", "").split()
    return words[0], words[-1]


def has(text: str, query: str) -> bool:
    return any(w in text.lower() for w in query.lower().split())


def search(name: str, specialty: str = "", location: str = "") -> list[ExpertCandidate]:
    # ponytail: word-level name and detail matching; real search will score properly
    if not name.split():
        return []
    first, last = first_last(name)
    found = []
    for c in CANDIDATES:
        c_first, c_last = first_last(c["name"])
        same_first, same_last = first == c_first, last == c_last
        if not (same_first or same_last):
            continue
        # which of the details the user typed this person matches, and which they don't
        details = [(k, q, has(c[k], q)) for k, q in (("specialty", specialty), ("location", location)) if q]
        matched = [k for k, _, ok in details if ok]
        missed = [k for k, _, ok in details if not ok]

        if same_first and same_last:
            reason = "Full name" + "".join(f" and {k}" for k in matched) + (" match" if matched else " matches")
        else:
            part = "Last name" if same_last else "First name"
            reason = f"{part}" + "".join(f" and {k}" for k in matched) + (" match" if matched else " matches")
            reason += f", different {'first' if same_last else 'last'} name"
        if missed:
            reason += "".join(f", different {k}" for k in missed)

        strong = same_first and same_last and not missed
        found.append(ExpertCandidate(**c, match="strong" if strong else "partial", matchReason=reason))
    return sorted(found, key=lambda c: c.match != "strong")


@app.post("/api/search")
def search_experts(req: SearchRequest) -> SearchResponse:
    return SearchResponse(candidates=search(req.name, req.specialty, req.location))


if __name__ == "__main__":
    r = {c.id: c for c in search("Margaret Chen", "orthopedic")}
    assert r["demo-1"].match == "strong", r["demo-1"]
    assert r["demo-1"].matchReason == "Full name and specialty match"
    assert r["chen-pe"].match == "partial" and r["chen-pe"].matchReason == "Full name matches, different specialty"
    assert r["chen-md-cleveland"].matchReason == "Last name and specialty match, different first name"
    assert list(r)[0] == "demo-1"
    assert search("Margaret Chen")[0].match == "strong"
    assert [c.id for c in search("robert alvarez")] == ["demo-2"]
    assert search("Nobody Here") == [] and search("   ") == []
    print("ok")
