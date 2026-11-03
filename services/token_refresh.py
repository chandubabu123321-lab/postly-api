"""
OAuth token refresh — called by the publisher worker before each publish.

Google (YouTube) access tokens last one hour and X tokens two hours, so a
scheduler that never refreshes fails every post made after that. Both
platforms hand out a refresh token at connect time; this swaps it for a new
access token when the stored one is expired, about to expire, or of unknown
age. Platforms without a refresh flow here are left untouched.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from config import get_settings
from services.security import decrypt_token, encrypt_token

logger   = logging.getLogger(__name__)
settings = get_settings()

REFRESH_MARGIN = timedelta(minutes=5)


def _needs_refresh(account) -> bool:
    if not account.encrypted_refresh_token:
        return False
    expires_at = account.token_expires_at
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:            # SQLite returns naive UTC
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at - datetime.now(timezone.utc) < REFRESH_MARGIN


async def _refresh_youtube(client: httpx.AsyncClient, refresh_token: str) -> httpx.Response:
    return await client.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
        },
    )


async def _refresh_x(client: httpx.AsyncClient, refresh_token: str) -> httpx.Response:
    auth = (settings.X_CLIENT_ID, settings.X_CLIENT_SECRET) if settings.X_CLIENT_SECRET else None
    return await client.post(
        "https://api.x.com/2/oauth2/token",
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     settings.X_CLIENT_ID,
        },
        auth=auth,
    )


REFRESHERS = {
    "youtube": _refresh_youtube,
    "x":       _refresh_x,
}


async def ensure_fresh_token(account, db) -> str | None:
    """
    Return a usable access token for `account`, refreshing it first if needed.
    Returns None when a refresh was required but failed (the caller should
    report that the account must be reconnected).
    """
    current = decrypt_token(account.encrypted_access_token) if account.encrypted_access_token else ""

    refresher = REFRESHERS.get(account.platform)
    if not refresher or not _needs_refresh(account):
        return current

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await refresher(client, decrypt_token(account.encrypted_refresh_token))
    except Exception as e:
        logger.error("[%s] token refresh error: %s", account.platform, e)
        return None

    if r.status_code != 200:
        logger.error("[%s] token refresh failed: %s %s", account.platform, r.status_code, r.text)
        return None

    tokens = r.json()
    account.encrypted_access_token = encrypt_token(tokens["access_token"])
    if tokens.get("refresh_token"):          # X rotates refresh tokens
        account.encrypted_refresh_token = encrypt_token(tokens["refresh_token"])
    if tokens.get("expires_in"):
        account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))
    db.commit()
    logger.info("[%s] access token refreshed", account.platform)
    return tokens["access_token"]
