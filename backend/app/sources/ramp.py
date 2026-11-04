"""Ramp virtual cards (Developer API v1): one card per purchase the payment service approves
(app/purchases.py), closed when the research run ends.

Verified 2026-11-04 against docs.ramp.com and the published spec (docs.ramp.com/openapi/developer-api.json):
- Token: `POST {api}/developer/v1/token`, Basic auth `client_id:client_secret`, form body
  `grant_type=client_credentials&scope=<space separated>` (no scope = a token that can do nothing).
  Answer `{"access_token", "expires_in"}` (seconds, about 10 days). Sent as `Authorization: Bearer`.
  https://docs.ramp.com/developer-api/v1/authorization
- Card: `POST {vault}/developer/v1/cards/vault` (the vault is its own host), header `X-Idempotency-Key`.
  Body `user_id`, `display_name`, `spending_restrictions` {`limit` {amount in cents, currency_code},
  `interval`, `transaction_amount_limit`, `lock_date`}, `allowed_overage_percent_override`. Answer 201
  `{"card": {"id", "pan", "cvv", "expiration" (MMYY)}, "spend_limit_id"}` right away (no polling).
  https://docs.ramp.com/developer-api/v1/virtual-cards
- Close: `DELETE {api}/developer/v1/funds/{spend_limit_id}`: at once, cannot be undone, closes every
  card on that fund.
- Hosts: sandbox `demo-api.ramp.com` / `demo-vault-api.ramp.com` (cannot move real money);
  production `api.ramp.com` / `vault-api.ramp.com`, only after Ramp's PCI review grants
  `cards:read_vault` (gotcha 62).
- Errors `{"error_v2": {...}}`; 200 requests per 10 s per IP; a request times out at 60 s.

Not confirmed in the docs: the `lock_date` format (ISO 8601 UTC is sent) and whether minute precision
is honored; the card is closed at the run's end either way. Ramp has no single-use flag: a TOTAL limit
of the price, the same per-charge limit, no overage and a lock date are the closest (gotcha 63).

Rules (gotcha 64): `pan`, `cvv` and `expiration` live only on the in-memory Card (`repr=False`), never
in a reply, trace, log or the database. Errors are fixed text: Ramp's error body is never passed on.
"""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app import http
from app.config import Settings, get_settings
from app.sources import SourceFailed, SourceSkipped

HOSTS = {  # env -> (api, vault)
    "sandbox": ("https://demo-api.ramp.com", "https://demo-vault-api.ramp.com"),
    "production": ("https://api.ramp.com", "https://vault-api.ramp.com"),
}
SCOPES = "limits:write cards:read_vault funds:write"  # make a card, read its number, close it
LOCK_MINUTES = 30  # the card stops working after this (user, 2026-11-04)
RENEW_SECONDS = 60  # get a new token this long before the old one runs out
TIMEOUT = httpx.Timeout(65.0, connect=5.0)  # Ramp gives up on a request after 60 s
NO_KEYS = "Ramp is not set up (RAMP_CLIENT_ID, RAMP_CLIENT_SECRET, RAMP_USER_ID)"

tokens: dict[tuple[str, str], tuple[str, float]] = {}  # (env, client_id) -> (token, monotonic expiry)


@dataclass(frozen=True)
class Card:
    """A card Ramp made. The number, CVV and expiry are never printed (gotcha 64)."""

    id: str
    fund_id: str  # Ramp's spend_limit_id: what `terminate` closes
    last4: str
    pan: str = field(repr=False)
    cvv: str = field(repr=False)
    expiration: str = field(repr=False)


def keys() -> Settings:
    settings = get_settings()
    if not (settings.ramp_client_id and settings.ramp_client_secret and settings.ramp_user_id):
        raise SourceSkipped(NO_KEYS)
    return settings


def body_of(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def failed(what: str, r: httpx.Response) -> SourceFailed:
    if r.status_code == 401:
        tokens.clear()  # a revoked or expired token: the next call signs in again
    again = r.status_code == 429 or r.status_code >= 500
    return SourceFailed(f"Ramp {what} failed (HTTP {r.status_code}); "
                        f"{'asking again later may help' if again else 'asking again will not help'}")


async def send(method: str, url: str, what: str, **kwargs) -> httpx.Response:
    if limiter := http.limiters.get("ramp"):
        await limiter.wait()
    try:
        return await http.get_client().request(method, url, timeout=TIMEOUT, **kwargs)
    except httpx.HTTPError as e:
        raise SourceFailed(f"Ramp {what}: no answer (network or timeout); asking again later may help") from e


async def token(settings: Settings) -> str:
    """A bearer token for these keys, cached until a minute before it runs out."""
    key = (settings.ramp_env, settings.ramp_client_id)
    if (hit := tokens.get(key)) and hit[1] > time.monotonic():
        return hit[0]
    api, _ = HOSTS[settings.ramp_env]
    r = await send("POST", f"{api}/developer/v1/token", "sign-in",
                   auth=(settings.ramp_client_id, settings.ramp_client_secret),
                   data={"grant_type": "client_credentials", "scope": SCOPES})
    body = body_of(r)
    if r.status_code != 200 or not isinstance(body.get("access_token"), str):
        raise failed("sign-in", r)
    seconds = body.get("expires_in") if isinstance(body.get("expires_in"), int) else 3600
    tokens[key] = (body["access_token"], time.monotonic() + max(0, seconds - RENEW_SECONDS))
    return body["access_token"]


async def create_card(cents: int, display_name: str, idempotency_key: str) -> Card:
    """A virtual card that can be charged `cents` once in total, locked after LOCK_MINUTES. The same
    `idempotency_key` asked again gives back the same card, not a second one.
    SourceSkipped: no keys. SourceFailed: Ramp refused or did not answer (fixed text)."""
    settings = keys()
    bearer = await token(settings)
    _, vault = HOSTS[settings.ramp_env]
    money = {"amount": cents, "currency_code": "USD"}
    lock = (datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
    body = {"user_id": settings.ramp_user_id, "display_name": display_name, "allowed_overage_percent_override": 0,
            "spending_restrictions": {"limit": money, "transaction_amount_limit": money, "interval": "TOTAL",
                                      "lock_date": lock}}
    r = await send("POST", f"{vault}/developer/v1/cards/vault", "new card", json=body,
                   headers={"Authorization": f"Bearer {bearer}", "X-Idempotency-Key": idempotency_key})
    answer = body_of(r)
    card = answer.get("card")
    if r.status_code not in (200, 201) or not isinstance(card, dict):
        raise failed("new card", r)
    try:
        pan = str(card["pan"])
        return Card(id=str(card["id"]), fund_id=str(answer["spend_limit_id"]), last4=pan[-4:], pan=pan,
                    cvv=str(card["cvv"]), expiration=str(card["expiration"]))
    except KeyError as e:  # ponytail: a card made without these fields is left to its lock date
        raise SourceFailed("Ramp new card: unexpected answer; asking again will not help") from e


async def terminate(fund_id: str) -> None:
    """Close a card for good (cannot be undone). A card Ramp no longer has (404) counts as closed."""
    settings = keys()
    bearer = await token(settings)
    api, _ = HOSTS[settings.ramp_env]
    r = await send("DELETE", f"{api}/developer/v1/funds/{quote(fund_id, safe='')}", "close card",
                   headers={"Authorization": f"Bearer {bearer}"})
    if r.status_code != 404 and not 200 <= r.status_code < 300:
        raise failed("close card", r)
