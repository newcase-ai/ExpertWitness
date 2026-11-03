"""One-time virtual card API (payments tools). Not part of the hackathon: CARD_API_URL unset or
"false" switches the card tool off. Assumed shape until the real API is known:
POST {amount_usd, memo} -> {id, number, exp, cvc}."""

import httpx

from app import http
from app.config import get_settings
from app.sources import SourceFailed, SourceSkipped

FIELDS = ("id", "number", "exp", "cvc")


async def issue(amount_usd: float, memo: str) -> dict:
    """A new one-time card for `amount_usd`. Raises SourceSkipped without CARD_API_URL, SourceFailed
    (fixed text) when the API fails or replies without a card."""
    url = get_settings().card_api_url
    if not url:
        raise SourceSkipped("CARD_API_URL is not set")
    try:
        r = await http.get_client().post(url, json={"amount_usd": amount_usd, "memo": memo})
    except httpx.HTTPError as e:
        raise SourceFailed(f"card API connection failed ({type(e).__name__}); calling again may help") from None
    if not r.is_success:
        raise SourceFailed(f"card API HTTP {r.status_code}; calling again will not help")
    try:
        card = r.json()
    except ValueError:
        card = None
    if not isinstance(card, dict) or not all(card.get(k) for k in FIELDS):
        raise SourceFailed("card API reply has no card; calling again will not help")
    return {k: str(card[k]) for k in FIELDS}
