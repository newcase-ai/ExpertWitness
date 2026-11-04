"""The payment service (DESIGN 4B.9, Tool 13): decides whether a court filing that is not public on
CourtListener is worth buying from PACER. The agent only asks (pay_for_document in
app/tools/courtlistener.py); the price, title and availability come from CourtListener's record of the
filing, never from the agent, and the value judge never sees the agent's reason (gotcha 61).

By default a dry run (user, 2026-11-04): an approved purchase is recorded as "would buy" and nothing is
charged. With PAYMENTS_LIVE=true, an approved purchase gets a one-time Ramp virtual card
(app/sources/ramp.py) for its price, locked after 30 minutes; the card stays in memory (`run.cards`)
and close_cards terminates it when the run ends. Checkout is not built yet, so nothing is bought either
way. The agent never sees the card, only its last 4 digits (gotcha 64). Every decision is a trace event
(`purchase`), so the limits can be tuned from real runs."""

from app import llm, trace
from app.config import get_settings
from app.profile import known_so_far
from app.sources import SourceFailed, SourceSkipped, ramp

RUN_BUDGET_CENTS = 1_000  # per research run
AUTO_BUY_MAX_CENTS = 300  # approved only at or under this price
MIN_SCORE = 7  # value judge score (0-10) to approve
PAGE_CENTS, DOC_MAX_CENTS = 10, 300  # PACER: $0.10 a page, at most $3.00 a filing (transcripts excluded)
SPENDING = ("judging", "would_buy", "card_ready")  # purchase states counted against the run's budget
TITLE_SHOWN, PASSAGE_SHOWN, FILES_SHOWN, REASON_MAX = 200, 300, 20, 200

# Fixed texts (never source or model text)
FREE_NOTE = "this filing is public: download it with court_file"
BUSY_NOTE = "a decision on this filing is still being made"
DRY_RUN_NOTE = "purchases are off (dry run): the filing was not bought"
CARD_NOTE = (f"a one-time card was made for this filing, but checkout is not built yet: the filing was not "
             f"bought; the card locks in {ramp.LOCK_MINUTES} minutes and is closed when the run ends")
TASK = ("Research one expert witness for a lawsuit: their qualifications, the cases they worked on and their "
        "role, what they said, how courts ruled on their testimony, and anything opposing counsel could use.")
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "reason"],
    "properties": {"score": {"type": "integer"}, "reason": {"type": "string"}},
}
JUDGE_INSTRUCTIONS = """You decide whether one court filing is worth buying for an expert witness
research. You get the research task, what the research already has, and the filing offered (its
court record title and docket entry, case, pages and a search snippet) with its price. Score it 0-10:
10 = very likely to show this expert's own work or how a court ruled on it (a report, declaration,
deposition, CV, or a motion or order about their testimony), or the expert's own side in a case where
they are a party (accused of fraud or misconduct, disciplined, suing an employer), and not already
covered; 0 = not about the expert or already covered. reason: one short line in your own words. Judge
only from what you are given."""


class JudgeFailed(Exception):
    """The value judge gave no usable answer (fixed text)."""


def price_cents(doc: dict) -> int | None:
    """PACER's fee for one filing in cents: 10 a page, at most 300; 300 when the page count is unknown
    (usual for filings nobody bought). None for a transcript: PACER's cap does not apply to those."""
    if "transcript" in (doc.get("description") or "").lower():
        return None
    pages = doc.get("page_count")
    return min(pages * PAGE_CENTS, DOC_MAX_CENTS) if isinstance(pages, int) and pages > 0 else DOC_MAX_CENTS


def spent_cents(filings: dict) -> int:
    """What the run has reserved or (would have) bought."""
    return sum(d["purchase"]["cents"] for d in filings.values()
               if (d.get("purchase") or {}).get("status") in SPENDING)


def decision(approved: bool, reason: str, **extra) -> dict:
    return {"decision": "approved" if approved else "rejected", "reason": reason} | extra


async def judge(ctx, doc: dict, case: str, cents: int) -> tuple[int, str]:
    """The value judge: one small model call on the task, what the run has and the filing's record.
    Raises JudgeFailed."""
    have = [(d.get("description") or "")[:80] for d in ctx.run.filings.values() if d.get("file_id")]
    pages = doc.get("page_count") or "unknown"
    prompt = (f"The task: {TASK}\n\nWhat the research knows about the expert:\n{known_so_far(ctx)}\n\n"
              f"Court filings it already downloaded: {'; '.join(have[:FILES_SHOWN]) or 'none'}\n\n"
              f"The filing offered: {(doc.get('description') or 'no description')[:TITLE_SHOWN]}\n"
              f"Docket entry: {doc.get('entry') or 'none'}\nCase: {case}\nPages: {pages}\n"
              f"Search snippet: {(doc.get('passage') or 'none')[:PASSAGE_SHOWN]}\nPrice: ${cents / 100:.2f}")
    try:
        data = (await llm.call_model(JUDGE_INSTRUCTIONS, prompt, schema=JUDGE_SCHEMA)).data
        return max(0, min(10, int(data["score"]))), str(data.get("reason") or "")[:REASON_MAX]
    except (llm.ModelError, KeyError, TypeError, ValueError) as e:
        raise JudgeFailed("the value judge gave no usable answer") from e


def logged(ctx, doc: dict, case: str, reply: dict, **extra) -> dict:
    """Every decision: a `purchase` trace event (the ledger in v1) and the activity detail."""
    trace.record("purchase", document_id=doc["id"], case=case, title=(doc.get("description") or "")[:TITLE_SHOWN],
                 **reply, **extra)
    score = f", score {reply['score']}" if "score" in reply else ""
    if reply["reason"] == "card_ready":
        ctx.log(f"card ready {reply['cents']}c, ends {reply['card_last4']}{score}")
    else:
        ctx.log(f"would buy {reply['cents']}c{score}" if reply["decision"] == "approved"
                else f"rejected: {reply['reason']}{score}")
    return reply


async def new_card(ctx, doc: dict, cents: int) -> ramp.Card:
    """A Ramp card for this filing's price. The idempotency key is the job and filing, so asking again
    after a lost answer gives back the same card. No filing title is sent to Ramp (it may name people)."""
    return await ramp.create_card(cents, f"ExpertWitness filing {doc['id']}", f"{ctx.job_id}-{doc['id']}")


async def close_cards(ctx) -> None:
    """Terminate every card this run made (cannot be undone). Never raises: a card that cannot be closed
    still locks itself at its lock date."""
    for document_id, card in list(ctx.run.cards.items()):
        try:
            await ramp.terminate(card.fund_id)
            status = "card_closed"
        except Exception:  # noqa: BLE001 - the run's end must not fail on Ramp; the lock date is the backstop
            status = "card_close_failed"
        trace.record("purchase", document_id=document_id, status=status, card_id=card.id, card_last4=card.last4)
        del ctx.run.cards[document_id]


async def request(ctx, doc: dict, case: str) -> dict:
    """The decision on one filing the agent asked for. `doc` is the filing's entry in ctx.run.filings
    (CourtListener's record); its `purchase` holds this run's decision. The check-and-reserve has no
    await in between, so parallel tool calls can neither overspend nor ask twice for one filing."""
    if doc["available"]:
        return logged(ctx, doc, case, decision(False, "free_copy_found", note=FREE_NOTE))
    if old := doc.get("purchase"):
        reply = old.get("reply") or decision(False, "already_requested", note=BUSY_NOTE)
        ctx.log(f"already requested: {reply['reason']}")
        return reply | {"already_requested": True}
    cents = price_cents(doc)
    if cents is None or cents > AUTO_BUY_MAX_CENTS:
        return logged(ctx, doc, case, decision(False, "over_price", cents=cents))
    if spent_cents(ctx.run.filings) + cents > RUN_BUDGET_CENTS:
        return logged(ctx, doc, case, decision(False, "over_budget", cents=cents,
                                               left_cents=RUN_BUDGET_CENTS - spent_cents(ctx.run.filings)))
    doc["purchase"] = {"status": "judging", "cents": cents}
    try:
        score, reason = await judge(ctx, doc, case, cents)
        approved = score >= MIN_SCORE
        card = await new_card(ctx, doc, cents) if approved and get_settings().payments_live else None
    except JudgeFailed:
        doc.pop("purchase", None)  # no decision: the agent may ask again
        return logged(ctx, doc, case, decision(False, "judge_failed", cents=cents))
    except (SourceFailed, SourceSkipped) as e:  # no card: nothing is reserved, the agent may ask again
        doc.pop("purchase", None)
        return logged(ctx, doc, case, decision(False, "payment_failed", cents=cents, note=str(e)))
    except BaseException:  # cancelled or failed: nothing stays reserved
        doc.pop("purchase", None)
        raise
    if card:
        ctx.run.cards[doc["id"]] = card
        reply = decision(True, "card_ready", cents=cents, score=score, judge=reason, card_last4=card.last4,
                         note=CARD_NOTE)
        doc["purchase"] = {"status": "card_ready", "cents": cents, "reply": reply}
        return logged(ctx, doc, case, reply, card_id=card.id)
    reply = decision(approved, "dry_run" if approved else "low_value", cents=cents, score=score, judge=reason,
                     **({"note": DRY_RUN_NOTE} if approved else {}))
    doc["purchase"] = {"status": "would_buy" if approved else "rejected", "cents": cents, "reply": reply}
    return logged(ctx, doc, case, reply)
