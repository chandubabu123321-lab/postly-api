"""
YouTube publisher — YouTube Data API v3.

Uses resumable upload for video, thumbnails optional.
All videos require a video file (YouTube does not support image-only posts).

Docs: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
"""
import httpx
import logging
from services.publishers.base import BasePublisher, PublishResult
from services.rate_limiter import respectful_delay, parse_rate_limit_headers

logger = logging.getLogger(__name__)

UPLOAD_API  = "https://www.googleapis.com/upload/youtube/v3/videos"
DATA_API    = "https://www.googleapis.com/youtube/v3"
CHUNK_SIZE  = 8 * 1024 * 1024   # 8 MB


class YouTubePublisher(BasePublisher):
    platform = "youtube"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        # The worker always sets these keys, possibly to None, so fall back with `or`.
        title       = extra.get("title") or caption[:100] or "Untitled"
        description = extra.get("description") or caption
        privacy     = extra.get("privacy") or "public"   # public | unlisted | private
        tags        = extra.get("tags") or []

        # YouTube has no Shorts upload flag: it decides from the video being
        # vertical and at most 3 minutes. The tag is only a helpful signal.
        if extra.get("post_type") == "short" and "#shorts" not in f"{title} {description}".lower():
            description = f"{description}\n\n#Shorts"

        if not media_url or not any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi", ".mkv"]):
            return PublishResult(success=False, error="YouTube requires a video file.")

        await respectful_delay("youtube")

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=120) as client:
            # Step 1 — download media bytes
            media_r = await client.get(media_url)
            if media_r.status_code != 200:
                return PublishResult(success=False, error="Could not download media for YouTube upload.")
            media_bytes = media_r.content
            media_size  = len(media_bytes)

            # Step 2 — initiate resumable upload session
            init_r = await client.post(
                f"{UPLOAD_API}?uploadType=resumable&part=snippet,status",
                headers={
                    **auth_headers,
                    "Content-Type":           "application/json",
                    "X-Upload-Content-Type":  "video/*",
                    "X-Upload-Content-Length": str(media_size),
                },
                json={
                    "snippet": {
                        "title":       title,
                        "description": description,
                        "tags":        tags,
                        "categoryId":  "22",   # People & Blogs default
                    },
                    "status": {
                        "privacyStatus":          privacy,
                        "selfDeclaredMadeForKids": False,
                    },
                }
            )
            parse_rate_limit_headers(dict(init_r.headers), "youtube")

            if init_r.status_code != 200:
                return PublishResult(success=False, error=f"YouTube upload init failed: {init_r.text}")

            upload_url = init_r.headers.get("location")
            if not upload_url:
                return PublishResult(success=False, error="YouTube did not return an upload URL.")

            # Step 3 — upload in chunks
            offset = 0
            video_id = None

            while offset < media_size:
                chunk      = media_bytes[offset: offset + CHUNK_SIZE]
                chunk_end  = offset + len(chunk) - 1
                content_range = f"bytes {offset}-{chunk_end}/{media_size}"

                upload_r = await client.put(
                    upload_url,
                    content=chunk,
                    headers={
                        **auth_headers,
                        "Content-Type":  "video/*",
                        "Content-Range": content_range,
                    }
                )

                if upload_r.status_code in (200, 201):
                    video_id = upload_r.json().get("id")
                    break
                elif upload_r.status_code == 308:
                    range_hdr = upload_r.headers.get("range", f"bytes=0-{offset - 1}")
                    offset    = int(range_hdr.split("-")[-1]) + 1
                else:
                    return PublishResult(success=False, error=f"YouTube chunk upload failed: {upload_r.text}")

            if not video_id:
                return PublishResult(success=False, error="YouTube upload completed but no video ID returned.")

            return PublishResult(
                success          = True,
                external_post_id = video_id,
                external_url     = f"https://www.youtube.com/watch?v={video_id}",
                api_response     = {"id": video_id, "title": title},
            )
