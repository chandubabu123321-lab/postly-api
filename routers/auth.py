"""Auth router — register, login, current user."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models.user import User
from schemas.auth import RegisterRequest, LoginRequest, TokenResponse, UserResponse
from services.security import hash_password, verify_password, create_access_token, get_current_user_id
import uuid

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already registered.")
    user = User(
        id            = str(uuid.uuid4()),
        email         = body.email,
        name          = body.name,
        password_hash = hash_password(body.password),
    )
    db.add(user)
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id), token_type="bearer")


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this email.")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled. Ask your super admin.")
    return TokenResponse(access_token=create_access_token(user.id), token_type="bearer")


@router.get("/me", response_model=UserResponse)
def me(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    from services.accounts import can_manage_accounts
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserResponse(id=user.id, email=user.email, name=user.name, role=user.role or "user",
                        can_manage_accounts=can_manage_accounts(user))
