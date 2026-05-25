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
