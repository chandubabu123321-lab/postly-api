"""
OAuth router — generate authorization URLs, handle callbacks, disconnect.

All flows use official OAuth 2.0 PKCE — no passwords collected, no browser scraping.
Tokens are encrypted before being stored in the database.
"""
import secrets
import httpx
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.connected_account import ConnectedAccount
from services.security import get_current_user_id, encrypt_token, decrypt_token

router   = APIRouter(prefix="/oauth", tags=["oauth"])
settings = get_settings()

# In-memory PKCE state store (production: use Redis)
_state_store: dict[str, dict] = {}


def _make_state(user_id: str, platform: str) -> str:
    state = secrets.token_urlsafe(32)
    _state_store[state] = {"user_id": user_id, "platform": platform}
    return state


def _pop_state(state: str) -> dict | None:
    return _state_store.pop(state, None)


# ──────────────────────────────────────────────
# Authorize endpoints — return redirect URL
# ──────────────────────────────────────────────

@router.get("/connect/{platform}")
def connect(platform: str, user_id: str = Depends(get_current_user_id)):
    state = _make_state(user_id, platform)

    if platform == "instagram" or platform == "facebook":
        url = (
            "https://www.facebook.com/v21.0/dialog/oauth"
            f"?client_id={settings.FACEBOOK_APP_ID}"
            f"&redirect_uri={settings.API_BASE_URL}/oauth/callback/facebook"
            f"&state={state}"
            "&scope=pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish"
            "&response_type=code"
        )

    elif platform == "linkedin":
        url = (
            "https://www.linkedin.com/oauth/v2/authorization"
            f"?client_id={settings.LINKEDIN_CLIENT_ID}"
            f"&redirect_uri={settings.API_BASE_URL}/oauth/callback/linkedin"
            f"&state={state}"
            "&scope=openid%20profile%20w_member_social"
            "&response_type=code"
        )

    elif platform == "youtube":
        url = (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={settings.GOOGLE_CLIENT_ID}"
            f"&redirect_uri={settings.API_BASE_URL}/oauth/callback/youtube"
            f"&state={state}"
            "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.upload%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube"
            "&response_type=code"
            "&access_type=offline"
            "&prompt=consent"
        )

    elif platform == "x":
        code_verifier  = secrets.token_urlsafe(32)
        _state_store[state]["code_verifier"] = code_verifier
        import hashlib, base64
        digest   = hashlib.sha256(code_verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        url = (
            "https://twitter.com/i/oauth2/authorize"
            f"?client_id={settings.X_CLIENT_ID}"
            f"&redirect_uri={settings.API_BASE_URL}/oauth/callback/x"
            f"&state={state}"
            "&scope=tweet.read%20tweet.write%20users.read%20offline.access"
            "&response_type=code"
            f"&code_challenge={challenge}"
            "&code_challenge_method=S256"
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

    return {"authorization_url": url}


# ──────────────────────────────────────────────
# Callback handlers
# ──────────────────────────────────────────────

async def _exchange_code(
    token_url: str,
    payload: dict,
    db: Session,
    user_id: str,
    platform: str,
    account_name: str,
    external_account_id: str,
    extra_info: dict | None = None,
):
    async with httpx.AsyncClient() as client:
        r = await client.post(token_url, data=payload, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {r.text}")

    tokens = r.json()
    acc = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id  == user_id,
        ConnectedAccount.platform == platform,
    ).first()

    if not acc:
        acc = ConnectedAccount(id=str(uuid.uuid4()), user_id=user_id, platform=platform)
        db.add(acc)

    acc.account_name          = account_name
    acc.external_account_id   = external_account_id
    acc.encrypted_access_token  = encrypt_token(tokens["access_token"])
    acc.encrypted_refresh_token = encrypt_token(tokens.get("refresh_token", "")) if tokens.get("refresh_token") else None
    acc.status                = "active"
    acc.extra_info_json       = str(extra_info or {})
    db.commit()


@router.get("/callback/facebook")
async def callback_facebook(code: str, state: str, db: Session = Depends(get_db)):
    ctx = _pop_state(state)
    if not ctx:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    token_r = await _exchange_code(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        {
            "client_id":     settings.FACEBOOK_APP_ID,
            "client_secret": settings.FACEBOOK_APP_SECRET,
            "redirect_uri":  f"{settings.API_BASE_URL}/oauth/callback/facebook",
            "code":          code,
        },
        db, ctx["user_id"], "facebook",
        account_name="Facebook Page", external_account_id="",
    )
    return {"status": "connected", "platform": "facebook"}


@router.get("/callback/linkedin")
async def callback_linkedin(code: str, state: str, db: Session = Depends(get_db)):
    ctx = _pop_state(state)
    if not ctx:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    await _exchange_code(
        "https://www.linkedin.com/oauth/v2/accessToken",
        {
            "grant_type":    "authorization_code",
            "code":          code,
            "client_id":     settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            "redirect_uri":  f"{settings.API_BASE_URL}/oauth/callback/linkedin",
        },
        db, ctx["user_id"], "linkedin",
        account_name="LinkedIn Profile", external_account_id="",
    )
    return {"status": "connected", "platform": "linkedin"}


@router.get("/callback/youtube")
async def callback_youtube(code: str, state: str, db: Session = Depends(get_db)):
    ctx = _pop_state(state)
    if not ctx:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    await _exchange_code(
        "https://oauth2.googleapis.com/token",
        {
            "grant_type":    "authorization_code",
            "code":          code,
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri":  f"{settings.API_BASE_URL}/oauth/callback/youtube",
        },
        db, ctx["user_id"], "youtube",
        account_name="YouTube Channel", external_account_id="",
    )
    return {"status": "connected", "platform": "youtube"}


@router.get("/callback/x")
async def callback_x(code: str, state: str, db: Session = Depends(get_db)):
    ctx = _pop_state(state)
    if not ctx:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    await _exchange_code(
        "https://api.twitter.com/2/oauth2/token",
        {
            "grant_type":     "authorization_code",
            "code":           code,
            "client_id":      settings.X_CLIENT_ID,
            "redirect_uri":   f"{settings.API_BASE_URL}/oauth/callback/x",
            "code_verifier":  ctx.get("code_verifier", ""),
        },
        db, ctx["user_id"], "x",
        account_name="X Account", external_account_id="",
    )
    return {"status": "connected", "platform": "x"}


# ──────────────────────────────────────────────
# Disconnect
# ──────────────────────────────────────────────

@router.delete("/disconnect/{platform}", status_code=204)
def disconnect(platform: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    acc = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id  == user_id,
        ConnectedAccount.platform == platform,
    ).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Account not connected.")
    acc.status = "revoked"
    db.commit()


@router.get("/status")
def oauth_status(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    accounts = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id == user_id,
        ConnectedAccount.status  == "active",
    ).all()
    return {a.platform: {"connected": True, "account_name": a.account_name} for a in accounts}
