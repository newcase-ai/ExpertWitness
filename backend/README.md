# Expert Witness API

Give it an expert's name, get back a cited dossier: testimony history, credentials and discipline, and publications. Used to vet opposing experts, or our own before retention.

See [SOURCES_AND_FLOW.md](SOURCES_AND_FLOW.md) for the pipeline, data sources and open decisions.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Run locally

```sh
gcloud auth application-default login  # Gemini on Vertex AI; set GOOGLE_CLOUD_PROJECT in .env
uv sync
uv run fastapi dev app/main.py
```

The API runs at http://127.0.0.1:8000. Interactive docs are at http://127.0.0.1:8000/docs.

## Endpoints

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok"}` |
