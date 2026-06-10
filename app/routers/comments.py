from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, auth
from app.main import limiter

router = APIRouter(tags=["comments"])


def current_week() -> str:
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


class CommentIn(BaseModel):
    body: str


@router.get("/characters/{char_id}/comments")
def get_comments(
    char_id: int,
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(auth.get_optional_user),
):
    week = current_week()
    comments = (
        db.query(models.Comment)
        .filter(models.Comment.character_id == char_id, models.Comment.week == week)
        .order_by(models.Comment.likes.desc(), models.Comment.created_at.asc())
        .limit(3)
        .all()
    )
    liked_ids = set()
    if user and comments:
        rows = db.query(models.CommentLike.comment_id).filter(
            models.CommentLike.user_id == user.id,
            models.CommentLike.comment_id.in_([c.id for c in comments]),
        ).all()
        liked_ids = {r.comment_id for r in rows}

    return [
        {
            "id": c.id,
            "username": c.username,
            "body": c.body,
            "likes": c.likes,
            "liked": c.id in liked_ids,
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@router.post("/characters/{char_id}/comments", status_code=201)
@limiter.limit("6/minute")
def post_comment(
    request: Request,
    char_id: int,
    payload: CommentIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    body = payload.body.strip()
    if not body:
        raise HTTPException(400, "Comment cannot be empty")
    if len(body) > 280:
        raise HTTPException(400, "Comment exceeds 280 characters")
    char = db.query(models.Character).filter(models.Character.id == char_id).first()
    if not char:
        raise HTTPException(404, "Character not found")
    comment = models.Comment(
        character_id=char_id,
        user_id=user.id,
        username=user.username,
        body=body,
        week=current_week(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return {
        "id": comment.id,
        "username": comment.username,
        "body": comment.body,
        "likes": 0,
        "liked": False,
        "created_at": comment.created_at.isoformat(),
    }


@router.get("/comments/top")
def top_comments(
    scope: str = "week",   # "week" | "all"
    limit: int = 30,
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(auth.get_optional_user),
):
    """Top liked comments across all characters. scope=week → current week only."""
    q = (
        db.query(models.Comment, models.Character.name)
        .join(models.Character, models.Comment.character_id == models.Character.id)
        .order_by(models.Comment.likes.desc(), models.Comment.created_at.desc())
    )
    if scope == "week":
        q = q.filter(models.Comment.week == current_week())
    results = q.limit(max(1, min(limit, 100))).all()

    liked_ids: set = set()
    if user and results:
        ids = [c.id for c, _ in results]
        rows = db.query(models.CommentLike.comment_id).filter(
            models.CommentLike.user_id == user.id,
            models.CommentLike.comment_id.in_(ids),
        ).all()
        liked_ids = {r.comment_id for r in rows}

    return [
        {
            "id": c.id,
            "username": c.username,
            "body": c.body,
            "likes": c.likes,
            "liked": c.id in liked_ids,
            "character_id": c.character_id,
            "character_name": char_name,
            "week": c.week,
            "created_at": c.created_at.isoformat(),
        }
        for c, char_name in results
    ]


@router.post("/comments/{comment_id}/like")
def toggle_like(
    comment_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(auth.get_current_user),
):
    comment = db.query(models.Comment).filter(models.Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(404, "Comment not found")
    existing = db.query(models.CommentLike).filter(
        models.CommentLike.user_id == user.id,
        models.CommentLike.comment_id == comment_id,
    ).first()
    if existing:
        db.delete(existing)
        comment.likes = max(0, comment.likes - 1)
        liked = False
    else:
        db.add(models.CommentLike(user_id=user.id, comment_id=comment_id))
        comment.likes += 1
        liked = True
    db.commit()
    return {"likes": comment.likes, "liked": liked}
