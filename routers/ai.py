"""AI router — generate captions and hashtags from a post's picture."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.ai_captions import PLATFORM_STYLE, ProviderError, generate_captions
from services.security import get_current_user_id

router = APIRouter(prefix="/ai", tags=["ai"])

MAX_IMAGE_B64_CHARS = 6 * 1024 * 1024   # the browser sends a downscaled JPEG


class CaptionRequest(BaseModel):
    master_caption: str = ""
    platforms: List[str]
    image_base64: Optional[str] = None


@router.post("/captions")
async def captions(body: CaptionRequest, user_id: str = Depends(get_current_user_id)):
    platforms = [p for p in body.platforms if p in PLATFORM_STYLE]
    if not platforms:
        raise HTTPException(status_code=400, detail="Select at least one platform.")
    if not body.image_base64 and not body.master_caption.strip():
        raise HTTPException(status_code=400, detail="Add a picture or a caption first.")
    if body.image_base64 and len(body.image_base64) > MAX_IMAGE_B64_CHARS:
        raise HTTPException(status_code=413, detail="Picture is too large.")

    try:
        return await generate_captions(body.master_caption.strip(), platforms, body.image_base64)
    except ProviderError as e:
        raise HTTPException(status_code=502, detail=f"All AI providers failed. {e}")
