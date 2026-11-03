"""Payment tools: budget, evaluator (helper agent faked) and the one-time card (card API faked over
HTTP). Synthetic people and a test card number only."""

import asyncio

import pytest

from app import http, llm, store
from app.research_agent import first_message
from app.tools import REGISTRY, UNKNOWN_TOOL, Ctx
from app.tools.payments import BAD_AMOUNT, UNKNOWN_APPROVAL, USED_APPROVAL
from tests.fakes import EXPERT, router, run_tool, tool_ctx_fixture  # noqa: F401 - fixtures

CARD_HOST = "cards.example.test"
CARD = {"id": "card_test_1", "number": "4242424242424242", "exp": "12/30", "cvc": "123"}


@pytest.fixture(name="evaluator")
def evaluator_fixture(monkeypatch):
    """set(text): the evaluator helper agent replies `text`. Returns the (instructions, prompt, tools) calls."""
    calls = []

    def setup(text):
        async def fake(self, instructions, prompt, tools=(), max_llm_calls=6, role="helper"):
            calls.append((instructions, prompt, tools, role))
            return llm.AgentRun(text=text)

        monkeypatch.setattr(Ctx, "sub_agent", fake)
        return calls

    return setup


@pytest.fixture(name="card_api")
def card_api_fixture(monkeypatch):
    """A fake card API at CARD_API_URL; set(status, body). Returns the seen requests."""
    monkeypatch.setenv("CARD_API_URL", f"https://{CARD_HOST}/cards")

    def setup(status=200, body=CARD):
        transport, seen = router({CARD_HOST: (status, body)})
        http.set_client(http.make_client(transport))
        return seen

    yield setup
    http.set_client(None)


def budget(ctx):
    return asyncio.run(run_tool(ctx, "payment_budget"))


def approve(ctx, evaluator, amount=12.0):
    evaluator("APPROVE a deposition transcript from a court site.")
    return asyncio.run(run_tool(ctx, "evaluate_payment", reason="Deposition transcript, Doe v. Example Co.",
                                amount_usd=amount))


def test_budget_default_and_env(tool_ctx, monkeypatch):
    assert budget(tool_ctx) == {"budget_usd": 25.0, "spent_usd": 0, "left_usd": 25.0}
    monkeypatch.setenv("PAYMENT_BUDGET_USD", "10")
    assert budget(tool_ctx)["budget_usd"] == 10.0


def test_approval_reserves_money_and_gives_context(tool_ctx, evaluator):
    reply = approve(tool_ctx, evaluator)
    assert reply == {"approved": True, "approval_id": "P1", "amount_usd": 12.0,
                     "why": "a deposition transcript from a court site."}
    assert budget(tool_ctx) == {"budget_usd": 25.0, "spent_usd": 12.0, "left_usd": 13.0}
    calls = evaluator("APPROVE ok")
    asyncio.run(run_tool(tool_ctx, "evaluate_payment", reason="Licence check", amount_usd=5))
    _, prompt, tools, role = calls[-1]
    assert (tools, role) == (["read_profile"], "evaluator")
    assert EXPERT.display_name in prompt and "Reason: Licence check" in prompt
    assert "$12.00: Deposition transcript, Doe v. Example Co." in prompt  # earlier approvals
    details = [(a.tool, a.detail) for a in store.activity(tool_ctx.job_id) if a.tool == "evaluate_payment"]
    assert details == [("evaluate_payment", "approved $12.00"), ("evaluate_payment", "approved $5.00")]


@pytest.mark.parametrize("text", ["REFUSE a people-search site.", "Maybe, it looks fine.", ""])
def test_anything_but_approve_is_a_refusal(tool_ctx, evaluator, text):
    evaluator(text)
    reply = asyncio.run(run_tool(tool_ctx, "evaluate_payment", reason="Background report", amount_usd=3))
    assert reply["approved"] is False and "approval_id" not in reply
    assert tool_ctx.run.payments == {}


@pytest.mark.parametrize("amount, error", [(0, BAD_AMOUNT), (-1, BAD_AMOUNT), (25.01, "over budget: $25.00 left")])
def test_bad_or_over_budget_amount_refused_without_the_evaluator(tool_ctx, evaluator, amount, error):
    calls = evaluator("APPROVE")
    reply = asyncio.run(run_tool(tool_ctx, "evaluate_payment", reason="Transcript", amount_usd=amount))
    assert reply == {"error": error} and calls == []


def test_card_tool_off_without_card_api_url(tool_ctx, monkeypatch):
    for value in (None, "false", "FALSE", ""):
        if value is None:
            monkeypatch.delenv("CARD_API_URL", raising=False)
        else:
            monkeypatch.setenv("CARD_API_URL", value)
        assert not REGISTRY["get_virtual_card"].enabled()
        assert "get_virtual_card" not in first_message(EXPERT, None)
        assert asyncio.run(run_tool(tool_ctx, "get_virtual_card", approval_id="P1")) == {"error": UNKNOWN_TOOL}


def test_card_for_an_approval_once_without_the_full_number(tool_ctx, evaluator, card_api):
    seen = card_api()
    assert "get_virtual_card" in first_message(EXPERT, None)
    assert asyncio.run(run_tool(tool_ctx, "get_virtual_card", approval_id="P9")) == {"error": UNKNOWN_APPROVAL}
    approve(tool_ctx, evaluator)
    reply = asyncio.run(run_tool(tool_ctx, "get_virtual_card", approval_id="P1"))
    assert reply == {"card_id": "card_test_1", "last4": "4242", "exp": "12/30", "amount_usd": 12.0}
    assert len(seen) == 1 and seen[0].method == "POST"
    assert asyncio.run(run_tool(tool_ctx, "get_virtual_card", approval_id="P1")) == {"error": USED_APPROVAL}


def test_card_api_failure_keeps_the_approval_unused(tool_ctx, evaluator, card_api):
    card_api(status=500, body={"detail": "provider text"})
    approve(tool_ctx, evaluator)
    reply = asyncio.run(run_tool(tool_ctx, "get_virtual_card", approval_id="P1"))
    assert "card API HTTP 500" in reply["error"] and "provider text" not in reply["error"]
    assert tool_ctx.run.payments["P1"]["used"] is False
