from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Postly API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    APP_BASE_URL: str = "http://localhost:8000"
    API_BASE_URL: str = "http://localhost:8000"   # used in OAuth callback URLs
    # Instagram Login only accepts https redirect URIs, so it may need a
    # tunnel URL while the other platforms keep using http://localhost.
    INSTAGRAM_REDIRECT_BASE_URL: str = ""
    # X's developer portal rejects "localhost" callbacks but accepts 127.0.0.1.
    X_REDIRECT_BASE_URL: str = ""
    FRONTEND_URL: str = "http://localhost:5199"

    # Database — SQLite by default, swap one line for Turso/Supabase later
    DATABASE_URL: str = "sqlite:///./postly.db"

    # Security
    SECRET_KEY: str = "change-this-to-a-long-random-secret-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ENCRYPTION_KEY: str = "change-this-32-byte-key-for-prod!!"

    # Redis + Celery (optional — tasks run sync if Redis unavailable)
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Storage — "local" for dev, "s3" or "r2" for production
    STORAGE_PROVIDER: str = "local"
    STORAGE_BUCKET: str = ""
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 500

    # Feature flags — all off until you have real API keys + app review
    ENABLE_INSTAGRAM_REAL: bool = False
    ENABLE_FACEBOOK_REAL: bool = False
    ENABLE_LINKEDIN_REAL: bool = False
    ENABLE_YOUTUBE_REAL: bool = False
    ENABLE_X_REAL: bool = False

    # Meta (Instagram + Facebook)
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_REDIRECT_URI: str = "http://localhost:8000/api/oauth/meta/callback"
    FACEBOOK_APP_ID: str = ""       # alias — set same value as META_APP_ID
    FACEBOOK_APP_SECRET: str = ""   # alias — set same value as META_APP_SECRET

    # Instagram Login (Instagram API with Instagram Login) — these are the
    # "Instagram app ID / secret" from the Instagram product inside the Meta
    # app, NOT the Facebook app ID. They let users authorise on instagram.com
    # instead of being sent to facebook.com.
    INSTAGRAM_APP_ID: str = ""
    INSTAGRAM_APP_SECRET: str = ""

    # LinkedIn
    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = "http://localhost:8000/api/oauth/linkedin/callback"

    # Google (YouTube)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/oauth/google/callback"

    # X / Twitter
    X_CLIENT_ID: str = ""
    X_CLIENT_SECRET: str = ""
    X_REDIRECT_URI: str = "http://localhost:8000/api/oauth/x/callback"

    # AI captions, tried in this order
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
