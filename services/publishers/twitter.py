"""
X (Twitter) publisher — X API v2 with OAuth 2.0 PKCE.

Flow:
  1. Upload media (v2 chunked upload: initialize → append → finalize) if any
  2. POST /2/tweets with text and optional media_ids

The v1.1 upload endpoint is retired and never accepted OAuth 2.0 user
tokens; the v2 endpoints do, given the media.write scope.

Docs:
  https://docs.x.com/x-api/posts/create-post
  https://docs.x.com/x-api/media/quickstart/media-upload-chunked
"""
import asyncio
import httpx
import logging
from services.publishers.base import BasePublisher, PublishResult
from services.rate_limiter import respectful_delay, parse_rate_limit_headers

logger = logging.getLogger(__name__)

TWEETS_URL = "https://api.x.com/2/tweets"
UPLOAD_URL = "https://api.x.com/2/media/upload"

MIME_BY_EXT = {
    ".mp4":  "video/mp4",
    ".mov":  "video/quicktime",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}


class MediaUploadError(Exception):
    pass


class TwitterPublisher(BasePublisher):
    platform = "x"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        await respectful_delay("x")

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            media_id = None

            if media_url:
                try:
                    media_id = await self._upload_media(client, auth_headers, media_url)
                except MediaUploadError as e:
                    return PublishResult(success=False, error=f"X media upload failed: {e}")

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

    async def _upload_media(self, client: httpx.AsyncClient, auth_headers: dict, media_url: str) -> str:
        """initialize → append (4 MB chunks) → finalize → poll status for videos."""
        media_r = await client.get(media_url)
        if media_r.status_code != 200:
            raise MediaUploadError(f"could not download {media_url}")
        media_bytes = media_r.content
        total_bytes = len(media_bytes)

        ext        = "." + media_url.lower().rsplit(".", 1)[-1]
        media_type = MIME_BY_EXT.get(ext, "image/jpeg")
        is_video   = media_type.startswith("video")

        init_r = await client.post(f"{UPLOAD_URL}/initialize", headers=auth_headers, json={
            "media_type":     media_type,
            "total_bytes":    total_bytes,
            "media_category": "tweet_video" if is_video else "tweet_image",
        })
        if init_r.status_code not in (200, 201, 202):
            raise MediaUploadError(f"initialize {init_r.status_code}: {init_r.text}")
        media_id = init_r.json()["data"]["id"]

        chunk_size = 4 * 1024 * 1024
        for i, offset in enumerate(range(0, total_bytes, chunk_size)):
            app_r = await client.post(
                f"{UPLOAD_URL}/{media_id}/append",
                headers=auth_headers,
                data={"segment_index": str(i)},
                files={"media": ("chunk", media_bytes[offset: offset + chunk_size], "application/octet-stream")},
            )
            if app_r.status_code not in (200, 201, 204):
                raise MediaUploadError(f"append {app_r.status_code}: {app_r.text}")

        fin_r = await client.post(f"{UPLOAD_URL}/{media_id}/finalize", headers=auth_headers)
        if fin_r.status_code not in (200, 201):
            raise MediaUploadError(f"finalize {fin_r.status_code}: {fin_r.text}")

        info = fin_r.json().get("data", {}).get("processing_info")
        for _ in range(40):
            if not info or info.get("state") == "succeeded":
                return media_id
            if info.get("state") == "failed":
                raise MediaUploadError(f"processing failed: {info}")
            await asyncio.sleep(info.get("check_after_secs", 3))
            status_r = await client.get(UPLOAD_URL, headers=auth_headers,
                                        params={"command": "STATUS", "media_id": media_id})
            info = status_r.json().get("data", {}).get("processing_info")
        raise MediaUploadError("processing timed out")
