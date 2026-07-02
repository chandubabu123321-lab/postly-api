from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class PublishJob(Base):
    __tablename__ = "publish_jobs"

    id               = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_target_id   = Column(String, ForeignKey("post_targets.id"), nullable=False)
    job_status       = Column(String, default="queued")   # queued | running | published | failed | retrying
    attempts         = Column(Integer, default=0)
    max_attempts     = Column(Integer, default=3)
    last_error       = Column(Text, nullable=True)
    external_post_id = Column(String, nullable=True)      # ID returned by the platform
    external_url     = Column(String, nullable=True)      # URL to the live post
    api_response_json= Column(Text, nullable=True)        # raw API response stored for debugging
    started_at       = Column(DateTime, nullable=True)
    completed_at     = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at       = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    post_target = relationship("PostTarget", back_populates="publish_jobs")
