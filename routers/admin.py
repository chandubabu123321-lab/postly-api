"""Admin router — the super admin's team: users and a report of who posted what."""
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from database import get_db
from models.post_draft import PostDraft
from models.user import User
from services.accounts import require_super_admin
from services.scheduler import utc_now
from services.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv")


class NewUser(BaseModel):
    name: str
    email: EmailStr
    password: str


class UserChange(BaseModel):
    is_active: Optional[bool] = None
    password: Optional[str] = None


def _team(admin: User, db: Session) -> list[User]:
    """The admin first, then the users they created."""
    members = db.query(User).filter(User.owner_id == admin.id).order_by(User.created_at).all()
    return [admin, *members]


def _member(admin: User, user_id: str, db: Session) -> User:
    user = db.get(User, user_id)
    if not user or user.owner_id != admin.id:
        raise HTTPException(status_code=404, detail="User not found in your team.")
    return user


def _media_kind(draft: PostDraft) -> str:
    asset = draft.media_asset
    if not asset:
        return "none"
    return "video" if asset.file_url.lower().endswith(VIDEO_EXTS) else "photo"


def _user_row(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role or "user",
            "is_active": bool(user.is_active), "created_at": str(user.created_at)}


@router.get("/users")
def list_users(admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return [_user_row(u) for u in _team(admin, db)]


@router.post("/users", status_code=201)
def create_user(body: NewUser, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="That email already has an account.")
    user = User(id=str(uuid.uuid4()), name=body.name.strip(), email=body.email,
                password_hash=hash_password(body.password), role="user", owner_id=admin.id, is_active=True)
    db.add(user)
    db.commit()
    return _user_row(user)


@router.patch("/users/{user_id}")
def change_user(user_id: str, body: UserChange, admin: User = Depends(require_super_admin),
                db: Session = Depends(get_db)):
    user = _member(admin, user_id, db)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(status_code=422, detail="Password must be at least 8 characters.")
        user.password_hash = hash_password(body.password)
    db.commit()
    return _user_row(user)


@router.get("/report")
def report(days: int = 7, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Posts per user over the last `days` days (0 = all time), plus the recent posts themselves."""
    team  = _team(admin, db)
    names = {u.id: u.name for u in team}
    query = db.query(PostDraft).filter(PostDraft.user_id.in_(names))
    if days > 0:
        query = query.filter(PostDraft.created_at >= utc_now() - timedelta(days=days))
    drafts = query.order_by(PostDraft.created_at.desc()).all()

    per_user = {u.id: {"user_id": u.id, "name": u.name, "email": u.email, "is_active": bool(u.is_active),
                       "posts": 0, "photos": 0, "videos": 0, "published": 0, "failed": 0, "scheduled": 0,
                       "platforms": {}, "last_post_at": None} for u in team}
    recent = []
    for d in drafts:
        row  = per_user[d.user_id]
        kind = _media_kind(d)
        row["posts"] += 1
        if kind in ("photo", "video"):
            row[kind + "s"] += 1
        if d.status in ("published", "failed", "scheduled"):
            row[d.status] += 1
        row["last_post_at"] = row["last_post_at"] or str(d.created_at)

        targets = []
        for t in d.post_targets:
            jobs = sorted(t.publish_jobs, key=lambda j: j.created_at, reverse=True)
            status = jobs[0].job_status if jobs else "not_started"
            if status == "published":
                row["platforms"][t.platform] = row["platforms"].get(t.platform, 0) + 1
            targets.append({"platform": t.platform, "post_type": t.post_type or "post", "status": status})
        if len(recent) < 100:
            recent.append({"id": d.id, "title": d.title, "user": names[d.user_id], "media": kind,
                           "status": d.status, "created_at": str(d.created_at),
                           "scheduled_at": str(d.scheduled_at) if d.scheduled_at else None, "targets": targets})

    users  = list(per_user.values())
    totals = {k: sum(u[k] for u in users) for k in ("posts", "photos", "videos", "published", "failed", "scheduled")}
    return {"days": days, "totals": totals, "users": users, "recent": recent}
