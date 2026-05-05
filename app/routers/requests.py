import os
import json as _json
import urllib.request as _urllib
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, auth

router = APIRouter(tags=["requests"])


def _post_to_discord(req: "models.CharacterRequest") -> Optional[str]:
    """Post a character request to Discord via webhook.
    Returns Discord message ID on success, None if webhook not configured or failed.
    Gracefully no-ops when DISCORD_CHARACTER_REQUEST_WEBHOOK is not set."""
    webhook_url = os.getenv("DISCORD_CHARACTER_REQUEST_WEBHOOK", "").strip()
    if not webhook_url:
        return None

    beri_str = f"{int(req.proposed_beri):,}฿" if req.proposed_beri else "Not specified"

    payload = {
        "embeds": [{
            "title": f"📋 Character Request: {req.name}",
            "description": req.reason or "No reason provided.",
            "color": 0x6A0DAD,
            "fields": [
                {"name": "Faction",    "value": req.faction or "Unknown",  "inline": True},
                {"name": "Category",   "value": req.category or "Unknown", "inline": True},
                {"name": "Suggested",  "value": beri_str,                  "inline": True},
                {"name": "Requested by", "value": req.username,            "inline": True},
            ],
            "footer": {"text": "Grand Line Exchange · Character Request System"},
        }],
        "poll": {
            "question": {"text": f"Add {req.name} to the Exchange?"},
            "answers": [
                {"answer_id": 1, "poll_media": {"text": "Add them", "emoji": {"name": "✅"}}},
                {"answer_id": 2, "poll_media": {"text": "Not yet",  "emoji": {"name": "❌"}}},
            ],
            "allow_multiselect": False,
            "duration": 48,
        },
    }

    try:
        data = _json.dumps(payload).encode()
        http_req = _urllib.Request(
            webhook_url + "?wait=true",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib.urlopen(http_req, timeout=5) as resp:
            body = _json.loads(resp.read())
            return str(body.get("id"))
    except Exception as e:
        print(f"[Discord] Character request webhook failed: {e}")
        return None


# ── Schemas ──────────────────────────────────────────────────────────────────

class CharacterRequestIn(BaseModel):
    name: str
    aliases: str = ""
    faction: str = ""
    category: str = ""
    proposed_beri: float = 0
    canon_bounty: float = 0
    reason: str


class PriceRequestIn(BaseModel):
    character_name: str
    proposed_beri: float
    reason: str


# ── Public submission endpoints ───────────────────────────────────────────────

@router.post("/requests/character", status_code=201)
def submit_character_request(
    payload: CharacterRequestIn,
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(auth.get_optional_user),
):
    if not payload.name.strip():
        raise HTTPException(400, "Character name is required")
    if not payload.reason.strip():
        raise HTTPException(400, "Reason is required")

    req = models.CharacterRequest(
        user_id=user.id if user else None,
        username=user.username if user else "anonymous",
        name=payload.name.strip(),
        aliases=payload.aliases,
        faction=payload.faction,
        category=payload.category,
        proposed_beri=payload.proposed_beri,
        canon_bounty=payload.canon_bounty,
        reason=payload.reason.strip(),
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    discord_id = _post_to_discord(req)
    if discord_id:
        req.discord_message_id = discord_id
        db.commit()

    count = db.query(models.CharacterRequest).filter(
        models.CharacterRequest.name.ilike(payload.name.strip()),
        models.CharacterRequest.status == "pending",
    ).count()
    return {"status": "submitted", "name": payload.name.strip(), "total_requests": count}


@router.post("/requests/price", status_code=201)
def submit_price_request(
    payload: PriceRequestIn,
    db: Session = Depends(get_db),
    user: Optional[models.User] = Depends(auth.get_optional_user),
):
    if not payload.character_name.strip():
        raise HTTPException(400, "Character name is required")
    if not payload.reason.strip():
        raise HTTPException(400, "Reason is required")

    req = models.PriceRequest(
        user_id=user.id if user else None,
        username=user.username if user else "anonymous",
        character_name=payload.character_name.strip(),
        proposed_beri=payload.proposed_beri,
        reason=payload.reason.strip(),
    )
    db.add(req)
    db.commit()
    return {"status": "submitted", "character_name": payload.character_name.strip()}


# ── Admin-only endpoints ──────────────────────────────────────────────────────

@router.get("/admin/requests")
def list_requests(
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.get_admin_user),
):
    char_reqs = (
        db.query(models.CharacterRequest)
        .filter(models.CharacterRequest.status == "pending")
        .order_by(models.CharacterRequest.created_at.desc())
        .all()
    )
    price_reqs = (
        db.query(models.PriceRequest)
        .filter(models.PriceRequest.status == "pending")
        .order_by(models.PriceRequest.created_at.desc())
        .all()
    )
    return {
        "character_requests": [
            {
                "id": r.id,
                "username": r.username,
                "name": r.name,
                "aliases": r.aliases,
                "faction": r.faction,
                "category": r.category,
                "proposed_beri": r.proposed_beri,
                "canon_bounty": r.canon_bounty,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in char_reqs
        ],
        "price_requests": [
            {
                "id": r.id,
                "username": r.username,
                "character_name": r.character_name,
                "proposed_beri": r.proposed_beri,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in price_reqs
        ],
    }


@router.post("/admin/requests/character/{req_id}/approve")
def approve_character_request(
    req_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.get_admin_user),
):
    req = db.query(models.CharacterRequest).filter(models.CharacterRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    req.status = "approved"
    db.commit()
    return {"status": "approved", "name": req.name}


@router.post("/admin/requests/character/{req_id}/dismiss")
def dismiss_character_request(
    req_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.get_admin_user),
):
    req = db.query(models.CharacterRequest).filter(models.CharacterRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    req.status = "dismissed"
    db.commit()
    return {"status": "dismissed"}


@router.post("/admin/requests/price/{req_id}/approve")
def approve_price_request(
    req_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.get_admin_user),
):
    req = db.query(models.PriceRequest).filter(models.PriceRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    char = db.query(models.Character).filter(
        models.Character.name.ilike(req.character_name)
    ).first()
    if not char:
        raise HTTPException(404, f"Character '{req.character_name}' not found in database")
    old_beri = char.beri
    char.beri = req.proposed_beri
    req.status = "approved"
    db.commit()
    return {"status": "approved", "name": char.name, "old_beri": old_beri, "new_beri": char.beri}


@router.post("/admin/requests/price/{req_id}/dismiss")
def dismiss_price_request(
    req_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.get_admin_user),
):
    req = db.query(models.PriceRequest).filter(models.PriceRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    req.status = "dismissed"
    db.commit()
    return {"status": "dismissed"}


@router.post("/admin/promote")
def promote_user(
    username: str,
    secret: str,
    db: Session = Depends(get_db),
):
    """One-time: promote a user to admin using the ADMIN_SECRET env var."""
    expected = os.getenv("ADMIN_SECRET", "").strip()
    if not expected or secret.strip() != expected:
        raise HTTPException(403, "Forbidden")
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.is_admin = True
    db.commit()
    return {"status": "promoted", "username": user.username}
