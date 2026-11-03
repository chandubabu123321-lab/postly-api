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
    # A team user posts through their super admin's connected accounts.
    from services.accounts import account_owner_id
    account = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id  == account_owner_id(db, user_id),
        ConnectedAccount.platform == target.platform,
        ConnectedAccount.status   == "active",
    ).first()

    access_token  = ""
    token_expired = False
    if account and account.encrypted_access_token:
        from services.token_refresh import ensure_fresh_token
        fresh = await ensure_fresh_token(account, db)
        if fresh is None:
            token_expired = True
        else:
            access_token = fresh

    # Resolve media URL
    media_url = ""
    asset_id  = target.media_asset_id or draft.media_asset_id
    if asset_id:
        asset = db.query(MediaAsset).get(asset_id)
        if asset:
            media_url = asset.file_url
            # Locally stored files are served from APP_BASE_URL, which changes
            # whenever the tunnel restarts — rebuild from the current value so
            # a retry after a tunnel change still points at a live URL.
            if "/uploads/" in media_url:
                from config import get_settings
                filename  = media_url.split("/uploads/")[-1]
                media_url = f"{get_settings().APP_BASE_URL.rstrip('/')}/uploads/{filename}"

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
        "post_type":   target.post_type or "post",
        "tags":        target.hashtags.split(",") if target.hashtags else [],
    })

    publisher = _get_publisher(target.platform)

    try:
        if token_expired:
            raise RuntimeError(
                f"{target.platform} login expired and could not be refreshed. "
                "Disconnect and connect the account again in Postly."
            )
        if needs_video_but_has_photo(target):
            media_url = await _youtube_video_from_photo(media_url, target.post_type)
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
    from models.post_target import PostTarget
    await _publish_target(target_id, user_id, db)
    refresh_draft_status(db.query(PostTarget).get(target_id).post_draft, db)


def _latest_job(target):
    jobs = sorted(target.publish_jobs, key=lambda j: j.created_at, reverse=True)
    return jobs[0] if jobs else None


def reset_job(job, db) -> None:
    """Give a failed job a fresh set of attempts."""
    job.job_status = "queued"
    job.attempts   = 0
    job.last_error = None
    db.commit()


async def _youtube_video_from_photo(media_url: str, post_type: str) -> str:
    """Turn the post's photo into a short video and return the video's URL."""
    from pathlib import Path
    from config import get_settings
    from services.photo_video import photo_to_video

    s     = get_settings()
    photo = Path(s.UPLOAD_DIR) / media_url.split("/uploads/")[-1]
    video = await photo_to_video(photo, post_type or "post")
    return f"{s.APP_BASE_URL.rstrip('/')}/uploads/{video.name}"


def needs_video_but_has_photo(target) -> bool:
    """YouTube and Reels only take videos, so a photo has to be turned into one first."""
    asset = target.media_asset or target.post_draft.media_asset
    wants_video = target.platform == "youtube" or target.post_type == "reel"
    return (wants_video and asset is not None
            and not asset.file_url.lower().endswith((".mp4", ".mov", ".avi", ".mkv")))


def refresh_draft_status(draft, db) -> None:
    statuses = [j.job_status if j else "not_started" for j in map(_latest_job, draft.post_targets)]
    done     = all(s in ("published", "skipped") for s in statuses) and "published" in statuses
    draft.status = "published" if done else "failed"
    db.commit()


async def retry_failed_targets(draft, user_id: str, db) -> None:
    """Publish only the platforms that have not succeeded, so nothing posts twice."""
    targets = []
    for target in draft.post_targets:
        job = _latest_job(target)
        if job and job.job_status in ("published", "skipped"):
            continue
        if job:
            reset_job(job, db)
        targets.append(target)

    draft.status = "publishing"
    db.commit()
    await asyncio.gather(
        *[_publish_target(t.id, user_id, db) for t in targets],
        return_exceptions=True,
    )
    refresh_draft_status(draft, db)


def broker_available() -> bool:
    """Quick Redis check. Without it Celery keeps retrying for about two minutes before giving up."""
    import socket
    from urllib.parse import urlparse
    from config import get_settings

    if not celery_app:
        return False
    url = urlparse(get_settings().REDIS_URL)
    try:
        with socket.create_connection((url.hostname or "localhost", url.port or 6379), timeout=0.5):
            return True
    except OSError:
        return False


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
