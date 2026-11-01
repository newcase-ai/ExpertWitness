import os
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = Path(".env")
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_DATABASE_PATH = "data/expert_witness.db"
# Main agent (DESIGN 4B.5, 4B.11 D7): starting values, tuned in the live runs.
RESEARCH_SECONDS = 4 * 60 * 60  # the research job's time budget: 4 hours
MAX_AGENT_CALLS = 1_000  # model calls of the main agent (ADK max_llm_calls)
TOOL_CAP_SCALE = 10  # a research run may call each tool this many times its card's cap


@dataclass(frozen=True)
class Settings:
    model_name: str
    courtlistener_token: str | None
    contact_email: str | None
    database_path: str
    firecrawl_api_key: str | None = None
    gcs_bucket: str | None = None  # downloaded files and their descriptions go here
    google_cloud_project: str | None = None  # Vertex AI (Gemini)


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=value parser; blank lines and # comments are skipped."""
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def get_settings() -> Settings:
    """Read settings on every call: real environment wins over `.env`; empty means unset."""
    env = read_env_file(ENV_FILE) | os.environ

    def value(name: str) -> str | None:
        return (env.get(name) or "").strip() or None

    return Settings(
        model_name=value("MODEL_NAME") or DEFAULT_MODEL,
        courtlistener_token=value("COURTLISTENER_TOKEN"),
        contact_email=value("CONTACT_EMAIL"),
        database_path=value("DATABASE_PATH") or DEFAULT_DATABASE_PATH,
        firecrawl_api_key=value("FIRECRAWL_API_KEY"),
        gcs_bucket=value("GCS_BUCKET_NAME"),
        google_cloud_project=value("GOOGLE_CLOUD_PROJECT"),
    )
