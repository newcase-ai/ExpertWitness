"""The payment service (app/purchases.py): PACER prices and the run's budget. The decisions through the
pay_for_document tool are in tests/test_tool_courtlistener.py. Synthetic filings only (gotcha 8)."""

import pytest

from app import purchases


@pytest.mark.parametrize(("pages", "description", "cents"), [
    (5, "Exhibit 8", 50),
    (30, "Exhibit 8", 300),
    (40, "Exhibit 8", 300),  # PACER caps a filing at $3.00
    (0, "Exhibit 8", 300),  # unknown page count: the cap
    (None, "Exhibit 8", 300),
    (5, "TRANSCRIPT of Hearing held 9-30-10", None),  # the cap does not apply to transcripts
])
def test_price_cents(pages, description, cents):
    assert purchases.price_cents({"page_count": pages, "description": description}) == cents


def test_spent_counts_reserved_and_would_buy_only():
    filings = {1: {"purchase": {"status": "judging", "cents": 50}}, 2: {"purchase": {"status": "would_buy", "cents": 300}},
               3: {"purchase": {"status": "rejected", "cents": 300}}, 4: {}}
    assert purchases.spent_cents(filings) == 350
