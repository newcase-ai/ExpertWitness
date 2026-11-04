# Expert Witness API

Give it an expert's name, get back a cited expert profile: court cases and testimony, licenses and discipline, the exclusion and retraction checks, publications and career. Used to vet opposing experts, or our own before retention.

See [DESIGN.md](DESIGN.md) for the pipeline, the data sources and the module layout (section 9), and the root [CLAUDE.md](../CLAUDE.md) for conventions and gotchas.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Run locally

```sh
cp .env.example .env   # then fill in the keys
uv sync
uv run fastapi dev app/main.py
```

The API runs at http://127.0.0.1:8000. Interactive docs are at http://127.0.0.1:8000/docs.

Data (the SQLite file, saved downloads, the LEIE CSV) goes to `backend/data/`, which is gitignored. Set `DATABASE_PATH` in `.env` to use another file.

## Test

```sh
uv run pytest -q
uv run ruff check
```

## Endpoints

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok"}` |
| POST | `/candidate-searches` | 202: starts a candidate search job |
| GET | `/candidate-searches` | The newest 100 searches with their research runs |
| GET | `/candidate-searches/{search_id}` | One search and its candidates |
| GET | `/jobs/{job_id}` | Job status, steps and the activity log (one row per tool call) |
| GET | `/jobs/{job_id}/events` | The run trace (poll with `after`) |
| POST | `/research` | 202: starts a research job for a candidate, with an optional pasted `testimony_list` (200 if one is already queued or running) |
| GET | `/dossiers/{dossier_id}` | The research run: ids, status and the expert's name |
| GET | `/dossiers/{dossier_id}/profile` | The live profile so far, with each cited finding's URL and title |
| GET | `/dossiers/{dossier_id}/findings` | The run's cited findings and gaps (409 while it runs) |
| GET | `/dossiers/{dossier_id}/expert-profile` | The structured profile for the results page (409 while it runs) |
| POST | `/dossiers/{dossier_id}/expert-profile/rebuild` | Runs the final profile call again for a finished run (502 if the model fails) |
| GET | `/dossiers/{dossier_id}/photo` | The expert's photo, if one was put in the run's files folder by hand (404 if none) |
| GET | `/dossiers/{dossier_id}/files` | The run's downloaded files and our notes on them (no bytes or text) |
