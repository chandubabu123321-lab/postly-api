from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class PostDraft(Base):
    __tablename__ = "post_drafts"

    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String, ForeignKey("users.id"), nullable=False)
    media_asset_id = Column(String, ForeignKey("media_assets.id"), nullable=True)
    title          = Column(String, nullable=True)
    master_caption = Column(Text, nullable=True)
    status         = Column(String, default="draft")   # draft | published | scheduled | failed
    scheduled_at   = Column(DateTime, nullable=True)
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user         = relationship("User",       back_populates="post_drafts")
    media_asset  = relationship("MediaAsset", back_populates="post_drafts")
    post_targets = relationship("PostTarget", back_populates="post_draft", cascade="all, delete-orphan")
