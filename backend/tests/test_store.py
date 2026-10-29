import asyncio
import os

import httpx
import pytest

from app import config, http, store
from app.models import Candidate, FailedSource

EVIDENCE = [{"source": "web", "url": "https://example.edu/x", "title": "Faculty page"}]


def make_candidate(name="Alex Rivera"):
    return Candidate(display_name=name, summary="Synthetic person.", evidence=EVIDENCE)


def test_creates_database_folder():
    store.init_db()
    assert os.path.isfile(os.environ["DATABASE_PATH"])


def test_candidates_hidden_until_done_and_ids_stable():
    job_id = store.create_job("candidate_search", [("openalex", "Publication records")])
    search_id = store.create_search("Alex Rivera", None, job_id)
    failed = [FailedSource(source="web_search", reason="timeout")]
    people = [make_candidate(), make_candidate("A. Rivera")]
    saved = store.save_candidates(search_id, people, failed)
    assert store.get_search(search_id).candidates == []  # job still queued
    store.finish_job(job_id, "done", result={"kind": "candidate_search", "id": search_id})
    search = store.get_search(search_id)
    ids = [c.candidate_id for c in search.candidates]
    assert ids == [c.candidate_id for c in saved]
    assert len(set(ids)) == 2 and all(i.startswith("cand_") for i in ids)
    assert store.get_candidate(ids[1]).display_name == "A. Rivera"
    assert search.failed_sources[0].source == "web_search"


def test_fail_interrupted_jobs():
    steps = [("a", "A"), ("b", "B"), ("c", "C")]
    running = store.create_job("candidate_search", steps)
    store.start_job(running)
    store.set_step(running, "a", "done", "ok")
    store.set_step(running, "b", "running")
    queued = store.create_job("candidate_search", steps)
    done = store.create_job("candidate_search", steps)
    store.finish_job(done, "done")

    assert store.fail_interrupted_jobs() == 2
    job = store.get_job(running)
    assert job.status == "failed" and job.error == "interrupted by restart"
    assert [s.status for s in job.steps] == ["done", "failed", "skipped"]
    assert job.progress.done == 3
    assert store.get_job(queued).status == "failed"
    assert store.get_job(done).status == "done"


def test_cache_and_call_log():
    assert store.cache_get("https://x.test/a") is None
    store.cache_put("https://x.test/a", b"body")
    assert store.cache_get("https://x.test/a") == b"body"
    store.record_call("courtlistener")
    store.record_call("courtlistener")
    assert store.calls_since("courtlistener", 3600) == 2
    assert store.calls_since("openalex", 3600) == 0


@pytest.mark.parametrize(
    "name",
    [
        "muse-spark-1.3-contributor",
        "MUSE-SPARK-1.3-CONTRIBUTOR",
        "Muse-Spark-Contributor-1.3 ",
        "muse-spark-contributor-1.3",
        "muse-spark-1.3-contributor\t",
    ],
)
def test_contributor_model_refused(monkeypatch, name):
    assert config.is_banned_model(name)
    monkeypatch.setenv("MODEL_NAME", name)
    with pytest.raises(ValueError):
        config.get_settings()


def test_model_name_trimmed(monkeypatch):
    monkeypatch.setenv("MODEL_NAME", " muse-spark-1.3 ")
    assert config.get_settings().model_name == "muse-spark-1.3"
    assert not config.is_banned_model("muse-spark-1.3")


def test_env_file_read_and_environment_wins(tmp_path, monkeypatch):
    env_file = tmp_path / "test.env"
    env_file.write_text("# comment\nCOURTLISTENER_TOKEN='abc'\nMODEL_API_KEY=from-file\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    settings = config.get_settings()
    assert settings.courtlistener_token == "abc"
    assert settings.model_api_key == "test-key"  # set in the real environment by conftest
    assert settings.model_name == "muse-spark-1.3"


def test_rate_limiter_spaces_calls():
    async def three_calls():
        limiter = http.RateLimiter(20)  # 50 ms apart
        start = asyncio.get_running_loop().time()
        await asyncio.gather(*(limiter.wait() for _ in range(3)))
        return asyncio.get_running_loop().time() - start

    assert asyncio.run(three_calls()) >= 0.09


def test_rate_limiter_timed_out_caller_gives_slot_back():
    async def run():
        limiter = http.RateLimiter(5)  # 200 ms apart
        await limiter.wait()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await limiter.wait()
        start = asyncio.get_running_loop().time()
        await limiter.wait()  # next free slot is 200 ms after the first call, not 400 ms
        return asyncio.get_running_loop().time() - start

    assert asyncio.run(run()) < 0.3


def test_fetch_sends_user_agent_and_caches_only_json(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "test@example.com")
    calls = []
    bodies = {"/a": (200, b'{"ok": true}'), "/junk": (200, b"<html>busy"), "/err": (500, b"{}")}

    def handler(request):
        calls.append(request)
        status, body = bodies[request.url.path]
        return httpx.Response(status, content=body)

    async def run():
        http.set_client(http.make_client(httpx.MockTransport(handler)))
        try:
            return [
                await http.fetch("openalex", f"https://x.test{path}", params={"q": "1"}, cache=True)
                for path in ("/a", "/a", "/junk", "/junk", "/err", "/err")
            ]
        finally:
            await http.close_client()

    responses = asyncio.run(run())
    assert [r.from_cache for r in responses] == [False, True, False, False, False, False]
    assert responses[1].json() == {"ok": True}
    assert len(calls) == 5  # only the JSON 200 was served from cache
    assert calls[0].headers["User-Agent"] == "expert-witness-api/0.1 (mailto:test@example.com)"


def test_fetch_cache_if_decides_what_is_cached():
    bodies = {
        "/null": b"null", "/list": b"[]", "/empty": b"{}",
        "/odd": b'{"x": 1}', "/ok": b'{"count": 1}',
    }
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=bodies[request.url.path])

    async def run():
        http.set_client(http.make_client(httpx.MockTransport(handler)))
        try:
            for path in ("/null", "/list", "/empty"):  # default: only a non-empty dict is cached
                for _ in range(2):
                    await http.fetch("openalex", f"https://x.test{path}", cache=True)
            for path in ("/odd", "/ok"):
                for _ in range(2):
                    await http.fetch(
                        "openalex", f"https://x.test{path}", cache=True,
                        cache_if=lambda data: "count" in data,
                    )
        finally:
            await http.close_client()

    asyncio.run(run())
    assert calls == ["/null", "/null", "/list", "/list", "/empty", "/empty", "/odd", "/odd", "/ok"]
