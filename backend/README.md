# Expert Witness API

Give it an expert's name, get back a cited dossier: testimony history, credentials and discipline, and publications. Used to vet opposing experts, or our own before retention.

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
| GET | `/jobs/{job_id}` | Job status and steps |
| GET | `/jobs/{job_id}/events` | The run trace (poll with `after`) |
| POST | `/research` | 202: starts a research job for a candidate (200 if one is already running) |
| GET | `/dossiers/{dossier_id}` | The dossier |
| GET | `/dossiers/{dossier_id}/profile` | The run's expert profile so far |
| GET | `/dossiers/{dossier_id}/findings` | Cited findings and activity of a research run |
