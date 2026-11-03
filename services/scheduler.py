"""
Post scheduler — publishes scheduled drafts when their time comes.

Runs inside the API process, so posts only go out while the API is running.
A post that comes due while it was stopped is still published on the next start
if it is less than MAX_LATENESS late; older ones are marked "missed" so an
outdated post never goes live by surprise.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from database import SessionLocal
from models.post_draft import PostDraft

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 20
MAX_LATENESS = timedelta(hours=6)


def utc_now() -> datetime:
    """Naive UTC, matching how SQLite stores scheduled_at."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def publish_due_posts() -> None:
    from workers.tasks import run_publish_sync

    db = SessionLocal()
    try:
        now = utc_now()
        due = db.query(PostDraft).filter(
            PostDraft.status == "scheduled",
            PostDraft.scheduled_at <= now,
        ).all()

        for draft in due:
            if now - draft.scheduled_at > MAX_LATENESS:
                draft.status = "missed"
                db.commit()
                logger.warning("[scheduler] draft %s missed its time, not publishing", draft.id)
                continue

            # Set before publishing so the next check cannot pick it up again.
            draft.status = "publishing"
            db.commit()
            logger.info("[scheduler] publishing draft %s", draft.id)
            try:
                await run_publish_sync(draft.id, draft.user_id, db)
            except Exception:
                logger.exception("[scheduler] draft %s failed", draft.id)
                db.rollback()
                draft.status = "failed"
                db.commit()
    finally:
        db.close()


async def scheduler_loop() -> None:
    while True:
        try:
            await publish_due_posts()
        except Exception:
            logger.exception("[scheduler] check failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
