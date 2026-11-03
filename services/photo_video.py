"""
Photo to video — YouTube and Reels only accept videos, so a photo becomes a
short still video before upload: the photo fitted onto a 1080p frame (vertical
for a Short or Reel, wide for a normal video) with silent audio, using the ffmpeg binary that
the imageio-ffmpeg package ships.
"""
import asyncio
import logging
from pathlib import Path

import imageio_ffmpeg

logger = logging.getLogger(__name__)

SECONDS = 10
FRAME = {"short": (1080, 1920), "reel": (1080, 1920), "post": (1920, 1080)}


_locks: dict[Path, asyncio.Lock] = {}


async def photo_to_video(photo: Path, post_type: str) -> Path:
    """Returns the video path, reusing it if this photo was converted before."""
    width, height = FRAME.get(post_type, FRAME["post"])
    out = photo.with_name(f"{photo.stem}-video-{width}x{height}.mp4")
    # One post can need the same video twice at once (an Instagram and a
    # Facebook Reel), so only one conversion per output file runs.
    async with _locks.setdefault(out, asyncio.Lock()):
        if not out.exists():
            await _convert(photo, out, width, height)
    return out


async def _convert(photo: Path, out: Path, width: int, height: int) -> None:
    fit = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p")
    tmp = out.with_suffix(".part.mp4")
    proc = await asyncio.create_subprocess_exec(
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(photo),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(SECONDS), "-vf", fit, "-r", "30",
        "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-shortest",
        str(tmp),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Could not turn the photo into a video: {err.decode(errors='ignore')[-300:]}")
    tmp.replace(out)
    logger.info("made %s from %s", out.name, photo.name)
