# Deployment Guide — PH Agent Hub

This document describes how PH Agent Hub is deployed using Docker and Docker Compose. The platform provides two compose files:

- **`docker-compose.yml`** — development (nginx, exposed ports, phpMyAdmin)
- **`docker-compose.prod.yml`** — production (Traefik, no exposed ports, external volumes)

---

# 1. Deployment Overview

PH Agent Hub is deployed as a multi-service Docker stack consisting of:

- **Backend** — Agent Framework server
- **Frontend** — single React web app containing chat and admin areas
- **MariaDB** — primary relational database
- **Redis** — caching, queues, memory store
- **Optional Vector DB** — for RAG (Qdrant, Milvus, Weaviate)
- **Nginx** — reverse proxy (dev only)
- **phpMyAdmin** — database admin UI (dev: port 8080; prod: via Traefik subdomain)

All services run inside a shared `phagent-network` bridge network.

---

# 2. Repository Structure for Deployment

```
/infrastructure
  docker-compose.yml          # Development
  docker-compose.prod.yml     # Production (Traefik)
  nginx.conf                  # Dev reverse proxy
  env.example                 # Environment variable template
```

Each application (`backend`, `frontend`) contains its own Dockerfile.

---

# 3. Architecture

## 3.1 Development

```
┌──────────────────────────────────────────────┐
│                  Nginx Proxy                 │
│  - localhost routing                         │
│  - /api/ → backend, / → frontend, /pma/ →   │
│    phpMyAdmin                                │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│            Application Layer                 │
│  backend  frontend  phpMyAdmin               │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│                Data Layer                    │
│  MariaDB  Redis  MinIO  Vector DB (optional)  │
└──────────────────────────────────────────────┘
```

## 3.2 Production

```
┌──────────────────────────────────────────────┐
│              Traefik Proxy                   │
│  - SSL termination (Let's Encrypt)           │
│  - Host-based routing                        │
│  - HTTP → HTTPS redirect                     │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│            Application Layer                 │
│  backend  frontend  phpMyAdmin               │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│                Data Layer                    │
│  MariaDB  Redis  MinIO  Vector DB (optional)  │
└──────────────────────────────────────────────┘
```

---

# 4. Compose Files

## 4.1 Development (`docker-compose.yml`)

See the actual file at `infrastructure/docker-compose.yml`. Key characteristics:

- Uses **nginx** as reverse proxy (no SSL)
- All ports exposed for debugging (`:3306`, `:6379`, `:8000`, `:3000`, `:9000`, `:9001`, `:8080`)
- phpMyAdmin available at `http://localhost:8080` or `http://localhost/pma/`
- Volumes are auto-created by Docker Compose
- Network `phagent-network` is auto-created

### Hot-reload (bind mounts)

Both backend and frontend source directories are bind-mounted into their containers:

- **Backend**: `../backend:/app` overlay + `uvicorn --reload` watches for `.py` changes and auto-restarts the server.
- **Frontend**: `../frontend:/app` overlay with an anonymous volume at `/app/node_modules` to preserve container-installed dependencies. Vite HMR handles live updates in the browser.

Code edits take effect immediately — no rebuild or restart needed.

```bash
cd infrastructure
# First run or after dependency changes:
docker compose up --build

# Subsequent runs (code-only changes):
docker compose up
```

> **When `--build` is needed:** After changes to `backend/requirements.txt` or `frontend/package.json`. Code-only changes don't require a rebuild.

## 4.2 Production (`docker-compose.prod.yml`)

See the actual file at `infrastructure/docker-compose.prod.yml`. Key characteristics:

- Uses **Traefik** for SSL termination (Let's Encrypt) and host-based routing
- No infrastructure ports exposed — only Traefik listens on 80/443
- `restart: unless-stopped` on all services
- Volumes are **external** (must be created before first run)
- Network `phagent-network` is **external** (must be created before first run)
- Requires a running Traefik stack with `traefik-public` network

### Prerequisites (one-time)

```bash
docker network create phagent-network
docker volume create phagent_mariadb_data
docker volume create phagent_redis_data
docker volume create phagent_minio_data
```

### Start

```bash
cd infrastructure
docker compose -f docker-compose.prod.yml up -d
```

### Traefik routing

| Service    | Domain env var     | Example                          |
|------------|--------------------|----------------------------------|
| Backend    | `API_DOMAIN`       | `api.phagent.example.com`        |
| Frontend   | `APP_DOMAIN`       | `app.phagent.example.com`        |
| phpMyAdmin | `PMA_DOMAIN`       | `pma.phagent.example.com`        |

---

# 5. Environment Variables

All services share a common `infrastructure/env` file (copy from `env.example`). See `infrastructure/env.example` for the full list with documentation.

Key variables:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | MariaDB connection string |
| `REDIS_URL` | Redis connection string |
| `MINIO_ENDPOINT` | MinIO internal endpoint |
| `JWT_SECRET` | JWT signing key |
| `ENCRYPTION_KEY` | Fernet key for DB field encryption |
| `VITE_API_URL` | Frontend API base path (`/api`) |
| `API_DOMAIN` | Production backend domain (Traefik) |
| `APP_DOMAIN` | Production frontend domain (Traefik) |
| `PMA_DOMAIN` | Production phpMyAdmin domain (Traefik) |
| `MAX_FREE_TENANTS` | Max tenants allowed on free tier (default: `3`) |
| `LICENSE_PUBLIC_KEY` | Ed25519 public key for Pro license verification (base64, 32 bytes). Leave empty to disable. |
| `EMBED_GUEST_TOKEN_SECRET` | Separate JWT secret for guest (widget) tokens. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Required for the embeddable chat widget. |
| `WIDGET_ALLOWED_ORIGINS` | Additional origins allowed to embed the widget in an iframe (CSP `frame-ancestors`). Space-separated, e.g. `https://kainotomo.com`. `'self'` is always included. |
| `WIDGET_CONFIG_LIMIT` | Per-IP rate limit for `GET /widget/config/{token}` (default: `"30/hour"`) |
| `A2A_SERVER_ENABLED` | Enable A2A server endpoints (`/.well-known/agent-card.json`, `POST /message:send`, etc.) (default: `"false"`) |
| `A2A_PUBLIC_URL` | Public-facing base URL shown in the Agent Card (e.g. `https://api.example.com`) |
| `A2A_ORGANIZATION_NAME` | Organization name shown in the Agent Card `provider` field (default: `"PH Agent Hub"`) |
| `A2A_ORGANIZATION_URL` | Organization URL shown in the Agent Card `provider` field |
| `A2A_DOCS_URL` | Documentation URL shown in the Agent Card |
| `WIDGET_MESSAGE_LIMIT` | Per-guest message rate limit for `POST /widget/session/message`, short window (default: `"20/minute"`) |
| `WIDGET_TOTAL_MESSAGE_LIMIT` | Per-guest total message rate limit for `POST /widget/session/message`, long window (default: `"100/hour"`) |
| `WIDGET_SESSION_READ_LIMIT` | Per-guest read rate limit for `GET /widget/session`, `GET /widget/session/messages`, and `DELETE /widget/session/stream` (default: `"60/minute"`) |
| `AUTOPILOT_MAX_TURNS` | Max agent-invocation turns before autopilot forces summary (default: `20`) |
| `AUTOPILOT_MAX_TOKENS` | Max cumulative tokens for autopilot; `0` = unlimited (default: `0`) |
| `MAX_CONCURRENT_BACKGROUND_TASKS_PER_USER` | Max concurrent background tasks per user (default: `3`) |
| `BACKGROUND_TASK_TIMEOUT_SECONDS` | Max wall-clock time for a background task in seconds (default: `3600`) |
| `AGENT_MAX_STEPS` | Max tool-call steps before agent loop terminates (default: `15`) |
| `AGENT_PARALLEL_TOOLS_ENABLED` | Enable parallel tool execution (default: `True`) |
| `AUTO_SELECT_TOOLS_TOP_K` | Number of tools presented for auto-selection (default: `8`) |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | How often scheduler polls for due tasks in seconds (default: `30`) |
| `A2A_TASK_TTL_SECONDS` | Default TTL for completed/canceled A2A task records (default: `86400`) |
| `A2A_DEFAULT_RETRY_MAX_ATTEMPTS` | Default max retry attempts for A2A transient errors (default: `3`) |
| `A2A_DEFAULT_CIRCUIT_BREAKER_THRESHOLD` | Default consecutive failures to trip circuit breaker (default: `5`) |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID for Gmail/Calendar/Tasks |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `MS_CLIENT_ID` | Microsoft OAuth client ID for Outlook/Calendar/Tasks |
| `MS_CLIENT_SECRET` | Microsoft OAuth client secret |
| `API_BASE_URL` | Public-facing API base URL for OAuth callbacks (default: `http://localhost:8000`) |
| `FRONTEND_URL` | Public-facing frontend URL for OAuth redirects (default: `http://localhost:3000`) |

**Important:** `infrastructure/env` is in `.gitignore` — keep secrets out of version control.

---

# 6. Reverse Proxy

## 6.1 Development (nginx)

See `infrastructure/nginx.conf`. Routes:

| Path | Target |
|------|--------|
| `/api/` | `backend:8000` (SSE-ready) |
| `/pma/` | `phpmyadmin:80` |
| `/` | `frontend:3000` |

The frontend router handles `/chat/*` and `/admin/*` inside the same web app.

## 6.2 Production (Traefik)

Production uses Traefik (external stack) with Let's Encrypt auto-SSL. Each service declares its own routing via Docker labels in `docker-compose.prod.yml`. No separate nginx config is needed.

---

# 7. Ollama Setup (Local Models)

PH Agent Hub supports local LLMs via [Ollama](https://ollama.com). Ollama provides an OpenAI-compatible API, so it integrates as a first-class model provider alongside cloud providers.

## 7.1 Install Ollama

```bash
# Linux (curl method)
curl -fsSL https://ollama.com/install.sh | sh

# macOS — download from https://ollama.com
# Docker — see https://hub.docker.com/r/ollama/ollama
```

## 7.2 Pull a Model

```bash
ollama pull llama3.2
# or
ollama pull mistral
# or
ollama pull qwen2.5
```

## 7.3 Verify Ollama is Running

```bash
# Ollama serves its API on port 11434 by default
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2","messages":[{"role":"user","content":"Hello"}]}'
```

## 7.4 Configure in PH Agent Hub

1. Go to **Admin → Models → Create Model**
2. Set **Provider** to `Ollama`
3. Set **Model ID** to the model name you pulled (e.g., `llama3.2`)
4. Set **Base URL** to your Ollama server URL (e.g., `http://localhost:11434/v1` or `http://host.docker.internal:11434/v1` when running PH Agent Hub in Docker and Ollama on the host)
5. **API Key** is not required for Ollama — it's auto-filled with a placeholder
6. Set **Max Tokens**, **Temperature**, and **Context Window** as desired (these are for PH Agent Hub's internal context management, not passed to Ollama)
7. Click **OK**

## 7.5 Docker Considerations

When running PH Agent Hub in Docker:

- If Ollama is on the **host machine**, use `http://host.docker.internal:11434/v1` as Base URL (Docker Desktop) or the host's LAN IP
- If Ollama runs in its **own container**, add it to the `phagent-network` and use `http://ollama:11434/v1`
- Local models run on CPU by default. For GPU acceleration, install the NVIDIA Container Toolkit and add GPU reservations to the Ollama container

## 7.6 Limitations

- **Thinking/reasoning mode** is not supported (most local models don't emit reasoning tokens)
- **Tool calling** depends on the model — only recent models (e.g., `llama3.2`, `qwen2.5`) support native function calling
- **Performance** varies by hardware — Ollama runs on CPU by default; GPU acceleration significantly improves throughput

---

# 7. Deployment Modes

## **7.1 Local Development**
```bash
cd infrastructure
docker compose up --build   # first run or after dependency changes
docker compose up           # subsequent runs (code changes only)
```

Access:
- App: `http://localhost`
- API: `http://localhost/api/`
- phpMyAdmin: `http://localhost:8080` or `http://localhost/pma/`
- MinIO Console: `http://localhost:9001`

Alembic migrations run automatically inside the backend container on startup before the application server starts.

### Migration Verification (Issue #482)

Before deploying to production, verify all migrations are safe:

**Pre-deploy checklist:**

1. **Review migration risk registry** — Check `backend/tests/test_migrations.py` for the `RISK_REGISTRY` dict which classifies each migration as HIGH/MEDIUM/LOW risk.
2. **Preview SQL** — Generate the raw SQL that will be executed:
   ```bash
   cd backend
   alembic upgrade heads --sql
   ```
3. **Run migration tests** — Execute the DAG integrity and round-trip tests:
   ```bash
   cd backend
   pytest tests/test_migrations.py -v
   ```
4. **Test on staging** — Apply migrations against a staging database that mirrors production schema and data volume.
5. **Back up production DB** — Take a MariaDB dump or snapshot before deployment:
   ```bash
   docker compose exec mariadb mysqldump -u root -p phagent_hub > pre_migration_backup.sql
   ```

**HIGH-risk operations to watch for:**

| Operation | Risk | Mitigation |
|-----------|------|------------|
| `DROP TABLE` | Irreversible data loss | Verify no production data exists or archive first |
| `DROP COLUMN` | Data loss | Confirm column is truly unused |
| `MODIFY COLUMN ... ENUM(...)` | Full table rebuild (downtime) | Check table size; plan maintenance window |
| Data backfill (`UPDATE`) | Partial failure risk | Test on staging copy of production data |
| `ADD UNIQUE` on existing data | Failure on duplicates | Verify dedup logic works (e.g., `n1o2p3q4r5s7`) |

**Rollback procedure:**

If a migration causes issues in production:

```bash
# 1. Check current migration version
docker compose exec backend alembic current

# 2. Downgrade one step (replace REVISION with the previous revision ID)
docker compose exec backend alembic downgrade -1

# 3. Verify downgrade succeeded
docker compose exec backend alembic current

# 4. Restore database from backup if needed
# docker compose exec -T mariadb mysql -u root -p phagent_hub < pre_migration_backup.sql
```

> **Note**: Merge migrations (`4ffaa9dfdcb5`, `b5c6d7e8f9a0`, `930b42d7f5c0`) have empty `downgrade()` — they are no-ops and cannot be "undone". Always test on staging first.

**Current migration chain:**
- Root: `6b6bd31267a0` (initial_schema)
- Current head: `a5b6c7d8e9f0` (add_goal_based_skill_type)
- Total migrations: 65
- Merge migrations: 3 (all branches resolved)

### Hot-reload behavior
- **Backend**: Edit any `.py` file → uvicorn logs `WatchFiles detected changes… Reloading…` and restarts the server in ~2 seconds.
- **Frontend**: Edit any `.tsx`/`.ts` file → Vite HMR updates the browser instantly without a full page reload.
- **Dependencies**: After changing `requirements.txt` or `package.json`, restart with `--build` to rebuild the image layer.

## **7.2 Production Deployment**

```bash
cd infrastructure
docker compose -f docker-compose.prod.yml up -d
```

Recommended setup:

- Deploy on a single VPS (e.g. $10–$20/month)
- Requires a running Traefik stack with `traefik-public` network
- Set domains in `infrastructure/env` (`API_DOMAIN`, `APP_DOMAIN`, `PMA_DOMAIN`)
- Keep `infrastructure/env` out of version control
- Optionally manage via Portainer or Coolify for a web UI
- For horizontal scaling, add a load balancer and replicate the backend service

---

# 8. Scaling Considerations

### **Backend**
- Can be horizontally scaled
- Stateless except for DB + Redis

### **Frontend**
- Static or SPA-style web frontend
- Easily replicated

### **MariaDB**
- Use managed DB or replication for production

### **Redis**
- Use persistent storage or managed Redis

### **Vector DB**
- Optional but recommended for RAG-heavy deployments

---

# 9. Backup Strategy

### **MariaDB**
- Nightly dumps
- Binary log backup (optional)

### **Redis**
- Snapshotting (RDB)
- AOF persistence (optional)

### **Configuration**
- Backup `/infrastructure/env`
- Backup `/backend/config`

---

# 10. Goals of the Deployment Architecture

- simple local development
- clean production deployment
- one backend and one frontend deployable
- easy scaling
- secure API routing
- support for multi-tenant workloads
- support for Microsoft Agent Framework-based workflows
