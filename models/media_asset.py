from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(String, ForeignKey("users.id"), nullable=False)
    original_name = Column(String, nullable=False)
    file_url      = Column(String, nullable=False)     # local path or cloud URL
    file_type     = Column(String, nullable=False)     # image | video | document
    mime_type     = Column(String, nullable=True)
    file_size     = Column(Integer, nullable=True)     # bytes
    duration      = Column(Float, nullable=True)       # seconds (video only)
    width         = Column(Integer, nullable=True)
    height        = Column(Integer, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user        = relationship("User",      back_populates="media_assets")
    post_drafts = relationship("PostDraft", back_populates="media_asset")
