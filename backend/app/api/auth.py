"""Authentication endpoints (SRS FR-001)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import get_current_user
from backend.app.schemas import TokenOut, UserOut
from backend.app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str
    password: str = Field(min_length=6)
    role: str = Field(default="analyst", pattern="^(analyst|viewer)$")


@router.post("/login", response_model=TokenOut)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenOut:
    user = db.query(models.User).filter_by(email=body.email.strip().lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenOut(
        access_token=create_access_token(user.id, user.email, user.role),
        user=UserOut.model_validate(user),
    )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenOut:
    email = body.email.strip().lower()
    if db.query(models.User).filter_by(email=email).first() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = models.User(
        name=body.name.strip(),
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenOut(
        access_token=create_access_token(user.id, user.email, user.role),
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
def me(user: models.User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)