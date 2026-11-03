"""Payments (hackathon): the budget, an evaluator helper agent that approves or refuses one purchase,
and a one-time virtual card for an approved purchase (only when CARD_API_URL is set). Approvals live
in the run's memory (`Run.payments`); approved money counts as spent."""

from app.config import get_settings
from app.evidence import refuse
from app.sources import card
from app.tools import Ctx, tool

BAD_AMOUNT = "the amount must be more than 0"
OVER_BUDGET = "over budget: ${:.2f} left"
UNKNOWN_APPROVAL = "unknown approval_id: call evaluate_payment first"
USED_APPROVAL = "this approval was already used for a card"
EVALUATOR_CALLS = 4
REASON_MAX = 500

EVALUATOR = """You approve or refuse one purchase for a legal research tool researching one expert
witness. Approve only if it buys professional records about this expert (a court record or filing, a
deposition or trial transcript, an expert report, a licence or certification check, a published
article) and is likely to add something new to what we know (read_profile shows what we have).
Refuse: a background check, consumer report or people-search site; anything about private life,
family, home address or health; LinkedIn, Google Scholar, Daubert Tracker, TrialSmith, CPAverify,
BrokerCheck, ABMS or DocInfo; anything that needs a login or account made to get around a site's
rules; a vague reason; a price that looks too high for what it buys. Your reply's first word is
APPROVE or REFUSE, then one sentence why."""


def spent(ctx: Ctx) -> float:
    return sum(p["amount_usd"] for p in ctx.run.payments.values())


def left(ctx: Ctx) -> float:
    return round(get_settings().payment_budget_usd - spent(ctx), 2)


@tool(label="Payment budget", cap=10)
async def payment_budget(ctx: Ctx) -> dict:
    """The money this research run may spend on paid records, in US dollars: the budget, what
    approved purchases already took, and what is left. Takes no arguments."""
    ctx.log(f"${left(ctx):.2f} left")
    return {"budget_usd": get_settings().payment_budget_usd, "spent_usd": round(spent(ctx), 2),
            "left_usd": left(ctx)}


@tool(label="Evaluate a payment", cap=5)
async def evaluate_payment(ctx: Ctx, reason: str, amount_usd: float) -> dict:
    """Ask for approval before paying for anything. `reason`: what you want to buy, from which site,
    and what it should add about the expert. `amount_usd`: the price in US dollars. An approval
    reserves the money and gives an approval_id for get_virtual_card."""
    if amount_usd <= 0:
        return refuse(BAD_AMOUNT)
    if amount_usd > left(ctx):
        return refuse(OVER_BUDGET.format(left(ctx)))
    reason = " ".join(reason.split())[:REASON_MAX]
    earlier = [f"- ${p['amount_usd']:.2f}: {p['reason']}" for p in ctx.run.payments.values()] or ["- none"]
    expert = ctx.expert
    prompt = "\n".join([
        f"Expert: {expert.display_name}", f"Field: {expert.field or 'unknown'}", "",
        f"Purchase: ${amount_usd:.2f}", f"Reason: {reason}", "",
        f"Budget: ${get_settings().payment_budget_usd:.2f}, left: ${left(ctx):.2f}",
        "Earlier approved purchases:", *earlier])
    run = await ctx.sub_agent(EVALUATOR, prompt, tools=["read_profile"], max_llm_calls=EVALUATOR_CALLS,
                              role="evaluator")
    words = run.text.strip().split(maxsplit=1)
    why = words[1].strip() if len(words) > 1 else ""
    if not words or words[0].strip(".:,").upper() != "APPROVE":  # anything but APPROVE is a refusal
        ctx.log("refused")
        return {"approved": False, "why": why or f"no clear approval ({run.stopped or 'no reply'})"}
    approval_id = f"P{len(ctx.run.payments) + 1}"
    ctx.run.payments[approval_id] = {"amount_usd": amount_usd, "reason": reason, "used": False}
    ctx.log(f"approved ${amount_usd:.2f}")
    return {"approved": True, "approval_id": approval_id, "amount_usd": amount_usd, "why": why}


@tool(label="Get a one-time card", cap=5, enabled=lambda: get_settings().card_api_url is not None)
async def get_virtual_card(ctx: Ctx, approval_id: str) -> dict:
    """Get a one-time virtual card for one approved purchase (approval_id from evaluate_payment). Each
    approval gives one card, limited to the approved amount."""
    payment = ctx.run.payments.get(approval_id.strip())
    if payment is None:
        return refuse(UNKNOWN_APPROVAL)
    if payment["used"]:
        return refuse(USED_APPROVAL)
    issued = await card.issue(payment["amount_usd"], payment["reason"])
    payment["used"] = True
    ctx.log(f"card for ${payment['amount_usd']:.2f}")
    # ponytail: the full number and CVC never go into a reply (the model and the trace see replies);
    # hand them to a checkout tool, outside the model, once one exists.
    return {"card_id": issued["id"], "last4": issued["number"][-4:], "exp": issued["exp"],
            "amount_usd": payment["amount_usd"]}
