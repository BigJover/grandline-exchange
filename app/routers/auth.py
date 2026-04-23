from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas, auth
from app.main import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "glse_session"
COOKIE_MAX_AGE = 60 * 60 * 24  # 24 hours


@router.post("/signup", response_model=schemas.UserOut, status_code=201)
@limiter.limit("5/hour")
def signup(request: Request, response: Response, user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    if len(user_in.username) < 3 or len(user_in.username) > 32:
        raise HTTPException(status_code=400, detail="Username must be 3–32 characters")
    if not user_in.username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Username may only contain letters, numbers, _ and -")
    if db.query(models.User).filter(models.User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    if db.query(models.User).filter(models.User.email == user_in.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = models.User(
        username=user_in.username,
        email=user_in.email,
        password_hash=auth.hash_password(user_in.password),
        beri_balance=100_000,
        user_faction="citizen",
        faction_history=["citizen"],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = auth.create_access_token({"sub": user.username})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=COOKIE_MAX_AGE,
    )
    return user


@router.post("/login")
@limiter.limit("20/hour")
def login(request: Request, response: Response, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form.username).first()
    if not user or not auth.verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    token = auth.create_access_token({"sub": user.username})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=COOKIE_MAX_AGE,
    )
    return {"status": "ok"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, httponly=True, secure=True, samesite="strict")
    return {"status": "ok"}


@router.get("/me", response_model=schemas.UserOut)
def me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user
