"""
Who owns which social accounts.

A super admin connects the social accounts and creates team users. A team
user (owner_id set) posts through the super admin's accounts but cannot
connect or disconnect them. A user with no owner manages their own accounts.
"""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from services.security import get_current_user_id


def account_owner_id(db: Session, user_id: str) -> str:
    """The user whose connected accounts this user posts through."""
    user = db.get(User, user_id)
    return user.owner_id if user and user.owner_id else user_id


def visible_user_ids(db: Session, user_id: str) -> list[str]:
    """Whose posts this user may see: their own, plus their team's for a super admin."""
    user = db.get(User, user_id)
    if not user or user.role != "super_admin":
        return [user_id]
    return [user_id, *(u.id for u in db.query(User.id).filter(User.owner_id == user_id))]


def can_manage_accounts(user: User) -> bool:
    return user.role == "super_admin" or not user.owner_id


def require_account_manager(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)) -> str:
    user = db.get(User, user_id)
    if not user or not can_manage_accounts(user):
        raise HTTPException(status_code=403, detail="Only the super admin can connect or disconnect accounts.")
    return user_id


def require_super_admin(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)) -> User:
    user = db.get(User, user_id)
    if not user or user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin only.")
    return user
