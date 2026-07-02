"""
Mock publisher — used when ENABLE_<PLATFORM>_REAL=False (default).
Returns realistic fake responses so the full UI flow works without any API keys.
"""
import asyncio
import random
import uuid
from services.publishers.base import BasePublisher, PublishResult

MOCK_URLS = {
    "instagram": "https://www.instagram.com/p/{id}/",
    "facebook":  "https://www.facebook.com/permalink/{id}/",
    "linkedin":  "https://www.linkedin.com/posts/{id}/",
    "youtube":   "https://www.youtube.com/watch?v={id}",
    "x":         "https://x.com/i/web/status/{id}",
}


class MockPublisher(BasePublisher):
    def __init__(self, platform: str):
        self.platform = platform

    async def publish(self, access_token: str, media_url: str, caption: str, extra: dict) -> PublishResult:
        # Simulate realistic upload time
        await asyncio.sleep(random.uniform(1.5, 3.5))

        post_id  = str(uuid.uuid4())[:11].replace("-", "")
        url_tpl  = MOCK_URLS.get(self.platform, "https://example.com/{id}")
        post_url = url_tpl.format(id=post_id)

        return PublishResult(
            success          = True,
            external_post_id = post_id,
            external_url     = post_url,
            api_response     = {
                "mock": True,
                "platform": self.platform,
                "id": post_id,
                "caption": caption[:80],
            },
        )
