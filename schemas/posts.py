from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class PostTargetCreate(BaseModel):
    platform: str
    account_id: Optional[str] = None
    caption: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    privacy: str = "public"
    hashtags: Optional[str] = None


class PostDraftCreate(BaseModel):
    title: Optional[str] = None
    master_caption: Optional[str] = None
    media_asset_id: Optional[str] = None
    targets: List[PostTargetCreate] = []


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


class PublishRequest(BaseModel):
    platform_ids: Optional[List[str]] = None   # if None, publish to all targets
