# PH Agent Hub

PH Agent Hub is a modular, multi-tenant AI platform that provides a chat interface for end users and an admin area for managing models, tools, tenants, and users — all in a single React web app. It is powered by the [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (MAF) and runs fully containerized with Docker.

---

## Features

### For End Users
- **AI Chat** with real-time streaming responses (Server-Sent Events)
- **Model selection** — choose from tenant-enabled AI models (DeepSeek, OpenAI, Anthropic, and more)
- **Templates, prompts, and skills** — curated by admins or created personally
- **File uploads** — attach files to chat sessions; stored securely in MinIO
- **Memory management** — view, add, and delete persistent memory entries
- **Session-level tools** — activate tenant-approved tools per session
- **Message branching** — edit or regenerate messages without losing history
- **Message feedback** — rate responses with thumbs up / down
- **Full-text search** across sessions and messages
- **Temporary sessions** — ephemeral chats that leave no database trace
- **Embeddable chat widget** — `<script>` tag embeddable on any website; anonymous guest sessions

### For Administrators & Managers
- **Tenant management** — create and manage isolated tenant environments
- **User management** — invite, deactivate, and reset passwords for users
- **Model configuration** — add AI providers with encrypted API keys
- **Tool configuration** — register ERPNext, membrane, or custom tools
- **Template & skill management** — curate reusable agent configurations
- **Usage analytics** — token usage reports scoped by tenant
- **Audit logging** — immutable record of all administrative actions
- **Role-based access** — admin (platform-wide) and manager (tenant-scoped) roles

### Platform
- **Multi-tenant** — complete data isolation between tenants
- **DeepSeek stabilizer** — automatic reasoning-strip, JSON repair, and retry for DeepSeek models
- **Docker deployment** — one command start with `docker compose up`
- **Production-ready** — Traefik with Let's Encrypt SSL, health checks, and external volumes

---

## Quick Start

```bash
git clone <repo-url> ph-agent-hub
cd ph-agent-hub/infrastructure
cp env.example env
# Edit `env` — set JWT_SECRET, ENCRYPTION_KEY, and at least one AI provider key
docker compose up --build
```

The platform starts at:
- **App**: http://localhost (frontend with chat + admin areas)
- **phpMyAdmin**: http://localhost:8080 (database admin, dev only)
- **MinIO Console**: http://localhost:9001 (object storage, dev only)

**Default admin login**: `admin@phagent.local` / `admin` (change immediately in production).

---

## Architecture

```
┌──────────────────────────────────────────────┐
│              React Frontend (Vite)           │
│  ┌──────────────┐   ┌──────────────────────┐ │
│  │  Chat Area   │   │     Admin Area       │ │
│  │  (end users) │   │  (admins/managers)   │ │
│  └──────┬───────┘   └──────────┬───────────┘ │
└─────────┼──────────────────────┼─────────────┘
          │   REST + SSE         │  REST
          ▼                      ▼
┌──────────────────────────────────────────────┐
│         FastAPI Backend + MAF Runtime        │
│  Auth · Models · Tools · Sessions · Memory   │
└──────────┬──────────┬──────────┬─────────────┘
           │          │          │
           ▼          ▼          ▼
┌──────────┐  ┌──────┐  ┌──────────────────────┐
│ MariaDB  │  │ Redis│  │  MinIO (S3-compat)   │
└──────────┘  └──────┘  └──────────────────────┘
```

- **Backend**: Python/FastAPI + SQLAlchemy 2.0 + Microsoft Agent Framework
- **Frontend**: React + TypeScript + Ant Design 5 + TanStack Query
- **Database**: MariaDB 11 (relational), Redis 7 (caching/sessions)
- **Storage**: MinIO (S3-compatible object storage)
- **Proxy**: nginx (dev) / Traefik (production)

---

## Documentation

| Document | Audience |
|---|---|
| [Administrator Guide](docs/admin-guide.md) | Platform admins & tenant managers |
| [End User Guide](docs/user-guide.md) | Chat users |
| [Architecture Overview](docs/architecture-overview.md) | Developers |
| [Backend Architecture](docs/backend-architecture.md) | Backend developers |
| [Frontend Architecture](docs/frontend-architecture.md) | Frontend developers |
| [Data Model](docs/data-model.md) | Developers |
| [Deployment Guide](docs/deployment.md) | DevOps |
| [Agent Framework Integration](docs/agent-framework-integration.md) | Backend developers |
| [DeepSeek Stabilizer](docs/deepseek-stabilizer.md) | Backend developers |

---

## License

See [LICENSE](LICENSE).
