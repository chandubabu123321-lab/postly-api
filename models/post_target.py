from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class PostTarget(Base):
    __tablename__ = "post_targets"

    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_draft_id  = Column(String, ForeignKey("post_drafts.id"), nullable=False)
    platform       = Column(String, nullable=False)    # instagram | facebook | linkedin | youtube | x
    account_id     = Column(String, nullable=True)     # connected_account.id
    caption        = Column(Text, nullable=True)
    title          = Column(String, nullable=True)     # YouTube only
    description    = Column(Text, nullable=True)       # YouTube only
    privacy        = Column(String, default="public")  # public | unlisted | private
    post_type      = Column(String, default="post")    # post | reel | story | short
    media_asset_id = Column(String, ForeignKey("media_assets.id"), nullable=True)  # own file, else the post's
    hashtags       = Column(String, nullable=True)
    status         = Column(String, default="pending") # pending | publishing | published | failed
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    post_draft   = relationship("PostDraft",   back_populates="post_targets")
    publish_jobs = relationship("PublishJob",  back_populates="post_target", cascade="all, delete-orphan")
    media_asset  = relationship("MediaAsset")
