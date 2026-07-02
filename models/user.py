from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class User(Base):
    __tablename__ = "users"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email         = Column(String, unique=True, nullable=False, index=True)
    name          = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    connected_accounts = relationship("ConnectedAccount", back_populates="user", cascade="all, delete-orphan")
    media_assets       = relationship("MediaAsset",       back_populates="user", cascade="all, delete-orphan")
    post_drafts        = relationship("PostDraft",        back_populates="user", cascade="all, delete-orphan")
    audit_logs         = relationship("AuditLog",         back_populates="user", cascade="all, delete-orphan")
