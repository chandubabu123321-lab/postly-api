from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id       = Column(String, ForeignKey("users.id"), nullable=True)
    action        = Column(String, nullable=False)      # upload | draft | publish | retry | connect | disconnect
    platform      = Column(String, nullable=True)
    resource_id   = Column(String, nullable=True)       # post_id, job_id, etc.
    status        = Column(String, nullable=True)       # success | failed
    metadata_json = Column(Text, nullable=True)         # extra context
    ip_address    = Column(String, nullable=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="audit_logs")
