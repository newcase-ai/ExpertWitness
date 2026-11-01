import pytest

from app import config, llm, profile
from tests.fakes import FakeAdkModel

ENV_NAMES = ["MODEL_API_KEY", "MODEL_NAME", "COURTLISTENER_TOKEN", "CONTACT_EMAIL", "DATABASE_PATH", "FIRECRAWL_API_KEY"]


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test: no real `.env`, no real keys, a fresh SQLite file in tmp_path."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no.env")
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data" / "test.db"))
    monkeypatch.setenv("MODEL_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def no_auto_profile(monkeypatch):
    """Every test: no automatic profile update after tool calls (one more AI call each); the
    profile tests turn it on."""
    monkeypatch.setattr(profile, "AUTO_UPDATE", False)


@pytest.fixture(autouse=True)
def adk_model(monkeypatch):
    """Every test: ADK agents get a FakeAdkModel, never LiteLLM (gotcha 9). It replies "DONE" unless
    a test sets `adk_model.script` (turns: text, `call(...)`, an Exception, HANG)."""
    fake = FakeAdkModel()
    monkeypatch.setattr(llm, "fake_model", fake)
    return fake
