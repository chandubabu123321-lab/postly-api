"""
Instagram publisher — Instagram API with Instagram Login.

Flow:
  1. Create media container (image or video)
  2. Poll container status until FINISHED
  3. Publish container → returns media_id
  4. Fetch permalink

The account is authorised through Instagram Login (instagram.com), so every
call goes to graph.instagram.com. graph.facebook.com is only for accounts
authorised through Facebook Login and rejects these tokens.

Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing/
"""
import asyncio
import httpx
import logging
from services.publishers.base import BasePublisher, PublishResult
from services.rate_limiter import respectful_delay, parse_rate_limit_headers

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.instagram.com/v21.0"
MAX_POLL   = 30   # max status checks before giving up
POLL_DELAY = 4    # seconds between status checks


class InstagramPublisher(BasePublisher):
    platform = "instagram"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        ig_user_id = extra.get("ig_user_id")
        if not ig_user_id:
            return PublishResult(success=False, error="No Instagram user ID in connected account.")

        post_type = extra.get("post_type") or "post"
        is_video  = any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi"])
        if post_type == "reel" and not is_video:
            return PublishResult(success=False, error="An Instagram Reel needs a video file.")

        await respectful_delay("instagram")

        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1 — create container. The API publishes every feed video as
            # a Reel, so for a video "Feed" is a Reel shown on the profile grid
            # and "Reel" stays in the Reels tab only. Stories take no caption.
            container_params = {"access_token": access_token}
            if post_type == "story":
                container_params["media_type"] = "STORIES"
            else:
                container_params["caption"] = caption
                if is_video:
                    container_params["media_type"]    = "REELS"
                    container_params["share_to_feed"] = "false" if post_type == "reel" else "true"

            if is_video:
                container_params["video_url"] = media_url
            else:
                container_params["image_url"] = media_url

            r = await client.post(f"{GRAPH_URL}/{ig_user_id}/media", params=container_params)
            parse_rate_limit_headers(dict(r.headers), "instagram")

            if r.status_code != 200:
                return PublishResult(success=False, error=f"Container creation failed: {r.text}", api_response=r.json())

            container_id = r.json().get("id")

            # Step 2 — poll status
            for _ in range(MAX_POLL):
                await asyncio.sleep(POLL_DELAY)
                status_r = await client.get(
                    f"{GRAPH_URL}/{container_id}",
                    params={"fields": "status_code", "access_token": access_token}
                )
                code = status_r.json().get("status_code", "")
                if code == "FINISHED":
                    break
                if code == "ERROR":
                    return PublishResult(success=False, error="Instagram container processing failed.", api_response=status_r.json())
            else:
                return PublishResult(success=False, error="Instagram container timed out.")

            # Step 3 — publish
            pub_r = await client.post(
                f"{GRAPH_URL}/{ig_user_id}/media_publish",
                params={"creation_id": container_id, "access_token": access_token}
            )
            if pub_r.status_code != 200:
                return PublishResult(success=False, error=f"Publish failed: {pub_r.text}", api_response=pub_r.json())

            media_id = pub_r.json().get("id")

            # Step 4 — get permalink
            perm_r   = await client.get(f"{GRAPH_URL}/{media_id}", params={"fields": "permalink", "access_token": access_token})
            permalink = perm_r.json().get("permalink")
            if not permalink and post_type != "story":
                permalink = f"https://www.instagram.com/p/{media_id}/"

            return PublishResult(
                success          = True,
                external_post_id = media_id,
                external_url     = permalink,
                api_response     = pub_r.json(),
            )
