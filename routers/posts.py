"""Posts router — create drafts, publish, list history."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.post_draft import PostDraft
from models.post_target import PostTarget
from models.publish_job import PublishJob
from schemas.posts import POST_TYPES, PostDraftCreate, PublishRequest, PostDraftResponse, ScheduleRequest
from services.scheduler import utc_now
from services.security import get_current_user_id
from services.accounts import visible_user_ids
from datetime import timedelta, timezone
import uuid

router = APIRouter(prefix="/posts", tags=["posts"])


def _schedule_time(scheduled_at, grace: timedelta = timedelta(0)):
    """Naive UTC for storage. `grace` lets a time pass while a big file was uploading."""
    if scheduled_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="scheduled_at must include a timezone.")
    when = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
    if when <= utc_now() - grace:
        raise HTTPException(status_code=400, detail="Pick a time in the future.")
    return when


@router.post("/drafts", status_code=201)
def create_draft(body: PostDraftCreate, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    for t in body.targets:
        if t.post_type not in POST_TYPES.get(t.platform, set()):
            raise HTTPException(status_code=400, detail=f"{t.platform} cannot publish a {t.post_type}.")

    # Files must be the poster's own uploads.
    asset_ids = {t.media_asset_id for t in body.targets if t.media_asset_id} | ({body.media_asset_id} - {None})
    if asset_ids:
        from models.media_asset import MediaAsset
        own = {a.id for a in db.query(MediaAsset.id).filter(MediaAsset.id.in_(asset_ids), MediaAsset.user_id == user_id)}
        if own != asset_ids:
            raise HTTPException(status_code=400, detail="Unknown media file.")

    # Scheduling in the same request means a post can never be left as a
    # plain draft because a second "schedule" call failed.
    scheduled_at = None
    if body.scheduled_at:
        if not body.targets:
            raise HTTPException(status_code=400, detail="Select at least one platform.")
        scheduled_at = _schedule_time(body.scheduled_at, grace=timedelta(minutes=10))

    draft = PostDraft(
        id             = str(uuid.uuid4()),
        user_id        = user_id,
        media_asset_id = body.media_asset_id,
        title          = body.title,
        master_caption = body.master_caption,
        status         = "scheduled" if scheduled_at else "draft",
        scheduled_at   = scheduled_at,
    )
    db.add(draft)
    db.flush()

    for t in body.targets:
        target = PostTarget(
            id            = str(uuid.uuid4()),
            post_draft_id = draft.id,
            platform      = t.platform,
            caption       = t.caption or body.master_caption,
            title         = t.title,
            description   = t.description,
            privacy       = t.privacy or "public",
            post_type     = t.post_type,
            media_asset_id = t.media_asset_id,
            hashtags      = ",".join(t.hashtags or []),
            status        = "pending",
        )
        db.add(target)

    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "status": draft.status}


@router.get("/drafts")
def list_drafts(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    drafts = db.query(PostDraft).filter(PostDraft.user_id.in_(visible_user_ids(db, user_id))).order_by(PostDraft.created_at.desc()).all()

    def media(d):
        """Path under the API for a thumbnail, and whether it is a photo or a video."""
        if not d.media_asset or "/uploads/" not in d.media_asset.file_url:
            return None, None
        path = "/uploads/" + d.media_asset.file_url.split("/uploads/")[-1]
        return path, "video" if path.lower().endswith((".mp4", ".mov", ".avi", ".mkv")) else "photo"

    rows = []
    for d in drafts:
        path, kind = media(d)
        rows.append({"id": d.id, "title": d.title, "caption": d.master_caption, "status": d.status,
                     "created_at": str(d.created_at),
                     "scheduled_at": str(d.scheduled_at) if d.scheduled_at else None,
                     "user_id": d.user_id, "user_name": d.user.name if d.user else None,
                     "media_path": path, "media_kind": kind,
                     "targets": [{"platform": t.platform, "post_type": t.post_type or "post"} for t in d.post_targets]})
    return rows


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id.in_(visible_user_ids(db, user_id))).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    targets = [{"id": t.id, "platform": t.platform, "caption": t.caption, "status": t.status} for t in draft.post_targets]
    return {"id": draft.id, "title": draft.title, "master_caption": draft.master_caption,
            "status": draft.status, "targets": targets, "created_at": str(draft.created_at)}


@router.post("/publish/{draft_id}")
async def publish_draft(
    draft_id: str,
    body: PublishRequest,
    user_id: str    = Depends(get_current_user_id),
    db: Session     = Depends(get_db),
):
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id.in_(visible_user_ids(db, user_id))).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.status == "published":
        raise HTTPException(status_code=409, detail="Draft already published.")

    # Mark as in-flight BEFORE dispatching. The inline fallback below finishes
    # the whole publish and writes the final status itself, so setting
    # "publishing" afterwards would overwrite the real result and leave every
    # draft stuck at "publishing" forever.
    draft.status = "publishing"
    db.commit()

    # Enqueue or run synchronously
    from workers.tasks import broker_available
    if broker_available():
        from workers.tasks import publish_draft_task
        task = publish_draft_task.delay(draft_id, user_id)
        job_id = str(task.id)
    else:
        # Redis not available — run synchronously inline
        from workers.tasks import run_publish_sync
        job_id = await run_publish_sync(draft_id, user_id, db)

    # Celery path: still "publishing" (the worker updates it later).
    # Inline path: already "published" or "failed".
    db.refresh(draft)
    return {"job_id": job_id, "draft_id": draft_id, "status": draft.status}


@router.post("/{draft_id}/retry")
async def retry_draft(draft_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """Publish again to the platforms that failed, or for the first time if it is still a draft.
    Platforms already posted are skipped."""
    from workers.tasks import retry_failed_targets

    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id.in_(visible_user_ids(db, user_id))).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Post not found.")
    if draft.status not in ("failed", "missed", "draft"):
        raise HTTPException(status_code=409, detail=f"A {draft.status} post cannot be retried.")

    await retry_failed_targets(draft, user_id, db)

    db.refresh(draft)
    results = []
    for target in draft.post_targets:
        jobs = sorted(target.publish_jobs, key=lambda j: j.created_at, reverse=True)
        job  = jobs[0] if jobs else None
        results.append({"platform": target.platform, "post_type": target.post_type or "post",
                        "status": job.job_status if job else "not_started",
                        "error": job.last_error if job else None})
    return {"id": draft.id, "status": draft.status, "results": results}


@router.post("/schedule/{draft_id}")
def schedule_draft(
    draft_id: str,
    body: ScheduleRequest,
    user_id: str = Depends(get_current_user_id),
    db: Session  = Depends(get_db),
):
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id.in_(visible_user_ids(db, user_id))).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.status not in ("draft", "scheduled"):
        raise HTTPException(status_code=409, detail=f"A {draft.status} post cannot be scheduled.")
    if not draft.post_targets:
        raise HTTPException(status_code=400, detail="Select at least one platform.")
    draft.scheduled_at = _schedule_time(body.scheduled_at)
    draft.status       = "scheduled"
    db.commit()
    return {"id": draft.id, "status": draft.status, "scheduled_at": str(draft.scheduled_at)}


@router.post("/schedule/{draft_id}/cancel")
def cancel_schedule(draft_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id.in_(visible_user_ids(db, user_id))).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.status != "scheduled":
        raise HTTPException(status_code=409, detail="This post is no longer scheduled.")

    draft.status       = "draft"
    draft.scheduled_at = None
    db.commit()
    return {"id": draft.id, "status": draft.status}


@router.get("/history")
def post_history(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    drafts = db.query(PostDraft).filter(PostDraft.user_id.in_(visible_user_ids(db, user_id))).order_by(PostDraft.created_at.desc()).limit(100).all()
    result = []
    for d in drafts:
        targets = []
        for t in d.post_targets:
            jobs = sorted(t.publish_jobs, key=lambda j: j.created_at, reverse=True)
            job  = jobs[0] if jobs else None
            targets.append({
                "platform":   t.platform,
                "status":     t.status,
                "post_url":   job.external_url if job else None,
                "published":  str(job.completed_at) if job and job.completed_at else None,
            })
        result.append({"id": d.id, "title": d.title, "status": d.status,
                        "created_at": str(d.created_at), "targets": targets})
    return result


@router.get("/{draft_id}/metrics")
async def post_metrics(draft_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """Likes, comments, shares and views for each platform this post went out on."""
    import asyncio
    from models.connected_account import ConnectedAccount
    from services.accounts import account_owner_id
    from services.metrics import fetch_metrics
    from services.token_refresh import ensure_fresh_token

    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id.in_(visible_user_ids(db, user_id))).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Post not found.")

    async def one(target):
        jobs = sorted(target.publish_jobs, key=lambda j: j.created_at, reverse=True)
        job  = next((j for j in jobs if j.job_status == "published"), None)
        row  = {"platform": target.platform, "post_type": target.post_type or "post",
                "post_url": job.external_url if job else None}
        if not job:
            return {**row, "likes": None, "comments": None, "shares": None, "views": None,
                    "note": "Not published on this platform."}

        account = db.query(ConnectedAccount).filter(
            ConnectedAccount.user_id  == account_owner_id(db, user_id),
            ConnectedAccount.platform == target.platform,
            ConnectedAccount.status   == "active",
        ).first()
        token = await ensure_fresh_token(account, db) if account else None
        if not token:
            return {**row, "likes": None, "comments": None, "shares": None, "views": None,
                    "note": "Account is not connected — reconnect it to see stats."}
        return {**row, **await fetch_metrics(target.platform, token, job.external_post_id)}

    return await asyncio.gather(*[one(t) for t in draft.post_targets])
