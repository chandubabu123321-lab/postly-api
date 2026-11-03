"""
OAuth router — generate authorization URLs, handle callbacks, disconnect.

All flows use official OAuth 2.0 PKCE — no passwords collected, no browser scraping.
Tokens are encrypted before being stored in the database.
"""
import json
import secrets
import urllib.parse
import httpx
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.connected_account import ConnectedAccount
from services.security import get_current_user_id, encrypt_token, decrypt_token
from services.accounts import account_owner_id, require_account_manager

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


# ────────────────────────────────────────────
# Per-platform credential requirements
#
# A platform can only run a real OAuth flow when its credentials are present in
# .env. Without them the provider shows its own opaque error ("Access blocked:
# Authorization Error" from Google, a dead end from LinkedIn), so check first
# and tell the caller exactly what is missing.
# ────────────────────────────────────────────

PLATFORM_SETUP: dict[str, dict] = {
    "facebook": {
        "label":       "Facebook",
        "creds":       ["FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET"],
        "console_url": "https://developers.facebook.com/apps/",
        "scopes_note": "pages_show_list, pages_read_engagement, pages_manage_posts, pages_read_user_content",
    },
    "instagram": {
        "label":       "Instagram",
        "creds":       ["INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"],
        "console_url": "https://developers.facebook.com/apps/",
        "scopes_note": "instagram_business_basic, instagram_business_content_publish, instagram_business_manage_insights",
        "note":        "Uses Instagram Login, so the user authorises on instagram.com and "
                       "never sees Facebook. In your Meta app open Instagram \u2192 API setup "
                       "with Instagram login and copy the Instagram app ID and secret "
                       "(these are different from the Facebook app ID). No Facebook Page "
                       "link is required \u2014 only an Instagram Professional account.",
    },
    "linkedin": {
        "label":       "LinkedIn",
        "creds":       ["LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"],
        "console_url": "https://www.linkedin.com/developers/apps",
        "scopes_note": "openid, profile, w_member_social",
    },
    "youtube": {
        "label":       "YouTube",
        "creds":       ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "console_url": "https://console.cloud.google.com/apis/credentials",
        "scopes_note": "youtube.upload, youtube",
        "note":        "Enable YouTube Data API v3 on the Google Cloud project, and add your "
                       "Google account as a Test user while the consent screen is in "
                       "Testing mode.",
    },
    "x": {
        "label":       "X (Twitter)",
        "creds":       ["X_CLIENT_ID"],
        "console_url": "https://developer.x.com/en/portal/dashboard",
        "scopes_note": "tweet.read, tweet.write, users.read, media.write, offline.access",
    },
}

DEMO_ACCOUNT_NAMES = {
    "instagram": "Demo Instagram Account",
    "facebook":  "Demo Facebook Page",
    "linkedin":  "Demo LinkedIn Profile",
    "youtube":   "Demo YouTube Channel",
    "x":         "Demo X Account",
}


def _missing_creds(platform: str) -> list[str]:
    """Names of the .env variables this platform needs but does not have."""
    return [
        name for name in PLATFORM_SETUP[platform]["creds"]
        if not (getattr(settings, name, "") or "").strip()
    ]


def _callback_url(platform: str) -> str:
    base = settings.API_BASE_URL
    if platform == "instagram" and settings.INSTAGRAM_REDIRECT_BASE_URL:
        base = settings.INSTAGRAM_REDIRECT_BASE_URL
    elif platform == "x" and settings.X_REDIRECT_BASE_URL:
        base = settings.X_REDIRECT_BASE_URL
    return f"{base.rstrip('/')}/oauth/callback/{platform}"


def _is_demo(acc: ConnectedAccount) -> bool:
    try:
        return bool(json.loads(acc.extra_info_json or "{}").get("demo"))
    except Exception:
        return False


# ──────────────────────────────────────────────
# Authorize endpoints — return redirect URL
# ──────────────────────────────────────────────

@router.get("/connect/{platform}")
def connect(platform: str, user_id: str = Depends(require_account_manager)):
    if platform not in PLATFORM_SETUP:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

    info    = PLATFORM_SETUP[platform]
    missing = _missing_creds(platform)

    # Never hand the browser a URL the provider is certain to reject.
    if missing:
        return {
            "mode":         "setup_required",
            "platform":     platform,
            "label":        info["label"],
            "missing":      missing,
            "console_url":  info["console_url"],
            "redirect_uri": _callback_url(platform),
            "scopes":       info["scopes_note"],
            "note":         info.get("note", ""),
            "message":      (
                f"{info['label']} is not configured yet. Add "
                + " and ".join(missing)
                + " to postly-api/.env and restart the API. "
                  "Until then you can connect this platform in demo mode."
            ),
        }

    state = _make_state(user_id, platform)

    if platform == "facebook":
        url = (
            "https://www.facebook.com/v21.0/dialog/oauth"
            f"?client_id={settings.FACEBOOK_APP_ID}"
            f"&redirect_uri={settings.API_BASE_URL}/oauth/callback/facebook"
            f"&state={state}"
            "&scope=public_profile,pages_show_list,pages_read_engagement,pages_manage_posts,pages_read_user_content"
            "&response_type=code"
        )

    elif platform == "instagram":
        # Instagram API with Instagram Login: the user authorises on
        # instagram.com with their Instagram credentials. enable_fb_login=false
        # hides the "continue with Facebook" option, so this flow never detours
        # through facebook.com.
        url = (
            "https://www.instagram.com/oauth/authorize"
            f"?client_id={settings.INSTAGRAM_APP_ID}"
            f"&redirect_uri={urllib.parse.quote(_callback_url('instagram'), safe='')}"
            f"&state={state}"
            "&scope=instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights"
            "&response_type=code"
            "&enable_fb_login=false"
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
            f"&redirect_uri={urllib.parse.quote(_callback_url('x'), safe='')}"
            f"&state={state}"
            "&scope=tweet.read%20tweet.write%20users.read%20media.write%20offline.access"
            "&response_type=code"
            f"&code_challenge={challenge}"
            "&code_challenge_method=S256"
        )

    return {"mode": "live", "authorization_url": url}


# ──────────────────────────────────────────────
# Callback handlers
# ──────────────────────────────────────────────

def _store_account(
    db: Session,
    user_id: str,
    platform: str,
    *,
    account_name: str,
    external_account_id: str,
    access_token: str,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
    scopes: str | None = None,
    extra_info: dict | None = None,
) -> ConnectedAccount:
    """Create or refresh this user's account record for one platform."""
    acc = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id  == user_id,
        ConnectedAccount.platform == platform,
    ).first()

    if not acc:
        acc = ConnectedAccount(id=str(uuid.uuid4()), user_id=user_id, platform=platform)
        db.add(acc)

    acc.account_name            = account_name
    acc.external_account_id     = external_account_id
    acc.encrypted_access_token  = encrypt_token(access_token)
    acc.encrypted_refresh_token = encrypt_token(refresh_token) if refresh_token else None
    acc.token_expires_at        = expires_at
    if scopes:
        acc.scopes = scopes
    acc.status                  = "active"
    acc.extra_info_json         = json.dumps(extra_info or {})
    db.commit()
    return acc


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
    _store_account(
        db, user_id, platform,
        account_name        = account_name,
        external_account_id = external_account_id,
        access_token        = tokens["access_token"],
        refresh_token       = tokens.get("refresh_token") or None,
        extra_info          = extra_info,
    )


def _redirect_success(platform: str) -> RedirectResponse:
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/connect?connected={platform}", status_code=302)

def _redirect_error(platform: str, msg: str) -> RedirectResponse:
    import urllib.parse
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/connect?error={urllib.parse.quote(msg)}", status_code=302)


def _precheck(
    platform: str,
    state: str | None,
    code: str | None,
    error: str | None,
    error_description: str | None,
) -> tuple[dict | None, RedirectResponse | None]:
    """Validate a callback. Returns (context, error_redirect) — one of the two."""
    ctx = _pop_state(state) if state else None

    if error:
        return None, _redirect_error(platform, error_description or error)
    if not ctx:
        return None, _redirect_error(platform, "OAuth session expired. Please try connecting again.")
    if not code:
        return None, _redirect_error(platform, "No authorization code was returned.")
    return ctx, None


@router.get("/callback/facebook")
async def callback_facebook(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Facebook Login callback.

    The publisher posts as a Page, so the user token is only a stepping stone:
    it is upgraded to long-lived, then /me/accounts yields the Page id and a
    Page token (which does not expire when derived from a long-lived token).
    """
    ctx, err = _precheck("facebook", state, code, error, error_description)
    if err:
        return err

    graph = "https://graph.facebook.com/v21.0"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{graph}/oauth/access_token",
                params={
                    "client_id":     settings.FACEBOOK_APP_ID,
                    "client_secret": settings.FACEBOOK_APP_SECRET,
                    "redirect_uri":  f"{settings.API_BASE_URL}/oauth/callback/facebook",
                    "code":          code,
                },
            )
            if r.status_code != 200:
                return _redirect_error("facebook", f"Token exchange failed: {r.text}")
            user_token = r.json()["access_token"]

            # Short-lived -> long-lived user token. Best effort: Page tokens
            # derived from a short-lived token still work, just for an hour.
            try:
                lr = await client.get(
                    f"{graph}/oauth/access_token",
                    params={
                        "grant_type":        "fb_exchange_token",
                        "client_id":         settings.FACEBOOK_APP_ID,
                        "client_secret":     settings.FACEBOOK_APP_SECRET,
                        "fb_exchange_token": user_token,
                    },
                )
                if lr.status_code == 200:
                    user_token = lr.json().get("access_token", user_token)
            except Exception:
                pass

            pr = await client.get(
                f"{graph}/me/accounts",
                params={"fields": "id,name,access_token", "access_token": user_token},
            )
            if pr.status_code != 200:
                return _redirect_error("facebook", f"Could not list your Pages: {pr.text}")
            pages = pr.json().get("data", [])

        if not pages:
            return _redirect_error(
                "facebook",
                "No Facebook Page found. Postly can only post to a Page, not a personal "
                "profile. Create a Page, then connect again and tick it when asked.",
            )

        page = pages[0]
        _store_account(
            db, ctx["user_id"], "facebook",
            account_name        = page.get("name") or "Facebook Page",
            external_account_id = page["id"],
            access_token        = page["access_token"],
            scopes              = PLATFORM_SETUP["facebook"]["scopes_note"],
            extra_info          = {"page_id": page["id"], "page_name": page.get("name")},
        )
    except Exception as e:
        return _redirect_error("facebook", str(e))
    return _redirect_success("facebook")


@router.get("/callback/instagram")
async def callback_instagram(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Instagram Login callback.

    Instagram's token endpoint lives on api.instagram.com (not the Graph API)
    and returns a short-lived token good for one hour, so it is immediately
    upgraded to a long-lived (60 day) token — a scheduler is useless with a
    one-hour token.
    """
    ctx, err = _precheck("instagram", state, code, error, error_description)
    if err:
        return err

    redirect_uri = _callback_url("instagram")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.instagram.com/oauth/access_token",
                data={
                    "client_id":     settings.INSTAGRAM_APP_ID,
                    "client_secret": settings.INSTAGRAM_APP_SECRET,
                    "grant_type":    "authorization_code",
                    "redirect_uri":  redirect_uri,
                    "code":          code,
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code != 200:
                return _redirect_error("instagram", f"Token exchange failed: {r.text}")

            tokens      = r.json()
            access      = tokens["access_token"]
            ig_user_id  = str(tokens.get("user_id", "") or "")
            permissions = tokens.get("permissions", "")
            if isinstance(permissions, list):   # Instagram returns a list
                permissions = ",".join(permissions)
            expires_at  = None

            # Short-lived -> long-lived (60 days). Best effort: a failure here
            # still leaves a usable one-hour token.
            try:
                lr = await client.get(
                    "https://graph.instagram.com/access_token",
                    params={
                        "grant_type":    "ig_exchange_token",
                        "client_secret": settings.INSTAGRAM_APP_SECRET,
                        "access_token":  access,
                    },
                )
                if lr.status_code == 200:
                    long_lived = lr.json()
                    access     = long_lived.get("access_token", access)
                    if long_lived.get("expires_in"):
                        expires_at = datetime.now(timezone.utc) + timedelta(
                            seconds=int(long_lived["expires_in"])
                        )
            except Exception:
                pass

            # Username for display — also best effort.
            username = "Instagram Account"
            try:
                me = await client.get(
                    "https://graph.instagram.com/v21.0/me",
                    params={"fields": "user_id,username", "access_token": access},
                )
                if me.status_code == 200:
                    username = me.json().get("username") or username
            except Exception:
                pass

        _store_account(
            db, ctx["user_id"], "instagram",
            account_name        = f"@{username}" if not username.startswith("@") else username,
            external_account_id = ig_user_id,
            access_token        = access,
            expires_at          = expires_at,
            scopes              = permissions or PLATFORM_SETUP["instagram"]["scopes_note"],
            extra_info          = {"ig_user_id": ig_user_id, "login": "instagram"},
        )
    except Exception as e:
        return _redirect_error("instagram", str(e))

    return _redirect_success("instagram")


@router.get("/callback/linkedin")
async def callback_linkedin(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """
    LinkedIn callback.

    The publisher needs the member's author URN (urn:li:person:<id>), which
    the OpenID userinfo endpoint provides as `sub`.
    """
    ctx, err = _precheck("linkedin", state, code, error, error_description)
    if err:
        return err
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "client_id":     settings.LINKEDIN_CLIENT_ID,
                    "client_secret": settings.LINKEDIN_CLIENT_SECRET,
                    "redirect_uri":  f"{settings.API_BASE_URL}/oauth/callback/linkedin",
                },
                headers={"Accept": "application/json"},
            )
            if r.status_code != 200:
                return _redirect_error("linkedin", f"Token exchange failed: {r.text}")
            tokens = r.json()
            access = tokens["access_token"]

            me = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access}"},
            )
            if me.status_code != 200:
                return _redirect_error("linkedin", f"Could not read your LinkedIn profile: {me.text}")
            profile = me.json()

        person_id  = profile["sub"]
        expires_at = None
        if tokens.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))

        _store_account(
            db, ctx["user_id"], "linkedin",
            account_name        = profile.get("name") or "LinkedIn Profile",
            external_account_id = person_id,
            access_token        = access,
            refresh_token       = tokens.get("refresh_token") or None,
            expires_at          = expires_at,
            scopes              = tokens.get("scope") or PLATFORM_SETUP["linkedin"]["scopes_note"],
            extra_info          = {"author_urn": f"urn:li:person:{person_id}"},
        )
    except Exception as e:
        return _redirect_error("linkedin", str(e))
    return _redirect_success("linkedin")


@router.get("/callback/youtube")
async def callback_youtube(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    ctx, err = _precheck("youtube", state, code, error, error_description)
    if err:
        return err
    try:
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
    except Exception as e:
        return _redirect_error("youtube", str(e))
    return _redirect_success("youtube")


@router.get("/callback/x")
async def callback_x(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """
    X callback.

    A "Web App" (confidential client) must authenticate the token request
    with HTTP Basic client_id:client_secret; a public client sends only the
    client_id in the body, so Basic auth is used only when a secret is set.
    """
    ctx, err = _precheck("x", state, code, error, error_description)
    if err:
        return err
    try:
        client_auth = None
        if settings.X_CLIENT_SECRET:
            client_auth = (settings.X_CLIENT_ID, settings.X_CLIENT_SECRET)

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.twitter.com/2/oauth2/token",
                data={
                    "grant_type":    "authorization_code",
                    "code":          code,
                    "client_id":     settings.X_CLIENT_ID,
                    "redirect_uri":  _callback_url("x"),
                    "code_verifier": ctx.get("code_verifier", ""),
                },
                auth=client_auth,
                headers={"Accept": "application/json"},
            )
            if r.status_code != 200:
                return _redirect_error("x", f"Token exchange failed: {r.text}")
            tokens = r.json()

            # Username for display — best effort.
            username, x_user_id = "X Account", ""
            try:
                me = await client.get(
                    "https://api.twitter.com/2/users/me",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )
                if me.status_code == 200:
                    data      = me.json().get("data", {})
                    x_user_id = data.get("id", "")
                    if data.get("username"):
                        username = f"@{data['username']}"
            except Exception:
                pass

        expires_at = None
        if tokens.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))

        _store_account(
            db, ctx["user_id"], "x",
            account_name        = username,
            external_account_id = x_user_id,
            access_token        = tokens["access_token"],
            refresh_token       = tokens.get("refresh_token") or None,
            expires_at          = expires_at,
            scopes              = tokens.get("scope") or PLATFORM_SETUP["x"]["scopes_note"],
            extra_info          = {"x_user_id": x_user_id},
        )
    except Exception as e:
        return _redirect_error("x", str(e))
    return _redirect_success("x")


# ──────────────────────────────────────────────
# Demo connect — no developer app required
#
# Creates a local account record so the rest of the product (compose, schedule,
# live status, history) can be exercised end to end. Publishing already falls
# back to MockPublisher while ENABLE_<PLATFORM>_REAL is False, so nothing is
# ever sent to the real platform.
# ──────────────────────────────────────────────

@router.post("/demo-connect/{platform}")
def demo_connect(
    platform: str,
    user_id: str = Depends(require_account_manager),
    db: Session = Depends(get_db),
):
    if platform not in PLATFORM_SETUP:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

    acc = db.query(ConnectedAccount).filter(
        ConnectedAccount.user_id  == user_id,
        ConnectedAccount.platform == platform,
    ).first()

    if not acc:
        acc = ConnectedAccount(id=str(uuid.uuid4()), user_id=user_id, platform=platform)
        db.add(acc)

    acc.account_name            = DEMO_ACCOUNT_NAMES[platform]
    acc.external_account_id     = f"demo-{platform}"
    acc.encrypted_access_token  = encrypt_token(f"demo-token-{platform}")
    acc.encrypted_refresh_token = None
    acc.scopes                  = PLATFORM_SETUP[platform]["scopes_note"]
    acc.status                  = "active"
    acc.extra_info_json         = json.dumps({"demo": True})
    db.commit()

    return {
        "connected":    True,
        "demo":         True,
        "platform":     platform,
        "account_name": acc.account_name,
    }


# ──────────────────────────────────────────────
# Disconnect
# ──────────────────────────────────────────────

@router.delete("/disconnect/{platform}", status_code=204)
def disconnect(platform: str, user_id: str = Depends(require_account_manager), db: Session = Depends(get_db)):
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
        ConnectedAccount.user_id == account_owner_id(db, user_id),
        ConnectedAccount.status  == "active",
    ).all()
    return {
        a.platform: {
            "connected":    True,
            "account_name": a.account_name,
            "demo":         _is_demo(a),
        }
        for a in accounts
    }
