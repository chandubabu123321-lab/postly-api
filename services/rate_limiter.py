"""
Per-platform rate limiter with exponential backoff + jitter.
Prevents platforms from flagging our app as a bot or bulk-poster.

Platform limits (approximate, check developer docs for latest):
  Instagram  → 200 calls/hour per token
  Facebook   → 200 calls/hour per token
  LinkedIn   → 100 calls/day for sharing
  YouTube    → 10,000 units/day quota
  X          → 300 tweets/3 hours per user
"""
import asyncio
import random
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Seconds between publish attempts per platform (conservative defaults)
PLATFORM_INTERVALS = {
    "instagram": 2.0,
    "facebook":  1.5,
    "linkedin":  3.0,
    "youtube":   5.0,
    "x":         2.0,
}

# Max backoff in seconds
MAX_BACKOFF = 64


async def respectful_delay(platform: str):
    """Wait the platform-appropriate interval before making an API call."""
    interval = PLATFORM_INTERVALS.get(platform, 2.0)
    jitter   = random.uniform(0.1, 0.5)   # small random jitter so calls don't look robotic
    await asyncio.sleep(interval + jitter)


async def backoff_retry(platform: str, attempt: int):
    """
    Exponential backoff after a failed attempt.
    attempt=1 → ~2s, attempt=2 → ~4s, attempt=3 → ~8s, capped at MAX_BACKOFF.
    """
    wait = min(MAX_BACKOFF, (2 ** attempt)) + random.uniform(0, 1)
    logger.info(f"[{platform}] backoff attempt {attempt} — waiting {wait:.1f}s")
    await asyncio.sleep(wait)


def parse_rate_limit_headers(headers: dict, platform: str) -> int | None:
    """
    Read X-RateLimit-Remaining (or platform-specific variant) from response headers.
    Returns remaining calls, or None if header not present.
    """
    checks = [
        "x-ratelimit-remaining",
        "x-rate-limit-remaining",
        "ratelimit-remaining",
    ]
    for key in checks:
        val = headers.get(key) or headers.get(key.upper())
        if val is not None:
            try:
                remaining = int(val)
                if remaining < 10:
                    logger.warning(f"[{platform}] Rate limit low: {remaining} calls remaining")
                return remaining
            except ValueError:
                pass
    return None
