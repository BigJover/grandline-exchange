"""
Single source of truth for proposition payout math.

Used by:
  - admin casino_resolve            (manual resolve)
  - admin casino_accept_auto_resolve (resolve from needs_review)
  - prediction_pipeline auto-resolve (chapter drop hook)

Rules (the original admin casino_resolve math — zero-sum on real stakes):
  - total_pool counts real (non-free-play) stakes only — free play credit is
    house-funded and never inflates the pool
  - effective stake = amount × multiplier for chapter predictions; multipliers
    are folded into the winner-pool denominator so payouts never exceed the pool
  - winners split (total_pool − house_cut) proportionally by effective stake
  - free play payouts are capped at 2× the free play credit
  - sale bettors get back a share of the house cut (sale_discount fraction)
  - losing bets with penalty_amount lose extra, capped at their balance

Does NOT commit — callers own the transaction.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import models


def resolve_proposition(
    db: Session,
    prop: models.Proposition,
    correct_option: int,
    llm_confidence=None,
    llm_reasoning=None,
) -> dict:
    """Pay out winners, log BeriEvents for all bettors, mark prop resolved.
    Returns summary stats. Caller must db.commit()."""
    prop.correct_option = correct_option
    prop.status = "resolved"
    prop.resolved_at = datetime.now(timezone.utc)
    if llm_confidence is not None:
        prop.llm_confidence = llm_confidence
    if llm_reasoning is not None:
        prop.llm_reasoning = llm_reasoning

    bets = db.query(models.PropositionBet).filter(
        models.PropositionBet.proposition_id == prop.id
    ).all()

    # For chapter predictions, use effective_amount = amount × multiplier for pool math
    is_prediction = bool(prop.is_chapter_prediction)

    def _effective_amount(b: models.PropositionBet) -> float:
        if is_prediction and not b.is_free_play:
            return b.amount * (b.multiplier or 1.0)
        return b.amount

    total_pool = sum(b.amount for b in bets if not b.is_free_play)
    winner_pool = sum(
        _effective_amount(b)
        for b in bets
        if b.option_index == correct_option
    )
    house_take = round(total_pool * (prop.house_cut or 0.05), 2)
    prize_pool = total_pool - house_take

    winners, losers, total_paid = 0, 0, 0.0

    for bet in bets:
        user = db.query(models.User).filter(models.User.id == bet.user_id).first()
        if not user:
            continue

        multiplier = float(bet.multiplier or 1.0)

        if bet.option_index == correct_option and winner_pool > 0:
            eff = _effective_amount(bet)
            base_payout = round((eff / winner_pool) * prize_pool, 2)
            # Free play winnings are capped at 2× the free play credit to limit house exposure
            if bet.is_free_play:
                base_payout = min(base_payout, bet.amount * 2)

            # Sale bonus: house refunds a portion of its cut to sale bettors
            sale_discount = float(getattr(bet, 'sale_discount', 0.0) or 0.0)
            sale_bonus = 0.0
            if sale_discount > 0 and not bet.is_free_play and winner_pool > 0:
                sale_bonus = round((eff / winner_pool) * house_take * sale_discount, 2)

            payout = base_payout + sale_bonus
            bet.payout = payout
            user.beri_balance += payout
            total_paid += payout

            extra = f" {multiplier}×" if multiplier > 1.0 and not bet.is_free_play else ""
            free_tag = " [free play]" if bet.is_free_play else ""
            sale_tag = f" [{int(sale_discount*100)}% sale]" if sale_bonus > 0 else ""
            db.add(models.BeriEvent(
                user_id=user.id,
                event_type="casino_win",
                amount=payout,
                description=(
                    f"Prediction win{free_tag}{sale_tag} — \"{prop.question}\" → "
                    f"\"{prop.options[correct_option]}\"{extra} "
                    f"({payout:,.0f}฿ on {bet.amount:,.0f}฿ bet)"
                ),
            ))
            winners += 1
        else:
            bet.payout = 0.0
            penalty = float(bet.penalty_amount or 0)
            if penalty > 0 and not bet.is_free_play:
                actual_penalty = min(penalty, user.beri_balance)
                user.beri_balance -= actual_penalty
                db.add(models.BeriEvent(
                    user_id=user.id,
                    event_type="casino_penalty",
                    amount=-actual_penalty,
                    description=(
                        f"Late prediction penalty — \"{prop.question}\" → "
                        f"\"{prop.options[correct_option]}\" "
                        f"({actual_penalty:,.0f}฿ penalty)"
                    ),
                ))

            if not bet.is_free_play:
                db.add(models.BeriEvent(
                    user_id=user.id,
                    event_type="casino_loss",
                    amount=0,
                    description=(
                        f"Prediction loss — \"{prop.question}\" → "
                        f"\"{prop.options[correct_option]}\" "
                        f"({bet.amount:,.0f}฿ lost)"
                    ),
                ))
            losers += 1

    return {
        "total_pool": total_pool,
        "house_take": house_take,
        "prize_pool": prize_pool,
        "winners": winners,
        "losers": losers,
        "total_paid_out": total_paid,
    }
