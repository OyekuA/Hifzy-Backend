# Hifzy — Backend

The API server powering Hifzy, an offline-first Quran memorisation app built on spaced repetition.

---

## What is Hifzy?

Hifzy helps you memorise and revise the Quran using spaced repetition — the same method behind Anki. Each verse becomes a flashcard. You listen to the audio, attempt the recitation, and grade yourself. The algorithm (FSRS) schedules the next review based on how well you remembered. The app works fully offline; your data syncs to the server whenever you're back online. Your activity also flows into your Quran.com profile — streaks, reading sessions, and goals update automatically.

---

## How the backend fits in

This repo is the FastAPI server. The frontend (Next.js) lives in a separate repo. The backend handles:

- **Auth** — OAuth2 PKCE login via Quran Foundation; issues short-lived JWTs to the frontend
- **Sync** — implements the WatermelonDB pull/push protocol so the frontend can work offline
- **Content** — proxies and caches Arabic verse text and audio URLs from the QF Content API
- **Bridge** — after every sync push, derives reading sessions, activity days, and streak reads from the review logs and sends them to the QF User API asynchronously

---

## Quran Foundation API integration

### Auth — OAuth2 PKCE

| | |
|---|---|
| **Server** | `https://prelive-oauth2.quran.foundation` |
| **Endpoints** | `GET /oauth2/auth` (redirect), `POST /oauth2/token` (exchange) |
| **Scopes** | `openid offline_access goal reading_session activity_day streak.read` |

Users log in with their Quran.com account. PKCE is used so the code verifier never leaves the server and no client secret is exposed to the browser. After login, the backend issues a one-time exchange code to the frontend, which trades it for a signed JWT.

### Content API — client credentials

| | |
|---|---|
| **Base** | `https://apis-prelive.quran.foundation/content/api/v4` |
| **Auth** | Client credentials (server-to-server, no user token needed) |

| Endpoint | Purpose |
|---|---|
| `GET /verses/by_range` | Arabic text (`text_uthmani`) for a verse range |
| `GET /recitations/{id}/by_chapter/{chapter}` | Per-verse audio file URLs |
| `GET /chapters` | Surah list with Arabic and English names |
| `GET /resources/recitations` | Available reciters |
| `GET /verses/random` | Daily verse fetch — gets random verse metadata; translation fetched separately via /verses/by_key |

Results are cached in Postgres. Audio URLs are considered stale after 7 days and re-fetched on the next request.

### User API — per-user access token

| | |
|---|---|
| **Base** | `https://apis-prelive.quran.foundation` |
| **Auth** | `x-auth-token: <access_token>` + `x-client-id: <client_id>` headers |

| Endpoint | What it does |
|---|---|
| `POST /auth/v1/reading-sessions` | Records the last verse reviewed in a session |
| `POST /auth/v1/activity-days` | Records verse ranges reviewed on a given date, with duration |
| `GET /auth/v1/streaks` | Reads back the user's current streak count (cached locally) |
| `POST /auth/v1/goals` | Creates a Quran range goal on Quran.com |
| `PUT /auth/v1/goals/{id}` | Updates an existing goal |

Every sync push triggers these calls asynchronously via an outbox — a QF API failure never blocks the sync response.

---

## Architecture

**Outbox pattern** — When the frontend pushes review logs, the sync handler writes bridge events (`reading_session`, `activity_day`, `streak_read`) to a `bridge_outbox` table before returning `200`. A background task then processes them. If QF is down, the outbox retries with exponential backoff (capped at 1 hour). Stale `processing` rows are reclaimed every 60 seconds by a sweep loop started at server startup.

**WatermelonDB sync** — Pull returns all records changed since `lastPulledAt` using a monotonic Postgres sequence (`server_version_seq`). Push upserts records and enforces ownership — you cannot modify another user's records. The response timestamp is the sequence value snapshotted at the start of the pull, not a wall clock time.

**Verse cache** — Arabic text and audio URLs are stored in `cached_verses`. A cache miss fetches from the QF Content API and stores the result. Audio URLs are re-fetched if older than 7 days.

**Daily verse** — `GET /content/daily-verse` returns a random verse once per UTC day. On the first request of the day, the backend performs a two-step fetch: first `GET /verses/random` on the QF Content API to obtain random verse metadata (no translation), then `GET /verses/by_key` with the resulting `verse_key` and the configurable `translation_id` (default `85` = M.A.S. Abdel Haleem) to retrieve the English translation. The combined result is cached in the `daily_verse` table. All subsequent requests that day return the cached row — no external call needed. The verse and its English translation are stored together; the first `translation_id` used each day wins. The response includes `tafsir_url` (`https://quran.com/{chapter}/{verse}/tafsirs`) so the frontend can link users directly to Quran.com for tafsir — solving the out-of-context problem for mid-passage verses. If today's cached verse has no translation (e.g. due to a transient QF API failure on the first request), any subsequent request that day will automatically attempt to back-fill the translation before returning the cached row.

**Token refresh** — Before any User API call, `token_service.get_valid_token` checks whether the stored access token is still valid. If not, it uses the refresh token to get a new one and updates the DB. If the refresh token is also expired, the outbox row is marked `failed`.

---

## Tech stack

| Layer | Technology |
|---|---|
| Framework | FastAPI (Python) |
| Database | PostgreSQL — async via SQLAlchemy + asyncpg |
| Migrations | Alembic |
| Auth | OAuth2 PKCE + HS256 JWT (`python-jose`) |
| HTTP client | httpx (async) |
| Settings | pydantic-settings |

---

## Local setup

```bash
# 1. Clone
git clone <repo-url>
cd Quran-BE

# 2. Configure
cp .env.example .env
# Edit .env — see the table below

# 3. Install
pip install -r requirements.txt

# 4. Migrate
alembic upgrade head

# 5. Run
fastapi dev app/main.py
# or: uvicorn app.main:app --reload

# 6. Explore
open http://localhost:8000/docs
```

---

## Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (e.g. `postgresql+asyncpg://user:pass@host/db`) |
| `JWT_SECRET` | Secret used to sign JWTs issued to the frontend — keep this random and private |
| `QF_CLIENT_ID` | Your app's client ID from Quran Foundation |
| `QF_CLIENT_SECRET` | Your app's client secret (used for content API client credentials) |
| `QF_REDIRECT_URI` | Must match exactly what's registered with QF (e.g. `https://your-backend/auth/callback`) |
| `FRONTEND_URL` | Where to redirect after OAuth completes (e.g. `http://localhost:3000`) |
| `CORS_ORIGINS` | Comma-separated list of allowed origins |
| `QF_AUTH_BASE_URL` | QF OAuth2 server base — prelive: `https://prelive-oauth2.quran.foundation` |
| `QF_CONTENT_BASE_URL` | QF Content API base — prelive: `https://apis-prelive.quran.foundation/content/api/v4` |
| `QF_CONTENT_TOKEN_URL` | Token URL for content API client credentials |
| `QF_USER_API_BASE_URL` | QF User API base — prelive: `https://apis-prelive.quran.foundation` |
| `QF_AUDIO_BASE_URL` | Base URL prepended to audio file paths from the Content API |

---

## API endpoints

### Auth `/auth`

| Method | Path | Description |
|---|---|---|
| `GET` | `/auth/login` | Starts OAuth2 PKCE flow — redirects to QF login |
| `GET` | `/auth/callback` | QF redirects here after login; issues a one-time code to the frontend |
| `POST` | `/auth/exchange` | Frontend exchanges the one-time code for a JWT |
| `GET` | `/auth/me` | Returns the logged-in user's profile (local DB only, no external call) |
| `POST` | `/auth/refresh` | Exchanges a refresh token for a new JWT + refresh token pair |
| `POST` | `/auth/logout` | Clears stored QF tokens |

### Content `/content`

| Method | Path | Description |
|---|---|---|
| `GET` | `/content/verses` | Arabic text + audio URL for a verse or page range |
| `GET` | `/content/metadata` | Surah list and available reciters |
| `GET` | `/content/daily-verse` | Random verse of the day with English translation, cached per UTC day. Optional `?translation_id=` query param (default `85` = M.A.S. Abdel Haleem). The first request of each UTC day determines the verse for all users. Response includes `tafsir_url` deep link to Quran.com for full context and tafsir. |

### Sync `/sync`

| Method | Path | Description |
|---|---|---|
| `GET` | `/sync/pull` | Returns changes since `lastPulledAt` for decks, cards, review logs, preferences |
| `POST` | `/sync/push` | Accepts local changes, upserts to DB, triggers QF bridge events |

### Goals `/goals`

| Method | Path | Description |
|---|---|---|
| `POST` | `/goals` | Creates a Quran range goal (synced to Quran.com) |
| `PATCH` | `/goals/{id}` | Updates an existing goal |

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"status": "ok"}` |

---

## Frontend integration notes

- After `GET /auth/login` redirects back to `FRONTEND_URL/auth/callback?code=...`, call `POST /auth/exchange` with that code to get a JWT.
- All protected endpoints require `Authorization: Bearer <jwt>`.
- The sync protocol follows WatermelonDB's standard `synchronize()` contract — pull first, then push.
- `GET /content/verses` accepts either `range_start` + `range_end` (e.g. `2:1` and `2:10`) or `page_start` + `page_end` (1–604). `recitation_id` is always required.
- `GET /content/daily-verse` accepts an optional `?translation_id=<int>` query param (default `85` = M.A.S. Abdel Haleem). The first `translation_id` used each UTC day wins and is cached. If today's cached verse has no translation (e.g. due to a prior QF API failure), the next request will automatically back-fill it.

---

## Related

- Frontend repo: *(add link)*
- Quran Foundation API docs: https://api-docs.quran.foundation
- Interactive API docs (local): http://localhost:8000/docs
