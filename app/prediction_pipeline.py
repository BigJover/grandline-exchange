"""
Prediction generation pipeline — free-tier (rule-based, no LLM).

generate_chapter_predictions(db, chapter_num, force=False)
    - Fires ~24h after chapter drop (scheduler calls it Saturday UTC)
    - Uses wiki character appearance data already stored in ProposedPriceChange
    - Creates live Propositions (status="open") for simple appearance/price questions
    - Creates draft Propositions (status="draft") from the standalone PREDICTION_TEMPLATES pool
    - Is idempotent — checks BotKV "last_prediction_chapter" before running

auto_resolve_predictions(db, chapter_num, wiki_chars)
    - Called from detect_chapter_drop() when a new chapter is found
    - Resolves open propositions with source_chapter == chapter_num
    - character_appearance questions resolved via wiki_chars dict
    - character_price questions resolved via ProposedPriceChange direction
    - Unclear ones → status="needs_review" with reasoning stored in llm_reasoning

Status flow:
    draft     → open (admin publishes)
    open      → closed (auto or admin)
    closed    → resolved (auto-resolve) or needs_review (admin manual)
    needs_review → resolved (admin picks correct option)
"""

import re
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app import models


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_thursday(extra_weeks: int = 0) -> datetime:
    """Next Thursday at 18:00 UTC. extra_weeks=1 for break week (two weeks out)."""
    now = datetime.now(timezone.utc)
    days_ahead = (3 - now.weekday()) % 7   # 3 = Thursday
    if days_ahead == 0:
        days_ahead = 7
    days_ahead += extra_weeks * 7
    target = now + timedelta(days=days_ahead)
    return target.replace(hour=18, minute=0, second=0, microsecond=0)


def _already_ran(db: Session, chapter_num: int) -> bool:
    kv = db.query(models.BotKV).filter(models.BotKV.key == "last_prediction_chapter").first()
    return kv is not None and kv.value == str(chapter_num)


def _mark_ran(db: Session, chapter_num: int):
    kv = db.query(models.BotKV).filter(models.BotKV.key == "last_prediction_chapter").first()
    if kv:
        kv.value = str(chapter_num)
    else:
        db.add(models.BotKV(key="last_prediction_chapter", value=str(chapter_num)))


def _exists(db: Session, question: str) -> bool:
    """True if a proposition with this exact question already exists (any non-deleted status)."""
    return db.query(models.Proposition).filter(
        models.Proposition.question == question,
    ).first() is not None


def _create_prop(
    db: Session,
    question: str,
    category: str,
    options: list,
    status: str,
    source_chapter: int,
    closes_at: Optional[datetime] = None,
    is_chapter_prediction: bool = True,
    is_break_week: bool = False,
) -> models.Proposition:
    prop = models.Proposition(
        question=question,
        category=category,
        options=options,
        status=status,
        house_cut=0.05,
        closes_at=closes_at,   # None = no close date for non-chapter predictions
        is_chapter_prediction=is_chapter_prediction,
        source_chapter=source_chapter,
        is_break_week=is_break_week,
    )
    db.add(prop)
    return prop


# ── Data gathering ────────────────────────────────────────────────────────────

def _wiki_chars_for_chapter(db: Session, chapter_num: int) -> list:
    """
    Return characters that appeared in chapter_num according to already-stored
    ProposedPriceChange data (those rows carry wiki_count > 0).
    """
    rows = (
        db.query(models.ProposedPriceChange)
        .filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
            models.ProposedPriceChange.reason.like("%wiki appearances%"),
        )
        .order_by(models.ProposedPriceChange.pct_change.desc())
        .limit(8)
        .all()
    )
    return [r.character_name for r in rows]


def _top_movers(db: Session, chapter_num: int, n: int = 3) -> list:
    """Top N upward price movers for a chapter."""
    return (
        db.query(models.ProposedPriceChange)
        .filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
            models.ProposedPriceChange.direction == "up",
        )
        .order_by(models.ProposedPriceChange.pct_change.desc())
        .limit(n)
        .all()
    )


# ── Generation ────────────────────────────────────────────────────────────────

def generate_chapter_predictions(db: Session, chapter_num: int, force: bool = False) -> dict:
    """
    Generate predictions for what will happen in Ch.(chapter_num + 1).

    source_chapter is set to chapter_num+1 so the auto-resolve hook can find
    these propositions when that next chapter drops.

    Reads Chapter.next_is_break to determine if the following week is a break:
    - Chapter predictions get closes_at two weeks out and is_break_week=True
    - Non-chapter (standalone) drafts get no closes_at — open until resolved

    Returns {"created": int, "drafts": int, "skipped": bool, "chapter": int}
    """
    if not force and _already_ran(db, chapter_num):
        print(f"[PredictionPipeline] Already ran for Ch.{chapter_num} — skipping")
        return {"created": 0, "drafts": 0, "skipped": True, "chapter": chapter_num}

    # Check if the week after this chapter is a break
    chapter_row = db.query(models.Chapter).filter(models.Chapter.number == chapter_num).first()
    is_break = bool(chapter_row and chapter_row.next_is_break)
    extra_weeks = 1 if is_break else 0

    next_ch = chapter_num + 1
    closes = _next_thursday(extra_weeks=extra_weeks)
    created = 0
    drafts = 0

    if is_break:
        print(f"[PredictionPipeline] Ch.{chapter_num}: break week — predictions close {closes.date()}, is_break_week=True")

    # ── 1. Character appearance predictions ──────────────────────────────────
    # "Will {char} appear in Ch.{next_ch}?" — resolves when next_ch drops
    wiki_chars = _wiki_chars_for_chapter(db, chapter_num)
    for char_name in wiki_chars[:5]:
        q = f"Will {char_name} appear in Ch.{next_ch}?"
        if _exists(db, q):
            continue
        _create_prop(db, q, "chapter", ["Yes", "No"], "open", next_ch, closes, is_break_week=is_break)
        created += 1

    # ── 2. Price movement predictions ────────────────────────────────────────
    # "Will {char}'s beri value increase after Ch.{next_ch}?"
    movers = _top_movers(db, chapter_num, n=3)
    for row in movers:
        char_name = row.character_name
        q = f"Will {char_name}'s beri value increase after Ch.{next_ch}?"
        if _exists(db, q):
            continue
        _create_prop(db, q, "chapter", ["Yes", "No"], "open", next_ch, closes, is_break_week=is_break)
        created += 1

    # ── 3. Standalone template drafts ────────────────────────────────────────
    # Not chapter-specific — no close date, stay open until manually resolved
    try:
        from vegapunk.personality import get_all_standalone_templates
        pool = [t for t in get_all_standalone_templates() if "{" not in t]
        random.shuffle(pool)
        for template in pool:
            if drafts >= 3:
                break
            if _exists(db, template):
                continue
            _create_prop(
                db, template, "endgame", ["Yes", "No"], "draft",
                source_chapter=next_ch, closes_at=None, is_chapter_prediction=False,
            )
            drafts += 1
    except Exception as e:
        print(f"[PredictionPipeline] Draft template generation failed (non-fatal): {e}")

    db.commit()
    _mark_ran(db, chapter_num)

    print(f"[PredictionPipeline] Ch.{chapter_num} → {created} live, {drafts} drafts")

    if created > 0:
        try:
            from app.discord_notify import announce_prediction_open
            announce_prediction_open(
                question="",
                options=[],
                closes_at=closes,
                chapter_num=chapter_num,
                batch_count=created,
            )
        except Exception as e:
            print(f"[PredictionPipeline] Discord notify failed (non-fatal): {e}")

    return {"created": created, "drafts": drafts, "skipped": False, "chapter": chapter_num}


# ── Auto-resolve ──────────────────────────────────────────────────────────────

# Patterns for parsing auto-generated questions
_RE_APPEAR   = re.compile(r"^Will (.+?) appear in Ch\.(\d+)\?$", re.IGNORECASE)
_RE_BERI_UP  = re.compile(r"^Will (.+?)'s beri value increase after Ch\.(\d+)\?$", re.IGNORECASE)

_AUTO_RESOLVE_CONFIDENCE  = 0.90
_PRICE_RESOLVE_CONFIDENCE = 0.80


def auto_resolve_predictions(db: Session, chapter_num: int, wiki_chars: dict) -> dict:
    """
    Called when chapter_num is detected. Attempts to resolve all open/closed
    propositions targeting this chapter (source_chapter == chapter_num).

    wiki_chars: canonical_name → count dict from _wiki_chapter_chars()
    Returns {"resolved": int, "needs_review": int, "skipped": int}
    """
    from sqlalchemy import or_, and_
    props = (
        db.query(models.Proposition)
        .filter(
            models.Proposition.status.in_(["open", "closed"]),
            models.Proposition.is_chapter_prediction == True,  # never auto-resolve endgame props
            or_(
                models.Proposition.source_chapter == chapter_num,
                # Safety net: manually re-added chapter predictions created
                # before source_chapter tagging existed — match by question text
                and_(
                    models.Proposition.source_chapter == None,
                    models.Proposition.question.contains(f"Ch.{chapter_num}"),
                ),
            ),
        )
        .all()
    )

    if not props:
        return {"resolved": 0, "needs_review": 0, "skipped": 0}

    # Build upward mover set for this chapter
    up_chars = {
        r.character_name
        for r in db.query(models.ProposedPriceChange).filter(
            models.ProposedPriceChange.chapter_number == chapter_num,
            models.ProposedPriceChange.direction == "up",
        ).all()
    }

    resolved = needs_review = skipped = 0

    for prop in props:
        q = prop.question

        # ── Character appearance ──────────────────────────────────────────────
        m = _RE_APPEAR.match(q)
        if m:
            char_name = m.group(1)
            appeared = char_name in wiki_chars or any(
                char_name.lower() in k.lower() for k in wiki_chars
            )
            correct_option = 0 if appeared else 1   # 0=Yes, 1=No
            reasoning = (
                f"Ch.{chapter_num} wiki confirms {char_name} appearance."
                if appeared
                else f"Ch.{chapter_num} wiki — {char_name} not in character list."
            )
            _do_resolve(db, prop, correct_option, _AUTO_RESOLVE_CONFIDENCE, reasoning)
            resolved += 1
            continue

        # ── Price movement ────────────────────────────────────────────────────
        m = _RE_BERI_UP.match(q)
        if m:
            char_name = m.group(1)
            if wiki_chars:
                # We have wiki data — can make a reliable call
                went_up = char_name in up_chars
                correct_option = 0 if went_up else 1
                reasoning = (
                    f"Ch.{chapter_num}: {char_name} price proposal is upward."
                    if went_up
                    else f"Ch.{chapter_num}: {char_name} has no upward price proposal."
                )
                _do_resolve(db, prop, correct_option, _PRICE_RESOLVE_CONFIDENCE, reasoning)
                resolved += 1
            else:
                # No wiki data — can't auto-resolve price question reliably
                prop.status = "needs_review"
                prop.llm_confidence = 0.5
                prop.llm_reasoning = f"Ch.{chapter_num}: no wiki data available to auto-resolve price question."
                needs_review += 1
            continue

        # ── Unknown pattern — flag for manual review ──────────────────────────
        prop.status = "needs_review"
        prop.llm_confidence = 0.0
        prop.llm_reasoning = f"Ch.{chapter_num}: auto-resolve pattern not recognised — admin review needed."
        needs_review += 1

    db.commit()
    print(
        f"[PredictionPipeline] Auto-resolve Ch.{chapter_num}: "
        f"{resolved} resolved, {needs_review} needs review, {skipped} skipped"
    )
    return {"resolved": resolved, "needs_review": needs_review, "skipped": skipped}


def _do_resolve(
    db: Session,
    prop: models.Proposition,
    correct_option: int,
    confidence: float,
    reasoning: str,
):
    """
    Pay out winners for a proposition and mark it resolved.
    Delegates to app.resolution.resolve_proposition — the same payout math as
    admin casino_resolve, so all resolve paths stay consistent.
    """
    from app.resolution import resolve_proposition
    resolve_proposition(db, prop, correct_option,
                        llm_confidence=confidence, llm_reasoning=reasoning)
