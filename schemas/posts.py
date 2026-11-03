from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# What each platform can publish through Postly.
POST_TYPES = {
    "instagram": {"post", "reel", "story"},
    "facebook":  {"post", "reel", "story"},
    "youtube":   {"post", "short"},
    "linkedin":  {"post"},
    "x":         {"post"},
}


class PostTargetCreate(BaseModel):
    platform: str
    account_id: Optional[str] = None
    caption: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    privacy: str = "public"
    hashtags: Optional[List[str]] = None
    post_type: str = "post"   # see POST_TYPES
    media_asset_id: Optional[str] = None   # this platform's own file, e.g. a 9:16 crop for a Reel


class PostDraftCreate(BaseModel):
    title: Optional[str] = None
    master_caption: Optional[str] = None
    media_asset_id: Optional[str] = None
    targets: List[PostTargetCreate] = []
    scheduled_at: Optional[datetime] = None   # set to schedule the post in the same request


class PostDraftUpdate(BaseModel):
    title: Optional[str] = None
    master_caption: Optional[str] = None
    targets: Optional[List[PostTargetCreate]] = None
    scheduled_at: Optional[datetime] = None


class PublishJobResponse(BaseModel):
    id: str
    platform: str
    job_status: str
    attempts: int
    last_error: Optional[str]
    external_post_id: Optional[str]
    external_url: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PostTargetResponse(BaseModel):
    id: str
    platform: str
    status: str
    caption: Optional[str]
    title: Optional[str]
    publish_jobs: List[PublishJobResponse] = []

    model_config = {"from_attributes": True}


class PostDraftResponse(BaseModel):
    id: str
    title: Optional[str]
    master_caption: Optional[str]
    status: str
    scheduled_at: Optional[datetime]
    created_at: datetime
    post_targets: List[PostTargetResponse] = []

    model_config = {"from_attributes": True}


class ScheduleRequest(BaseModel):
    scheduled_at: datetime


class PublishRequest(BaseModel):
    platform_ids: Optional[List[str]] = None   # if None, publish to all targets
