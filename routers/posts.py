"""Posts router — create drafts, publish, list history."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.post_draft import PostDraft
from models.post_target import PostTarget
from models.publish_job import PublishJob
from schemas.posts import PostDraftCreate, PublishRequest, PostDraftResponse
from services.security import get_current_user_id
import uuid

router = APIRouter(prefix="/posts", tags=["posts"])


@router.post("/drafts", status_code=201)
def create_draft(body: PostDraftCreate, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    draft = PostDraft(
        id             = str(uuid.uuid4()),
        user_id        = user_id,
        media_asset_id = body.media_asset_id,
        title          = body.title,
        master_caption = body.master_caption,
        status         = "draft",
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
            hashtags      = ",".join(t.hashtags or []),
            status        = "pending",
        )
        db.add(target)

    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "status": draft.status}


@router.get("/drafts")
def list_drafts(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    drafts = db.query(PostDraft).filter(PostDraft.user_id == user_id).order_by(PostDraft.created_at.desc()).all()
    return [{"id": d.id, "title": d.title, "status": d.status, "created_at": str(d.created_at)} for d in drafts]


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id == user_id).first()
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
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id == user_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.status == "published":
        raise HTTPException(status_code=409, detail="Draft already published.")

    # Enqueue or run synchronously
    try:
        from workers.tasks import publish_draft_task
        task = publish_draft_task.delay(draft_id, user_id)
        job_id = str(task.id)
    except Exception:
        # Celery/Redis not available — run synchronously inline
        from workers.tasks import run_publish_sync
        job_id = await run_publish_sync(draft_id, user_id, db)

    draft.status = "publishing"
    db.commit()
    return {"job_id": job_id, "draft_id": draft_id, "status": "publishing"}


@router.get("/history")
def post_history(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    drafts = db.query(PostDraft).filter(PostDraft.user_id == user_id).order_by(PostDraft.created_at.desc()).limit(100).all()
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
