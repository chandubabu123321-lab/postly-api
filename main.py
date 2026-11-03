"""Postly API — FastAPI entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from config import get_settings
from database import create_tables

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    logger.info("Database tables ready.")
    os.makedirs(get_settings().UPLOAD_DIR, exist_ok=True)
    logger.info("Upload directory: %s", get_settings().UPLOAD_DIR)
    from services.scheduler import scheduler_loop
    scheduler = asyncio.create_task(scheduler_loop())
    yield
    scheduler.cancel()
    logger.info("Postly API shutting down.")


settings = get_settings()

app = FastAPI(
    title       = "Postly API",
    description = "Multi-platform social media scheduler backend.",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins     = [
        "http://localhost:5199",
        "http://localhost:5200",
        "http://localhost:5201",
        "http://localhost:5202",
        "http://localhost:5173",
        settings.FRONTEND_URL,
    ],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# Serve uploaded files
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR, check_dir=False), name="uploads")

# Routers
from routers.auth  import router as auth_router
from routers.media import router as media_router
from routers.posts import router as posts_router
from routers.oauth import router as oauth_router
from routers.jobs  import router as jobs_router
from routers.ai    import router as ai_router
from routers.admin import router as admin_router

app.include_router(ai_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(media_router)
app.include_router(posts_router)
app.include_router(oauth_router)
app.include_router(jobs_router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
