"""Jobs router — check publish job status, retry failed jobs."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.publish_job import PublishJob
from models.post_target import PostTarget
from models.post_draft import PostDraft
from services.security import get_current_user_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    job = db.query(PublishJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "id":              job.id,
        "status":          job.job_status,
        "platform":        job.post_target.platform,
        "attempts":        job.attempts,
        "external_url":    job.external_url,
        "last_error":      job.last_error,
        "started_at":      str(job.started_at) if job.started_at else None,
        "completed_at":    str(job.completed_at) if job.completed_at else None,
    }


@router.get("/draft/{draft_id}")
def jobs_for_draft(draft_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    draft = db.query(PostDraft).filter(PostDraft.id == draft_id, PostDraft.user_id == user_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    result = []
    for target in draft.post_targets:
        jobs = sorted(target.publish_jobs, key=lambda j: j.created_at, reverse=True)
        job  = jobs[0] if jobs else None
        result.append({
            "platform":     target.platform,
            "target_id":    target.id,
            "target_status": target.status,
            "job_id":       job.id if job else None,
            "job_status":   job.job_status if job else "not_started",
            "external_url": job.external_url if job else None,
            "last_error":   job.last_error if job else None,
        })
    return result


@router.post("/retry/{job_id}")
async def retry_job(job_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    job = db.query(PublishJob).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.job_status not in ("failed",):
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried.")

    job.job_status = "queued"
    job.attempts   = 0
    job.last_error = None
    db.commit()

    try:
        from workers.tasks import publish_target_task
        publish_target_task.delay(job.post_target_id, user_id)
    except Exception:
        from workers.tasks import run_target_sync
        await run_target_sync(job.post_target_id, user_id, db)

    return {"status": "queued", "job_id": job_id}
