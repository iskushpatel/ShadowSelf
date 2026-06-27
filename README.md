 Live Demo: https://shadowself-lb24.onrender.com
# ShadowSelf
> **An AI system that shows how your online writing may be perceived — using data from your own social media.**

ShadowSelf ingests your writing from sources you control, normalises it into a unified post store, and runs LLM agents to build a *perception mirror*: not a proof of who you are, but a reflection of how you are perceived by others based on your social media presence.

Every result is tied to the data it came from and labelled with how reliable that source is, so you always know what the analysis is (and isn't) based on.

<img width="1920" height="1080" alt="Screenshot (183)" src="https://github.com/user-attachments/assets/23c8bcbf-40b4-459e-a410-ac3ddf8ce00b" />
<img width="1920" height="1080" alt="Screenshot (184)" src="https://github.com/user-attachments/assets/2e90042c-9d46-4c6b-ac71-022dc50f1677" />
<img width="1920" height="1080" alt="Screenshot (185)" src="https://github.com/user-attachments/assets/5cacd062-146b-4693-a256-672711ffc5ea" />



---

## How it works

```
Your data  →  Ingestion layer  →  raw_posts (Postgres)  →  LLM agents  →  Profile snapshot
```

1. **Collect** — connect or upload one or more sources
2. **Analyze** — agents score OCEAN personality dimensions, dominant topics, tone, and communication style
3. **Review** — every finding is labelled with its source tier so you know how much weight to give it

---

## Source tiers

Not all sources are equal. ShadowSelf labels each one so results are honest about confidence.

| Tier | Sources | Why it matters |
|---|---|---|
| **Reliable** | Manual text paste, GitHub, Reddit (live API), Gmail OAuth | Real posts you authored, fetched directly |
| **User export** | LinkedIn ZIP, Facebook/Instagram ZIP, Twitter/X ZIP | Your own GDPR export — authoritative but requires a manual download step |
| **Reference only** | LinkedIn public URL, Meta public pages, Twitter public search | Scraped snapshots; limited content, no guarantee posts are yours. Labelled *Weak reference* in the UI — do not treat as primary signal |

---

## Stack

- **Backend** — FastAPI, SQLAlchemy (async), PostgreSQL, Redis-ready Celery
- **Frontend** — Static HTML/CSS/JS served by FastAPI at `/`
- **Analysis** — Groq LLM via LangChain agents
- **Scraping** — Playwright (headless Chromium) for JS-rendered pages
- **Observability** — optional LangSmith tracing

---

## Quickstart

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — install with `pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker Desktop (for Postgres + Redis)

### 1. Clone and install

```bash
git clone https://github.com/your-username/shadowself.git
cd shadowself
uv sync
```

### 2. Configure environment

```powershell
Copy-Item .env.example .env
```

Minimum required keys:

```env
DATABASE_URL=postgresql+asyncpg://shadowself:shadowself_secret@localhost:5432/shadowself
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.1-8b-instant
```

See [Environment reference](#environment-reference) for all options.

### 3. Start services

```powershell
docker compose up -d
```

Verify both containers are healthy:

```powershell
docker compose ps
# shadowself-postgres   Up (healthy)
# shadowself-redis      Up (healthy)
```

### 4. Run the app

```powershell
uv run python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` in your browser.

### 5. Check it works

```powershell
# Syntax and import check
.\scripts\test.ps1

# End-to-end smoke test (GitHub — no token needed for public profiles)
.\scripts\smoke_mvp.ps1 -Source github -Account octocat

# Other sources
.\scripts\smoke_mvp.ps1 -Source reddit -Account spez
.\scripts\smoke_mvp.ps1 -Source manual
.\scripts\smoke_mvp.ps1 -Source gmail   -AccessToken "<google_access_token>"
.\scripts\smoke_mvp.ps1 -Source linkedin -AccessToken "<linkedin_access_token>"
.\scripts\smoke_mvp.ps1 -Source meta    -AccessToken "<meta_access_token>"
```

The smoke script hits `/health`, ingests a source, calls `/analyze/{user_id}`, and prints the full profile JSON.

For the browser flow:

1. Click **Check health** to confirm the API and database are connected
2. Enter a public GitHub username and click **Pull GitHub data**
3. Click **Analyze profile**
4. Review the OCEAN scores, topics, tone, and communication style

---

## API endpoints

### Reliable sources

| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest/github` | Public GitHub profile, repos, and events by username |
| `POST` | `/ingest/reddit-live` | Public Reddit comments and submissions by username |
| `POST` | `/ingest/gmail` | Gmail messages via OAuth token (readonly scope) |
| `POST` | `/ingest/manual` | Direct text paste — Twitter/X exports, any freeform writing |

### User export (GDPR ZIP)

| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest/linkedin` | LinkedIn ZIP export (CSV/JSON) via multipart `file` field |
| `POST` | `/ingest/meta` | Instagram ZIP export (JSON) via multipart `file` field |
| `POST` | `/ingest/reddit-zip` | Reddit GDPR export |

### Reference only (weak signal)

| Method | Path | Description |
|---|---|---|
| `POST` | `/ingest/linkedin` | LinkedIn public URL — rendered with Playwright, limited content, labelled *Weak reference* |
| `POST` | `/ingest/linkedin-search` | LinkedIn profile search via Tavily — reference only |

> LinkedIn may serve a login wall for private or restricted profiles. Public URL scraping is best-effort.

### Analysis and profile

| Method | Path | Description |
|---|---|---|
| `POST` | `/analyze/{user_id}` | Run LLM agents; build profile snapshot from all ingested posts |
| `GET` | `/profile/{user_id}` | Retrieve the latest profile snapshot |
| `GET` | `/health` | API and database status |

All ingestion endpoints normalise posts to `raw_posts`, deduplicate by content hash per user, then `/analyze/{user_id}` builds the snapshot.

---

## Environment reference

Copy `.env.example` to `.env`. Keys marked **required** must be set before the app will start.

```env
# === Required ===
DATABASE_URL=postgresql+asyncpg://shadowself:shadowself_secret@localhost:5432/shadowself
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.1-8b-instant

# === App ===
PUBLIC_BASE_URL=http://127.0.0.1:8000   # Set to your deployed URL in production
OAUTH_STATE_SECRET=long_random_string   # Auto-generated on Render; set manually elsewhere

# === Optional: higher GitHub rate limits ===
GITHUB_TOKEN=your_github_personal_access_token

# === Optional: LinkedIn public profile search ===
TAVILY_API_KEY=your_tavily_key
TAVILY_API_URL=https://api.tavily.com

# === Optional: Gmail OAuth ===
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=

# === Optional: LinkedIn OAuth ===
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=

# === Optional: Meta / Instagram OAuth ===
META_APP_ID=
META_APP_SECRET=

# === Optional: LangSmith tracing ===
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=shadowself

# === Optional: Celery / Redis (not required for MVP) ===
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_BACKEND_URL=redis://localhost:6379/0
```

---

## OAuth setup

Set `PUBLIC_BASE_URL` to your deployed URL, then register these callback URIs in each provider's developer dashboard:

```
https://your-app.onrender.com/oauth/gmail/callback
https://your-app.onrender.com/oauth/linkedin/callback
https://your-app.onrender.com/oauth/meta/callback
```

- **Gmail** — requires Gmail readonly scope
- **LinkedIn** — uses OpenID Connect profile scopes
- **Meta** — uses Graph API; `user_posts` permission may require app review for non-development users

---

## Deployment

### Docker

```bash
docker build -t shadowself .
docker run -p 8000:8000 --env-file .env shadowself
```

### Generic host (uv)

```bash
uv sync
playwright install chromium
uv run uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## Caveats

ShadowSelf builds a *perception mirror*, not a psychological assessment.

- Results reflect patterns in your writing, not your personality or intentions
- Confidence varies by source tier — a profile built from one weak reference source is very different from one built from 141 posts across Gmail and GitHub
- OCEAN scores are model estimates, not validated psychometric measurements
- Public scraping (LinkedIn URL, Meta pages) is best-effort and may break if providers change their pages or block the scraper

---
