"""
X (Twitter) publisher — X API v2 with OAuth 2.0 PKCE.

Flow:
  1. Upload media (v1.1 endpoint) if any
  2. POST /2/tweets with text and optional media_ids

Docs:
  https://developer.twitter.com/en/docs/twitter-api/tweets/manage-tweets/api-reference/post-tweets
  https://developer.twitter.com/en/docs/twitter-api/v1/media/upload-media/api-reference/post-media-upload
"""
import asyncio
import base64
import httpx
import logging
from services.publishers.base import BasePublisher, PublishResult
from services.rate_limiter import respectful_delay, parse_rate_limit_headers

logger = logging.getLogger(__name__)

TWEETS_URL = "https://api.twitter.com/2/tweets"
UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"


class TwitterPublisher(BasePublisher):
    platform = "x"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        await respectful_delay("x")

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=60) as client:
            media_id = None

            if media_url:
                media_id = await self._upload_media(client, access_token, media_url)
                if media_id is None:
                    return PublishResult(success=False, error="X media upload failed.")

            tweet_body: dict = {"text": caption[:280]}
            if media_id:
                tweet_body["media"] = {"media_ids": [str(media_id)]}

            r = await client.post(TWEETS_URL, headers={**auth_headers, "Content-Type": "application/json"}, json=tweet_body)
            parse_rate_limit_headers(dict(r.headers), "x")

            if r.status_code not in (200, 201):
                return PublishResult(success=False, error=f"X tweet failed: {r.text}", api_response=r.json())

            data     = r.json().get("data", {})
            tweet_id = data.get("id", "")

            return PublishResult(
                success          = True,
                external_post_id = tweet_id,
                external_url     = f"https://x.com/i/web/status/{tweet_id}",
                api_response     = data,
            )

    async def _upload_media(self, client: httpx.AsyncClient, access_token: str, media_url: str) -> str | None:
        """INIT → APPEND → FINALIZE chunked media upload."""
        auth_headers = {"Authorization": f"Bearer {access_token}"}

        # Download media
        media_r = await client.get(media_url)
        if media_r.status_code != 200:
            logger.error("Could not download media for X upload: %s", media_url)
            return None
        media_bytes = media_r.content
        total_bytes = len(media_bytes)

        is_video   = any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov"])
        media_type = "video/mp4" if is_video else "image/jpeg"

        # INIT
        init_r = await client.post(UPLOAD_URL, headers=auth_headers, data={
            "command":       "INIT",
            "total_bytes":   str(total_bytes),
            "media_type":    media_type,
            "media_category": "tweet_video" if is_video else "tweet_image",
        })
        if init_r.status_code != 202:
            return None
        media_id = init_r.json().get("media_id_string")

        # APPEND in 4 MB chunks
        chunk_size = 4 * 1024 * 1024
        for i, offset in enumerate(range(0, total_bytes, chunk_size)):
            chunk  = media_bytes[offset: offset + chunk_size]
            b64    = base64.b64encode(chunk).decode()
            app_r  = await client.post(UPLOAD_URL, headers=auth_headers, data={
                "command":        "APPEND",
                "media_id":       media_id,
                "segment_index":  str(i),
                "media_data":     b64,
            })
            if app_r.status_code not in (200, 204):
                return None

        # FINALIZE
        fin_r = await client.post(UPLOAD_URL, headers=auth_headers, data={
            "command":  "FINALIZE",
            "media_id": media_id,
        })
        if fin_r.status_code not in (200, 201):
            return None

        # Poll processing for videos
        if is_video:
            for _ in range(20):
                await asyncio.sleep(3)
                status_r = await client.get(UPLOAD_URL, headers=auth_headers, params={
                    "command":  "STATUS",
                    "media_id": media_id,
                })
                info = status_r.json().get("processing_info", {})
                state = info.get("state")
                if state == "succeeded":
                    break
                if state == "failed":
                    return None

        return media_id
