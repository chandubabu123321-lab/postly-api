"""Base publisher interface. Every platform publisher inherits from this."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class PublishResult:
    success: bool
    external_post_id: Optional[str] = None
    external_url: Optional[str]     = None
    api_response: Optional[dict]    = None
    error: Optional[str]            = None


class BasePublisher(ABC):
    platform: str = ""

    @abstractmethod
    async def publish(
        self,
        access_token: str,
        media_url: str,
        caption: str,
        extra: dict,
    ) -> PublishResult:
        """
        Publish content to the platform.

        Args:
            access_token: Decrypted OAuth access token.
            media_url:    Public URL of the media file.
            caption:      Platform-specific caption / text.
            extra:        Platform-specific extras (title, privacy, page_id, etc.)

        Returns:
            PublishResult with success flag, post ID, URL, and raw API response.
        """
        ...
