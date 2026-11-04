"""Ramp virtual cards (app/sources/ramp.py) against fake Ramp answers: never the live API (gotcha 9).
Synthetic card only (tests/fakes.py)."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app import config
from app.sources import SourceFailed, SourceSkipped, ramp
from tests.fakes import RAMP_PAN, ramp_http_fixture, ramp_keys_fixture  # noqa: F401 - fixtures

ERROR = {"error_v2": {"error_code": "SYNTHETIC", "message": "SYNTHETIC-RAMP-ERROR"}}


def new_card(cents=250):
    return asyncio.run(ramp.create_card(cents, "ExpertWitness filing 1108", "job-1-1108"))


def test_create_card_signs_in_and_asks_for_a_one_time_limit(ramp_http):
    seen = ramp_http()
    card = new_card()
    assert (card.id, card.fund_id, card.last4, card.pan) == ("card-synthetic-1", "fund-synthetic-1", "1111", RAMP_PAN)
    sign_in, made = seen
    assert str(sign_in.url) == "https://demo-api.ramp.com/developer/v1/token" and sign_in.method == "POST"
    assert sign_in.headers["authorization"] == "Basic " + base64.b64encode(b"client-1:secret-1").decode()
    assert parse_qs(sign_in.content.decode()) == {"grant_type": ["client_credentials"], "scope": [ramp.SCOPES]}
    assert str(made.url) == "https://demo-vault-api.ramp.com/developer/v1/cards/vault"
    assert made.headers["authorization"] == "Bearer SYNTHETIC-RAMP-TOKEN"
    assert made.headers["x-idempotency-key"] == "job-1-1108"
    body = json.loads(made.content)
    money = {"amount": 250, "currency_code": "USD"}
    lock = datetime.fromisoformat(body["spending_restrictions"].pop("lock_date"))
    assert body == {"user_id": "user-1", "display_name": "ExpertWitness filing 1108",
                    "allowed_overage_percent_override": 0,
                    "spending_restrictions": {"limit": money, "transaction_amount_limit": money, "interval": "TOTAL"}}
    assert abs(lock - (datetime.now(UTC) + timedelta(minutes=30))) < timedelta(minutes=1)


def test_the_token_is_reused(ramp_http):
    seen = ramp_http()
    new_card()
    new_card()
    assert [r.url.path for r in seen] == ["/developer/v1/token", "/developer/v1/cards/vault",
                                          "/developer/v1/cards/vault"]


def test_production_uses_the_production_hosts(ramp_http, monkeypatch):
    monkeypatch.setenv("RAMP_ENV", "production")
    seen = ramp_http(env="production")
    new_card()
    assert [r.url.host for r in seen] == ["api.ramp.com", "vault-api.ramp.com"]


def test_bad_ramp_env_is_refused(monkeypatch):
    monkeypatch.setenv("RAMP_ENV", "live")
    with pytest.raises(ValueError, match="RAMP_ENV"):
        config.get_settings()


def test_card_number_is_never_printed(ramp_http):
    ramp_http()
    card = new_card()
    assert RAMP_PAN not in repr(card) and "SYNTHETIC-CVV" not in repr(card) and "1227" not in repr(card)


@pytest.mark.parametrize("status", [204, 200, 404])  # 404: Ramp no longer has it, so it is closed
def test_terminate_closes_the_fund(ramp_http, status):
    seen = ramp_http(close=(status, None))
    asyncio.run(ramp.terminate("fund-synthetic-1"))
    assert seen[-1].method == "DELETE"
    assert str(seen[-1].url) == "https://demo-api.ramp.com/developer/v1/funds/fund-synthetic-1"


@pytest.mark.parametrize(("routes", "message"), [
    ({"token": (401, ERROR)}, "Ramp sign-in failed (HTTP 401); asking again will not help"),
    ({"card": (500, ERROR)}, "Ramp new card failed (HTTP 500); asking again later may help"),
    ({"card": (429, ERROR)}, "Ramp new card failed (HTTP 429); asking again later may help"),
    ({"card": (201, {"card": {"id": "card-synthetic-1"}})}, "Ramp new card: unexpected answer; asking again will not help"),
    ({"card": httpx.ConnectError("SYNTHETIC-RAMP-ERROR")},
     "Ramp new card: no answer (network or timeout); asking again later may help"),
])
def test_failures_are_fixed_text(ramp_http, routes, message):
    ramp_http(**routes)
    with pytest.raises(SourceFailed) as failure:
        new_card()
    assert str(failure.value) == message  # never Ramp's own text


def test_a_refused_token_is_dropped(ramp_http):
    ramp_http(card=(401, ERROR))
    with pytest.raises(SourceFailed):
        new_card()
    assert ramp.tokens == {}


def test_close_failure_is_fixed_text(ramp_http):
    ramp_http(close=(500, ERROR))
    with pytest.raises(SourceFailed, match=r"^Ramp close card failed \(HTTP 500\)"):
        asyncio.run(ramp.terminate("fund-synthetic-1"))


def test_no_keys_skips_ramp():
    with pytest.raises(SourceSkipped, match="Ramp is not set up"):
        new_card()
