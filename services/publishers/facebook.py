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
RUPLOAD_URL = "https://rupload.facebook.com/video-upload/v21.0"


class FacebookPublisher(BasePublisher):
    platform = "facebook"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        page_id = extra.get("page_id")
        if not page_id:
            return PublishResult(success=False, error="No Facebook Page ID in connected account.")

        await respectful_delay("facebook")

        is_video  = any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov", ".avi"])
        post_type = extra.get("post_type") or "post"
        if post_type == "reel" and not is_video:
            return PublishResult(success=False, error="A Facebook Reel needs a video file.")

        # Facebook downloads a video from file_url before it answers, so allow time.
        async with httpx.AsyncClient(timeout=300 if is_video else 60) as client:
            if post_type == "story" and not is_video:
                return await self._photo_story(client, access_token, page_id, media_url)
            if post_type == "story":
                return await self._hosted_video(client, access_token, page_id, media_url, "video_stories", {})
            if post_type == "reel":
                return await self._hosted_video(client, access_token, page_id, media_url, "video_reels", {
                    "video_state": "PUBLISHED",
                    "description": caption,
                })

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
            if is_video:   # /videos answers with the video id, not a post id
                post_url = f"https://www.facebook.com/{page_id}/videos/{data.get('id', '')}"
            else:
                post_url = f"https://www.facebook.com/{page_id}/posts/{post_id.split('_')[-1]}"

            return PublishResult(
                success          = True,
                external_post_id = post_id,
                external_url     = post_url,
                api_response     = data,
            )

    async def _photo_story(self, client: httpx.AsyncClient, token: str, page_id: str, media_url: str) -> PublishResult:
        """Upload the photo unpublished, then attach it to a Page story."""
        r = await client.post(f"{GRAPH_URL}/{page_id}/photos",
                              params={"access_token": token, "url": media_url, "published": "false"})
        if r.status_code != 200:
            return PublishResult(success=False, error=f"Facebook story photo failed: {r.text}", api_response=r.json())

        s = await client.post(f"{GRAPH_URL}/{page_id}/photo_stories",
                              params={"access_token": token, "photo_id": r.json().get("id")})
        if s.status_code != 200:
            return PublishResult(success=False, error=f"Facebook story failed: {s.text}", api_response=s.json())

        data = s.json()
        return PublishResult(success=True, external_post_id=data.get("post_id", ""), api_response=data)

    async def _hosted_video(self, client: httpx.AsyncClient, token: str, page_id: str, media_url: str,
                            edge: str, finish_params: dict) -> PublishResult:
        """Reels and video stories: start a session, point Facebook at the file, finish."""
        start = await client.post(f"{GRAPH_URL}/{page_id}/{edge}",
                                  params={"access_token": token, "upload_phase": "start"})
        if start.status_code != 200:
            return PublishResult(success=False, error=f"Facebook {edge} start failed: {start.text}", api_response=start.json())
        video_id = start.json().get("video_id")

        upload = await client.post(f"{RUPLOAD_URL}/{video_id}",
                                   headers={"Authorization": f"OAuth {token}", "file_url": media_url})
        if upload.status_code != 200:
            return PublishResult(success=False, error=f"Facebook {edge} upload failed: {upload.text}")

        finish = await client.post(f"{GRAPH_URL}/{page_id}/{edge}", params={
            "access_token": token, "upload_phase": "finish", "video_id": video_id, **finish_params,
        })
        if finish.status_code != 200:
            return PublishResult(success=False, error=f"Facebook {edge} finish failed: {finish.text}", api_response=finish.json())

        is_reel = edge == "video_reels"
        return PublishResult(
            success          = True,
            external_post_id = finish.json().get("post_id") or video_id,
            external_url     = f"https://www.facebook.com/reel/{video_id}" if is_reel else None,
            api_response     = finish.json(),
        )
