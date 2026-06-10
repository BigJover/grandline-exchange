"""
Trivia system — weekly 3-question rotation (1 per category).

Payouts:
  - Correct: 20,000฿ × streak_multiplier
  - Wrong:   −20,000฿ (flat, no multiplier)

Streak multiplier (consecutive full-correct weeks):
  0 → 1.0×   1 → 1.1×   2 → 1.2×   3 → 1.3×   4 → 1.4×   5+ → 1.5×

Streak resets only when a full week passes without completing all 3 correctly.
"""
import random
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth

router = APIRouter(prefix="/trivia", tags=["trivia"])

TRIVIA_BASE_BERI = 20_000
TRIVIA_CATEGORIES = ["lore", "geography", "backstory"]

_STREAK_MULTIPLIERS = {0: 1.0, 1: 1.1, 2: 1.2, 3: 1.3, 4: 1.4}
_STREAK_MAX_MULTIPLIER = 1.5


def _current_week() -> str:
    """Return ISO week string for today, e.g. '2026-W18'."""
    iso = date.today().isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _streak_multiplier(streak: int) -> float:
    return _STREAK_MULTIPLIERS.get(streak, _STREAK_MAX_MULTIPLIER)


def _prev_week(week: str) -> str:
    """Return the ISO week string immediately before the given one."""
    year, w = week.split("-W")
    year, w = int(year), int(w)
    if w == 1:
        # Jump to last week of previous year
        prev_year_last = date(year - 1, 12, 28)
        iso = prev_year_last.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return f"{year}-W{w - 1:02d}"


def _get_week_questions(db: Session, week: str) -> List[models.TriviaQuestion]:
    """Return the 3 questions assigned to this week (1 per category).
    Assigns questions lazily if the week hasn't been set up yet."""
    assigned = (
        db.query(models.TriviaQuestion)
        .filter(models.TriviaQuestion.week_assigned == week, models.TriviaQuestion.active == True)
        .all()
    )

    assigned_cats = {q.category for q in assigned}
    missing = [c for c in TRIVIA_CATEGORIES if c not in assigned_cats]

    if missing:
        # Lazily assign questions for categories not yet covered this week
        for cat in missing:
            candidate = (
                db.query(models.TriviaQuestion)
                .filter(
                    models.TriviaQuestion.category == cat,
                    models.TriviaQuestion.active == True,
                    models.TriviaQuestion.week_assigned == None,
                )
                .order_by(models.TriviaQuestion.id)
                .all()
            )
            if not candidate:
                # All questions in this category have been used — recycle by clearing oldest assignments
                oldest = (
                    db.query(models.TriviaQuestion)
                    .filter(
                        models.TriviaQuestion.category == cat,
                        models.TriviaQuestion.active == True,
                    )
                    .order_by(models.TriviaQuestion.week_assigned)
                    .all()
                )
                candidate = oldest

            if candidate:
                chosen = random.choice(candidate)
                chosen.week_assigned = week
                db.commit()
                db.refresh(chosen)
                assigned.append(chosen)

    return assigned


@router.get("/week", response_model=List[schemas.TriviaQuestionOut])
def get_week_questions(
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(auth.get_optional_user),
):
    """Return this week's 3 questions with shuffled choices. Correct answer is NOT revealed."""
    week = _current_week()
    questions = _get_week_questions(db, week)

    result = []
    for q in questions:
        choices = [q.correct_answer] + list(q.wrong_answers or [])
        random.shuffle(choices)

        already_answered = False
        answered_correctly: Optional[bool] = None

        if current_user:
            ans = (
                db.query(models.TriviaAnswer)
                .filter(
                    models.TriviaAnswer.user_id == current_user.id,
                    models.TriviaAnswer.question_id == q.id,
                    models.TriviaAnswer.week == week,
                )
                .first()
            )
            if ans:
                already_answered = True
                answered_correctly = ans.is_correct

        result.append(schemas.TriviaQuestionOut(
            id=q.id,
            category=q.category,
            question=q.question,
            choices=choices,
            week=week,
            already_answered=already_answered,
            answered_correctly=answered_correctly,
        ))

    return result


@router.post("/answer", response_model=schemas.TriviaAnswerOut)
def submit_answer(
    req: schemas.TriviaAnswerRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Submit an answer to a trivia question. Exactly one attempt per question per week."""
    week = _current_week()

    question = db.query(models.TriviaQuestion).filter(
        models.TriviaQuestion.id == req.question_id,
        models.TriviaQuestion.week_assigned == week,
        models.TriviaQuestion.active == True,
    ).first()
    if not question:
        raise HTTPException(404, "Question not found or not active this week")

    existing = db.query(models.TriviaAnswer).filter(
        models.TriviaAnswer.user_id == current_user.id,
        models.TriviaAnswer.question_id == req.question_id,
        models.TriviaAnswer.week == week,
    ).first()
    if existing:
        raise HTTPException(400, "You have already answered this question this week")

    is_correct = req.answer.strip() == question.correct_answer.strip()
    multiplier = _streak_multiplier(current_user.trivia_streak or 0)

    if is_correct:
        beri_change = round(TRIVIA_BASE_BERI * multiplier)
    else:
        # Loss is capped at the user's balance — can't go negative
        beri_change = -round(min(TRIVIA_BASE_BERI, max(0, current_user.beri_balance)))

    current_user.beri_balance += beri_change
    db.add(models.BeriEvent(
        user_id=current_user.id,
        event_type="trivia_correct" if is_correct else "trivia_wrong",
        amount=beri_change,
        description=(
            f"Trivia {'win' if is_correct else 'loss'} — {question.category} — "
            f"{'+'if beri_change > 0 else ''}{int(beri_change):,}฿"
            + (f" (streak {current_user.trivia_streak or 0}× {multiplier})" if is_correct and multiplier > 1.0 else "")
        ),
    ))

    db.add(models.TriviaAnswer(
        user_id=current_user.id,
        question_id=question.id,
        week=week,
        is_correct=is_correct,
        beri_change=beri_change,
    ))
    db.commit()
    db.refresh(current_user)

    # Check if this completes the week (all 3 questions answered correctly)
    week_answers = (
        db.query(models.TriviaAnswer)
        .join(models.TriviaQuestion, models.TriviaAnswer.question_id == models.TriviaQuestion.id)
        .filter(
            models.TriviaAnswer.user_id == current_user.id,
            models.TriviaAnswer.week == week,
            models.TriviaAnswer.is_correct == True,
            models.TriviaQuestion.week_assigned == week,
        )
        .count()
    )

    week_questions_total = (
        db.query(models.TriviaQuestion)
        .filter(models.TriviaQuestion.week_assigned == week, models.TriviaQuestion.active == True)
        .count()
    )

    week_complete = week_answers >= week_questions_total and week_questions_total == 3

    if week_complete:
        # Check if this is consecutive with last completed week
        prev = _prev_week(week)
        if current_user.trivia_last_week == prev:
            current_user.trivia_streak = (current_user.trivia_streak or 0) + 1
        else:
            current_user.trivia_streak = 1  # first completion or gap — start at 1
        current_user.trivia_last_week = week
        db.commit()
        db.refresh(current_user)

    return schemas.TriviaAnswerOut(
        correct=is_correct,
        beri_change=beri_change,
        new_balance=current_user.beri_balance,
        streak=current_user.trivia_streak or 0,
        multiplier=multiplier,
        week_complete=week_complete,
    )


@router.get("/streak", response_model=schemas.TriviaStreakOut)
def get_streak(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Return the user's current trivia streak and this week's progress."""
    week = _current_week()

    week_answers = (
        db.query(models.TriviaAnswer)
        .join(models.TriviaQuestion, models.TriviaAnswer.question_id == models.TriviaQuestion.id)
        .filter(
            models.TriviaAnswer.user_id == current_user.id,
            models.TriviaAnswer.week == week,
            models.TriviaQuestion.week_assigned == week,
        )
        .all()
    )

    streak = current_user.trivia_streak or 0
    return schemas.TriviaStreakOut(
        streak=streak,
        multiplier=_streak_multiplier(streak),
        last_week=current_user.trivia_last_week,
        this_week_answers=len(week_answers),
        this_week_correct=sum(1 for a in week_answers if a.is_correct),
    )
