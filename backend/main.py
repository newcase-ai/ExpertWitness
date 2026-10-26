# Mock backend: returns canned expert candidates. Replace search() with real agents later; the
# frontend only depends on the request/response shape.
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ExpertWitness")


class SearchRequest(BaseModel):
    name: str
    hint: str = ""


class ExpertCandidate(BaseModel):
    id: str  # profile id the frontend opens at /experts/:id
    name: str
    specialty: str
    location: str
    otherLocations: list[str]
    summary: str
    urls: list[str]


class SearchResponse(BaseModel):
    candidates: list[ExpertCandidate]


CANDIDATES = [
    ExpertCandidate(
        id="demo-1",
        name="Margaret Chen",
        specialty="Orthopedic surgery, spine injury, surgical standard of care",
        location="Columbus, OH",
        otherLocations=[],
        summary="Board-certified orthopedic surgeon; reviewed 120+ cases, mostly for the defense.",
        urls=["https://www.mchen-ortho.com"],
    ),
    ExpertCandidate(
        id="demo-2",
        name="Robert Alvarez",
        specialty="Accident reconstruction, vehicle dynamics, trucking collisions",
        location="Phoenix, AZ",
        otherLocations=[],
        summary="Reconstructs vehicle and trucking collisions from event data recorders and scene evidence.",
        urls=["https://www.alvarezcollision.com"],
    ),
]


def search(name: str, hint: str = "") -> list[ExpertCandidate]:
    # ponytail: any searched word in the name is a match; hint is ignored until real search exists
    words = [w for w in name.lower().split() if len(w) > 2]
    return [c for c in CANDIDATES if any(w in c.name.lower() for w in words)]


@app.post("/api/search")
def search_experts(req: SearchRequest) -> SearchResponse:
    return SearchResponse(candidates=search(req.name, req.hint))


if __name__ == "__main__":
    assert [c.id for c in search("Margaret Chen")] == ["demo-1"]
    assert [c.id for c in search("dr alvarez")] == ["demo-2"]
    assert search("Nobody Here") == []
    print("ok")
