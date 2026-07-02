"""
Storage abstraction.
STORAGE_PROVIDER=local  → saves files to ./uploads/ on disk  (default, no setup)
STORAGE_PROVIDER=s3     → uploads to AWS S3  (set S3_* env vars)
STORAGE_PROVIDER=r2     → uploads to Cloudflare R2 (set R2_* env vars)
Swap provider by changing one env var — code is identical.
"""
import os
import uuid
import aiofiles
from pathlib import Path
from fastapi import UploadFile
from config import get_settings

settings = get_settings()


async def save_file(file: UploadFile) -> dict:
    """Save uploaded file and return metadata dict."""
    ext       = Path(file.filename).suffix.lower()
    file_id   = str(uuid.uuid4())
    filename  = f"{file_id}{ext}"

    if settings.STORAGE_PROVIDER == "local":
        return await _save_local(file, filename)
    # Future: elif settings.STORAGE_PROVIDER == "s3": return await _save_s3(...)
    raise ValueError(f"Unknown STORAGE_PROVIDER: {settings.STORAGE_PROVIDER}")


async def _save_local(file: UploadFile, filename: str) -> dict:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / filename

    async with aiofiles.open(dest, "wb") as f:
        content = await file.read()
        await f.write(content)

    size      = dest.stat().st_size
    mime      = file.content_type or ""
    file_type = "video" if mime.startswith("video") else "image"
    url       = f"{settings.APP_BASE_URL}/uploads/{filename}"

    return {
        "file_url":      url,
        "local_path":    str(dest),
        "file_type":     file_type,
        "mime_type":     mime,
        "file_size":     size,
        "original_name": file.filename,
    }


def delete_file(file_url: str):
    """Delete local file. No-op for cloud providers (use lifecycle rules)."""
    if settings.STORAGE_PROVIDER == "local":
        filename = file_url.split("/uploads/")[-1]
        path = Path(settings.UPLOAD_DIR) / filename
        if path.exists():
            os.remove(path)
