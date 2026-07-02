"""
Celery tasks for background publishing.

If Redis is not running, run_publish_sync / run_target_sync are called inline
from the routers as an automatic fallback — no configuration required.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Celery app (optional — skipped if Redis absent)
# ──────────────────────────────────────────────
try:
    from celery import Celery
    from config import get_settings
    _s = get_settings()
    celery_app = Celery("postly", broker=_s.REDIS_URL, backend=_s.REDIS_URL)
    celery_app.conf.task_serializer      = "json"
    celery_app.conf.result_serializer    = "json"
    celery_app.conf.accept_content       = ["json"]
    celery_app.conf.task_acks_late       = True
    celery_app.conf.worker_prefetch_multiplier = 1
except Exception:
    celery_app = None


def _get_publisher(platform: str):
    from config import get_settings
    s = get_settings()

    flags = {
        "instagram": s.ENABLE_INSTAGRAM_REAL,
        "facebook":  s.ENABLE_FACEBOOK_REAL,
        "linkedin":  s.ENABLE_LINKEDIN_REAL,
        "youtube":   s.ENABLE_YOUTUBE_REAL,
        "x":         s.ENABLE_X_REAL,
    }

    if flags.get(platform, False):
        if platform == "instagram":
            from services.publishers.instagram import InstagramPublisher
            return InstagramPublisher()
        if platform == "facebook":
            from services.publishers.facebook import FacebookPublisher
            return FacebookPublisher()
        if platform == "linkedin":
            from services.publishers.linkedin import LinkedInPublisher
            return LinkedInPublisher()
        if platform == "youtube":
            from services.publishers.youtube import YouTubePublisher
            return YouTubePublisher()
        if platform == "x":
            from services.publishers.twitter import TwitterPublisher
            return TwitterPublisher()

    from services.publishers.mock import MockPublisher
    return MockPublisher(platform)


async def _publish_target(target_id: str, user_id: str, db):
    from models.post_target import PostTarget
    from models.post_draft import PostDraft
    from models.publish_job import PublishJob
    from models.connected_account import ConnectedAccount
    from models.media_asset import MediaAsset
    from services.security import decrypt_token
    from services.rate_limiter import backoff_retry

    target = db.query(PostTarget).get(target_id)
    if not target:
        logger.error("Target %s not found", target_id)
        return

    draft = target.post_draft

    # Find or create job
    existing = sorted(target.publish_jobs, key=lambda j: j.created_at, reverse=True)
    job      = existing[0] if existing else None

    if not job or job.job_status == "queued":
        if not job:
            job = PublishJob(id=str(uuid.uuid4()), post_target_id=target_id, job_status="queued", max_attempts=3)
            db.add(job)
            db.commit()

    job.job_status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts  += 1
    target.status  = "publishing"
    db.commit()

    # Resolve access token
    account = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id  == user_id,
        ConnectedAccount.platform == target.platform,
        ConnectedAccount.status   == "active",
    ).first()

    access_token = ""
    if account and account.encrypted_access_token:
        access_token = decrypt_token(account.encrypted_access_token)

    # Resolve media URL
    media_url = ""
    if draft.media_asset_id:
        asset = db.query(MediaAsset).get(draft.media_asset_id)
        if asset:
            media_url = asset.file_url

    # Extra platform-specific data
    extra = {}
    if account:
        import json
        try:
            extra = json.loads(account.extra_info_json or "{}")
        except Exception:
            pass

    extra.update({
        "title":       target.title,
        "description": target.description,
        "privacy":     target.privacy,
        "tags":        target.hashtags.split(",") if target.hashtags else [],
    })

    publisher = _get_publisher(target.platform)

    try:
        await backoff_retry(target.platform, job.attempts)
        result = await publisher.publish(access_token, media_url, target.caption, extra)
    except Exception as exc:
        result = type("R", (), {"success": False, "error": str(exc), "external_post_id": None, "external_url": None, "api_response": None})()

    if result.success:
        job.job_status       = "published"
        job.external_post_id = result.external_post_id
        job.external_url     = result.external_url
        job.api_response_json = str(result.api_response or {})
        job.completed_at     = datetime.now(timezone.utc)
        target.status        = "published"
    else:
        job.last_error = result.error
        if job.attempts >= job.max_attempts:
            job.job_status = "failed"
            target.status  = "failed"
        else:
            job.job_status = "retrying"

    db.commit()
    return result.success


async def run_publish_sync(draft_id: str, user_id: str, db) -> str:
    """Synchronous fallback when Celery/Redis is unavailable."""
    from models.post_draft import PostDraft
    draft = db.query(PostDraft).get(draft_id)
    if not draft:
        return "unknown"

    results = await asyncio.gather(
        *[_publish_target(t.id, user_id, db) for t in draft.post_targets],
        return_exceptions=True,
    )

    all_ok = all(r is True for r in results)
    draft.status = "published" if all_ok else "failed"
    db.commit()
    return draft_id


async def run_target_sync(target_id: str, user_id: str, db) -> None:
    await _publish_target(target_id, user_id, db)


# ──────────────────────────────────────────────
# Celery task wrappers
# ──────────────────────────────────────────────
if celery_app:
    @celery_app.task(name="publish_draft", bind=True, max_retries=0)
    def publish_draft_task(self, draft_id: str, user_id: str):
        from database import SessionLocal
        db = SessionLocal()
        try:
            asyncio.run(run_publish_sync(draft_id, user_id, db))
        finally:
            db.close()

    @celery_app.task(name="publish_target", bind=True, max_retries=0)
    def publish_target_task(self, target_id: str, user_id: str):
        from database import SessionLocal
        db = SessionLocal()
        try:
            asyncio.run(_publish_target(target_id, user_id, db))
        finally:
            db.close()
