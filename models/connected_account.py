from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base


class ConnectedAccount(Base):
    __tablename__ = "connected_accounts"

    id                      = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id                 = Column(String, ForeignKey("users.id"), nullable=False)
    platform                = Column(String, nullable=False)          # instagram | facebook | linkedin | youtube | x
    account_name            = Column(String, nullable=True)
    external_account_id     = Column(String, nullable=True)           # page_id, channel_id, etc.
    encrypted_access_token  = Column(Text, nullable=True)
    encrypted_refresh_token = Column(Text, nullable=True)
    token_expires_at        = Column(DateTime, nullable=True)
    scopes                  = Column(String, nullable=True)           # comma-separated
    status                  = Column(String, default="active")        # active | expired | revoked
    extra_info_json         = Column(Text, nullable=True)              # platform-specific extras (page_id, author_urn, etc.)
    created_at              = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at              = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="connected_accounts")
