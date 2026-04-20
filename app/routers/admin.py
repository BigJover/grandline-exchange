import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.scheduler import run_beri_drop, WEEKLY_DROP

router = APIRouter(prefix="/admin", tags=["admin"])


def _check_secret(secret: str):
    expected = os.getenv("ADMIN_SECRET", "")
    if not expected or secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/beri-drop")
def trigger_beri_drop(secret: str, db: Session = Depends(get_db)):
    """Manually trigger a beri drop. Protected by ADMIN_SECRET env var."""
    _check_secret(secret)
    run_beri_drop()
    count = db.query(models.User).filter(models.User.is_bot == False).count()
    return {
        "status": "ok",
        "beri_per_user": WEEKLY_DROP,
        "users_paid": count,
        "total_distributed": WEEKLY_DROP * count,
    }
