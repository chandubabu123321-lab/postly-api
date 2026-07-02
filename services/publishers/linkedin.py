"""
LinkedIn publisher — LinkedIn Posts API.

Flow (image/video):
  1. Register upload → get uploadUrl + asset URN
  2. PUT binary to uploadUrl
  3. Create post referencing asset URN

Text-only: skip steps 1-2.

Docs: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
"""
import httpx
import logging
from services.publishers.base import BasePublisher, PublishResult
from services.rate_limiter import respectful_delay, parse_rate_limit_headers

logger = logging.getLogger(__name__)

API = "https://api.linkedin.com/rest"
HEADERS = {
    "LinkedIn-Version": "202411",
    "Content-Type": "application/json",
    "X-Restli-Protocol-Version": "2.0.0",
}


class LinkedInPublisher(BasePublisher):
    platform = "linkedin"

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        author_urn = extra.get("author_urn")   # urn:li:person:<id> or urn:li:organization:<id>
        if not author_urn:
            return PublishResult(success=False, error="No LinkedIn author URN in connected account.")

        await respectful_delay("linkedin")

        auth_headers = {**HEADERS, "Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=60) as client:
            media_urn = None

            if media_url:
                # Step 1 — register upload
                is_video = any(media_url.lower().endswith(ext) for ext in [".mp4", ".mov"])
                recipe   = "urn:li:digitalmediaRecipe:feedshare-video" if is_video else "urn:li:digitalmediaRecipe:feedshare-image"

                reg_r = await client.post(f"{API}/assets?action=registerUpload", headers=auth_headers, json={
                    "registerUploadRequest": {
                        "owner": author_urn,
                        "recipes": [recipe],
                        "serviceRelationships": [{"identifier": "urn:li:userGeneratedContent", "relationshipType": "OWNER"}],
                    }
                })
                parse_rate_limit_headers(dict(reg_r.headers), "linkedin")

                if reg_r.status_code != 200:
                    return PublishResult(success=False, error=f"LinkedIn upload registration failed: {reg_r.text}")

                reg_data   = reg_r.json()
                upload_url = reg_data["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
                media_urn  = reg_data["value"]["asset"]

                # Step 2 — download media and PUT to LinkedIn
                media_r  = await client.get(media_url)
                await client.put(upload_url, content=media_r.content,
                                 headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/octet-stream"})

            # Step 3 — create post
            post_body: dict = {
                "author":          author_urn,
                "lifecycleState":  "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": caption},
                        "shareMediaCategory": "NONE" if not media_urn else ("VIDEO" if "video" in (media_urn or "") else "IMAGE"),
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            }

            if media_urn:
                post_body["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
                    {"status": "READY", "media": media_urn}
                ]

            post_r = await client.post(f"{API}/ugcPosts", headers=auth_headers, json=post_body)

            if post_r.status_code not in (200, 201):
                return PublishResult(success=False, error=f"LinkedIn post failed: {post_r.text}", api_response=post_r.json())

            post_id  = post_r.headers.get("x-restli-id", "")
            post_url = f"https://www.linkedin.com/feed/update/{post_id}/"

            return PublishResult(
                success          = True,
                external_post_id = post_id,
                external_url     = post_url,
                api_response     = {"id": post_id},
            )
