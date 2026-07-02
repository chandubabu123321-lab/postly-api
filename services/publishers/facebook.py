"""
Facebook Page publisher — Meta Graph API.

Docs: https://developers.facebook.com/docs/pages-api/posts/
"""
import httpx
import logging
from services.publishers.base import BasePublisher, PublishResult
from services.rate_limiter import respectful_delay, parse_rate_limit_headers

logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/v21.0"


class FacebookPublisher(BasePublisher):
    platform = "facebook"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        page_id = extra.get("page_id")
        if not page_id:
            return PublishResult(success=False, error="No Facebook Page ID in connected account.")

        await respectful_delay("facebook")

        is_video = any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi"])

        async with httpx.AsyncClient(timeout=60) as client:
            if is_video:
                endpoint = f"{GRAPH_URL}/{page_id}/videos"
                payload  = {"access_token": access_token, "file_url": media_url, "description": caption}
            else:
                endpoint = f"{GRAPH_URL}/{page_id}/photos"
                payload  = {"access_token": access_token, "url": media_url, "caption": caption}

            r = await client.post(endpoint, params=payload)
            parse_rate_limit_headers(dict(r.headers), "facebook")

            if r.status_code != 200:
                return PublishResult(success=False, error=f"Facebook post failed: {r.text}", api_response=r.json())

            data     = r.json()
            post_id  = data.get("post_id") or data.get("id", "")
            post_url = f"https://www.facebook.com/{page_id}/posts/{post_id.split('_')[-1]}"

            return PublishResult(
                success          = True,
                external_post_id = post_id,
                external_url     = post_url,
                api_response     = data,
            )
