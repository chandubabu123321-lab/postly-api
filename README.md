# Postly API

FastAPI backend for **Postly** — a multi-platform social media scheduling tool.  
Publish to Instagram, Facebook, LinkedIn, YouTube, and X (Twitter) from a single UI.

---

## Architecture

```
postly-api/
├── main.py                        # FastAPI entry point
├── config.py                      # Settings (pydantic-settings, reads .env)
├── database.py                    # SQLAlchemy engine + session
├── models/                        # ORM models
│   ├── user.py
│   ├── connected_account.py       # OAuth accounts per platform
│   ├── media_asset.py             # Uploaded files
│   ├── post_draft.py              # A post + its targets
│   ├── post_target.py             # Per-platform post config
│   ├── publish_job.py             # Execution record per target
│   └── audit_log.py
├── schemas/                       # Pydantic request/response shapes
│   ├── auth.py
│   └── posts.py
├── routers/                       # FastAPI routers
│   ├── auth.py                    # /auth — register, login, /me
│   ├── media.py                   # /media — upload, list
│   ├── posts.py                   # /posts — drafts, publish, history
│   ├── oauth.py                   # /oauth — connect, callbacks, disconnect
│   └── jobs.py                    # /jobs — status, retry
├── services/
│   ├── security.py                # JWT, Fernet encryption, password hash
│   ├── storage.py                 # File upload (local / S3 / R2)
│   ├── rate_limiter.py            # Per-platform delays, backoff, jitter
│   └── publishers/
│       ├── base.py                # BasePublisher + PublishResult
│       ├── mock.py                # Mock publisher (default — no API keys needed)
│       ├── instagram.py           # Instagram Graph API
│       ├── facebook.py            # Facebook Pages API
│       ├── linkedin.py            # LinkedIn UGC Posts API
│       ├── youtube.py             # YouTube Data API v3 (resumable upload)
│       └── twitter.py             # X API v2 (PKCE, chunked media)
└── workers/
    └── tasks.py                   # Celery tasks (falls back to sync if Redis absent)
```

### Database

SQLite by default (`postly.db`, zero setup). Swap one line in `.env` for [Turso](https://turso.tech/) (serverless SQLite) or any SQLAlchemy-compatible database:

```
DATABASE_URL=libsql://your-db.turso.io?authToken=TOKEN
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- Node 18+ (for the frontend)
- Git

### 1 — Clone and enter the directory

```bash
cd /Applications/XAMPP/xamppfiles/htdocs/postly-api
```

### 2 — Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
```

### 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### 4 — Configure environment

```bash
cp .env.example .env
```

Edit `.env` — the only values you **must** change for local testing:

```env
SECRET_KEY=<any long random string>
ENCRYPTION_KEY=<32-byte Fernet key — generate below>
```

Generate keys:

```bash
# SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# ENCRYPTION_KEY (must be a valid Fernet key)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5 — Run the server

```bash
python main.py
# or:
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000/docs` to see the interactive API docs (Swagger UI).

---

## Running the Frontend

```bash
cd /Applications/XAMPP/xamppfiles/htdocs/postly
npm install
npm run dev      # starts on http://localhost:5199
```

---

## Platform Feature Flags

By default **all platforms use the mock publisher** — no API keys required.  
The mock returns realistic fake post IDs and URLs so you can test the full UI flow.

To enable real publishing for a platform, set the flag in `.env` **and** fill in the platform credentials:

```env
ENABLE_INSTAGRAM_REAL=True
META_APP_ID=your_meta_app_id
META_APP_SECRET=your_meta_app_secret
FACEBOOK_APP_ID=your_meta_app_id       # same value
FACEBOOK_APP_SECRET=your_meta_app_secret
```

---

## Connecting Social Accounts (OAuth)

All connections use official OAuth 2.0 PKCE — no passwords are ever collected.

### Flow

1. Your app calls `GET /oauth/connect/{platform}` → returns `{"authorization_url": "..."}`
2. The user visits the URL in their browser and approves access.
3. The platform redirects to `GET /oauth/callback/{platform}?code=...`
4. Postly exchanges the code for tokens, encrypts them with Fernet, and stores them in the database.
5. `GET /oauth/status` confirms which platforms are connected.

### Platform Developer Apps Required

| Platform | Create app at | Required scopes |
|----------|--------------|-----------------|
| Instagram / Facebook | [developers.facebook.com](https://developers.facebook.com/) | `pages_manage_posts` `instagram_content_publish` |
| LinkedIn | [linkedin.com/developers](https://www.linkedin.com/developers/) | `openid` `profile` `w_member_social` |
| YouTube | [console.cloud.google.com](https://console.cloud.google.com/) | `youtube.upload` |
| X (Twitter) | [developer.x.com](https://developer.x.com/) | `tweet.read` `tweet.write` `users.read` `offline.access` |

> **Testing note:** Platform APIs require app review before publishing on behalf of other users. During development, add test users in each platform's developer console.

---

## Security Design

| Concern | Implementation |
|---------|---------------|
| OAuth tokens at rest | Fernet symmetric encryption (AES-128-CBC + HMAC) |
| Passwords | bcrypt via passlib |
| Auth | JWT HS256, 7-day expiry |
| Token exposure | Tokens never appear in API responses, logs, or browser storage |
| Rate limiting | Per-platform delays + jitter + exponential backoff — posts look organic |
| Detection avoidance | Official APIs only. No browser scraping, no session injection |

---

## Rate Limiting

`services/rate_limiter.py` enforces per-platform minimum intervals with random jitter:

| Platform | Min interval | Notes |
|----------|-------------|-------|
| Instagram | 2 s | + 0–1 s jitter |
| Facebook | 1.5 s | + 0–1 s jitter |
| LinkedIn | 3 s | + 0–1 s jitter |
| YouTube | 5 s | + 0–1 s jitter |
| X | 2 s | + 0–1 s jitter |

On `429 Too Many Requests`, exponential backoff kicks in (1 s → 2 → 4 → … → 64 s max).

---

## Background Jobs (optional)

Publish jobs run **synchronously** by default — no Redis needed.

To enable background processing with Celery:

```bash
# Install Redis (macOS)
brew install redis
brew services start redis

# Start Celery worker (in a separate terminal, venv active)
celery -A workers.tasks worker --loglevel=info
```

Set in `.env`:

```env
REDIS_URL=redis://localhost:6379/0
```

When Redis is running, publish jobs are queued and the API returns immediately.  
When Redis is absent, jobs run inline and the response waits for completion.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Create account |
| POST | `/auth/login` | Login → JWT |
| GET  | `/auth/me` | Current user |
| POST | `/media/upload` | Upload image/video |
| GET  | `/media/` | List your assets |
| POST | `/posts/drafts` | Create a draft |
| GET  | `/posts/drafts` | List drafts |
| GET  | `/posts/drafts/{id}` | Get draft + targets |
| POST | `/posts/publish/{id}` | Publish a draft |
| GET  | `/posts/history` | Published post history |
| GET  | `/oauth/connect/{platform}` | Get auth URL |
| GET  | `/oauth/callback/{platform}` | OAuth callback |
| DELETE | `/oauth/disconnect/{platform}` | Revoke connection |
| GET  | `/oauth/status` | Connected platforms |
| GET  | `/jobs/draft/{draft_id}` | Job status per draft |
| POST | `/jobs/retry/{job_id}` | Retry failed job |
| GET  | `/health` | Health check |

Full interactive docs: `http://localhost:8000/docs`

---

## Upgrading Storage to S3 / R2

1. Set `STORAGE_PROVIDER=s3` (or `r2`) in `.env`
2. Add `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `STORAGE_BUCKET`
3. `services/storage.py` is already structured to dispatch — add the S3 branch there

---

## Production Checklist

- [ ] Set `DEBUG=False`
- [ ] Use a strong `SECRET_KEY` and `ENCRYPTION_KEY`
- [ ] Switch `DATABASE_URL` to Turso / PostgreSQL
- [ ] Set `STORAGE_PROVIDER=s3` or `r2` with a CDN in front
- [ ] Run behind HTTPS (Nginx / Caddy / Cloudflare Tunnel)
- [ ] Enable Redis + Celery for async job processing
- [ ] Complete platform app reviews before enabling `ENABLE_*_REAL=True`
- [ ] Store `.env` in a secrets manager (AWS Secrets Manager, Doppler, etc.)

---

## License

MIT — see LICENSE.
