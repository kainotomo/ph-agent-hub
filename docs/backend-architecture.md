# Backend Architecture — PH Agent Hub

The backend of PH Agent Hub is the core service responsible for agent execution, model orchestration, tool integration, authentication, multi-tenant routing, and all persistent data operations. It exposes the APIs and streaming interfaces consumed by the single React frontend, which contains separate chat and admin areas.

This document defines the backend's responsibilities, internal structure, and integration points.

---

## 1. Backend Responsibilities

The backend provides the following core capabilities:

### **1.1 Agent Execution**
- Runs agent loops using the [Microsoft Agent Framework (MAF)](agent-framework-integration.md) — Python package `agent-framework`
- Agents are assembled per request from tenant and session state (model, template, skill, active tools)
- Supports multi‑step reasoning and tool calling via MAF's `Agent` and `Workflow` primitives
- Provides a DeepSeek‑compatible stabilization layer implemented as MAF middleware (JSON repair, retries, output filtering)
- Supports streaming responses and agent events to the chat area via SSE

### **1.2 Model Orchestration**
- Supports multiple model providers (DeepSeek, OpenAI, Anthropic, Ollama, local models, etc.)
- Allows per‑tenant model configuration
- Allows administrators to enable/disable models
- Provides routing logic for selecting the correct model per request

### **1.3 Tool Execution**
- ERPNext API tools (per‑tenant)
- Membrane tools
- Custom tools (Python modules)
- Tool permission enforcement based on user roles and tenant settings
- Session-level tool activation: users may activate tenant-approved tools per session; the active tool list is enforced at execution time
- Managers may create, edit, and delete tools within their tenant

### **1.4 Authentication & Authorization**
- JWT‑based authentication
- Three user roles:
  - **admin** — platform-wide access; manages all tenants and platform configuration
  - **manager** — tenant-scoped operator; can manage tools, models, templates, skills, and users within their own tenant
  - **user** — end user; chat area access only
- Tenant isolation enforced on every request
- Per‑tenant model and tool access rules
- Role claims in JWT used for endpoint-level permission enforcement

### **1.5 Data Storage**
- Users, roles, tenants (with tenant count gated by license on free tier)
- Models and tool configurations
- MCP server configurations (encrypted env vars and headers stored at rest)
- Templates, user prompts, and skills (tenant-shared and user-owned)
- Permanent chat sessions and messages (MariaDB)
- Temporary chat sessions (Redis with TTL; purged on logout or expiry, convertible to permanent)
- Message branches, soft-deleted messages, and message feedback
- Session-level active tool associations (auto-synced on skill change)
- Memory items (per user, optionally per session; supports manual user entries, pagination, and cross-session retrieval)
- RAG documents
- ERPNext instance configurations
- License verification (Ed25519 signature-based; optional for Pro tier)
- Schema defined as SQLAlchemy ORM models; migrations versioned and applied with Alembic

### **1.6 Multi‑Tenant Routing**
Each request is routed based on:
- JWT tenant claim
- Tenant‑specific model list
- Tenant‑specific tool list
- Tenant‑specific ERPNext instance (optional)
- Tenant‑specific templates and shared skills
- User‑owned prompts and personal skills within the tenant boundary

### **1.7 Extensibility**
The backend is designed to be fully patchable:
- Custom model adapters
- DeepSeek monkey‑patching
- Custom tool runners
- Custom agent behaviors
- Skill definitions mapped to MAF agents and workflows via `maf_target_key` (see [agent-framework-integration.md](agent-framework-integration.md))
- Middleware for request/response processing

---

## 2. High‑Level Backend Architecture

```
┌──────────────────────────────────────────────┐
│                API Layer (REST)              │
│  - Auth endpoints                             │
│  - Chat endpoints                             │
│  - Admin endpoints                            │
└──────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────┐
│          Agent Orchestration Layer           │
│  - Agent loop                                │
│  - Tool calling                              │
│  - DeepSeek stabilizer                       │
│  - Model routing                             │
└──────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────┐
│              Integration Layer               │
│  - Model adapters                            │
│  - ERPNext client                            │
│  - Membrane client                           │
│  - Custom tools                              │
└──────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────┐
│               Persistence Layer              │
│  - MariaDB (primary DB)                      │
│  - Redis (cache, queues, memory)             │
│  - MinIO (object storage for file uploads)   │
│  - MariaDB embedded vectors (RAG)            │
└──────────────────────────────────────────────┘
```

---

## 3. API Structure

### **3.1 Authentication**
```
POST /auth/login
POST /auth/refresh
GET  /auth/me
```

### **3.2 Chat**
```
POST   /chat/session
GET    /chat/session/:id
PUT    /chat/session/:id
POST   /chat/session/:id/message
GET    /chat/session/:id/messages
PUT    /chat/session/:id/message/:msgId
DELETE /chat/session/:id/message/:msgId
POST   /chat/session/:id/message/:msgId/regenerate
POST   /chat/session/:id/message/:msgId/feedback
GET    /chat/session/:id/stream
DELETE /chat/session/:id
POST   /chat/session/:id/finalize       # Convert temp → permanent (v1.10)
GET    /chat/sessions/search
```

> **`POST /chat/session/:id/finalize`** — Converts a temporary (Redis) session into a permanent (MariaDB) session. Migrates messages, tool activations, and file uploads. Returns the new permanent session. Requires the session to be in temporary mode.

### **3.3 File Uploads**
```
POST   /chat/session/:id/upload
GET    /chat/session/:id/uploads
GET    /chat/session/:id/upload/:fileId/url
DELETE /chat/session/:id/upload/:fileId
```

### **3.4 Embed Widget**

Widget endpoints are public (no user auth) — they use a guest token or short-lived
guest JWT instead:
```
GET    /api/widget/config/:token         # Bootstrap: returns theme + guest JWT + session
GET    /api/widget/session                # Get session info (guest JWT)
GET    /api/widget/session/messages       # List messages (guest JWT)
POST   /api/widget/session/message        # Send message + SSE stream (guest JWT)
DELETE /api/widget/session/stream         # Stop streaming (guest JWT)
```

Admin embed config management (requires auth):
```
GET    /api/admin/embed-configs           # List configs
POST   /api/admin/embed-configs           # Create config
GET    /api/admin/embed-configs/:id       # Get config
PUT    /api/admin/embed-configs/:id       # Update config
DELETE /api/admin/embed-configs/:id       # Delete config
POST   /api/admin/embed-configs/:id/regenerate-token  # Regenerate guest token
```

See [`embed-widget.md`](embed-widget.md) for full documentation.

### **3.5 User-Facing Configuration**
```
GET  /models
GET  /templates
GET  /prompts
POST /prompts
PUT  /prompts/:id
DELETE /prompts/:id
GET  /skills
POST /skills
PUT  /skills/:id
DELETE /skills/:id
```

### **3.5 Memory**
```
GET    /memory
POST   /memory
DELETE /memory/:id
```

Memory supports pagination via `?page=&page_size=` query parameters. When a `session_id` filter is applied, global memories (`session_id IS NULL`) are also included alongside session-scoped entries.

### **3.6 Session Tools**
```
GET    /chat/session/:id/tools
POST   /chat/session/:id/tools/:toolId
DELETE /chat/session/:id/tools/:toolId
```

### **3.7 Admin / Manager Users**
```
GET    /admin/users
POST   /admin/users
PUT    /admin/users/:id
DELETE /admin/users/:id
```

> Admins see users across all tenants. Managers see only users within their own tenant. Scope is enforced by the backend using the `tenant_id` claim in the JWT.

### **3.8 Admin Tenants** *(admin only)*
```
GET    /admin/tenants
POST   /admin/tenants
PUT    /admin/tenants/:id
DELETE /admin/tenants/:id
```

### **3.9 Admin / Manager Models**
```
GET    /admin/models
POST   /admin/models
PUT    /admin/models/:id
DELETE /admin/models/:id
```

### **3.10 Admin / Manager MCP Servers** *(added in v1.10)*
```
GET    /admin/mcp-servers
POST   /admin/mcp-servers
PUT    /admin/mcp-servers/:id
DELETE /admin/mcp-servers/:id
POST   /admin/mcp-servers/:id/test-connection
POST   /admin/mcp-servers/:id/sync-tools
```

MCP (Model Context Protocol) servers let administrators connect external tool sources. Supported transports:
- **streamable_http** — remote HTTP/SSE endpoint
- **stdio** — local subprocess (`npx`, `uvx`, etc.)
- **websocket** — persistent bidirectional connection

Secret env vars and HTTP headers are encrypted at rest using the Fernet key and masked in API responses.

### **3.11 Admin / Manager Tools**
```
GET    /admin/tools
POST   /admin/tools
PUT    /admin/tools/:id
DELETE /admin/tools/:id
```

Tools now support a `mcp` type (alongside the existing `erpnext`, `membrane`, `custom`, `datetime`, etc.). MCP tools are synced automatically from MCP server configurations via the `POST /admin/mcp-servers/:id/sync-tools` endpoint.

### **3.12 Admin / Manager ERPNext Instances**
```
GET    /admin/tools/erpnext
POST   /admin/tools/erpnext
PUT    /admin/tools/erpnext/:instance_id
DELETE /admin/tools/erpnext/:instance_id
```

> Sensitive fields (`api_key`, `api_secret`) are never returned in response bodies. Values are transparently encrypted at rest via the `EncryptedString` ORM type.

### **3.13 Admin / Manager Templates**
```
GET    /admin/templates
POST   /admin/templates
PUT    /admin/templates/:id
DELETE /admin/templates/:id
```

### **3.14 Admin / Manager Skills**
```
GET    /admin/skills
POST   /admin/skills
PUT    /admin/skills/:id
DELETE /admin/skills/:id
```

### **3.15 Analytics and Audit**
```
GET /admin/usage
GET /admin/logs
GET /admin/audit
```

> `GET /admin/audit` is admin-only. Managers can access `/admin/usage` and `/admin/logs` scoped to their own tenant. Audit log entries are read-only — no delete endpoint is exposed.

---

## 4. Backend Folder Structure

```
/backend
  /src
    /api
      auth.py
      chat.py
      models.py
      prompts.py
      skills.py
      admin.py
    /agents
      runner.py
      stabilizer.py
      deepseek_patch.py
    /models
      base.py
      deepseek.py
      openai.py
      anthropic.py
    /tools
      mcp.py                — MCP tool runner (dynamically invokes MCP server tools)
      erpnext.py
      membrane.py
      custom/
    /storage
      s3.py              — all MinIO/boto3 interactions; single module rule
    /services
      user_service.py
      tenant_service.py
      model_service.py
      tool_service.py
      mcp_service.py          — MCP server CRUD, encryption, tool sync
      erpnext_service.py
      template_service.py
      prompt_service.py
      skill_service.py
      session_service.py      — finalize_session (temp→permanent), sync_session_tools_for_skill
      memory_service.py       — pagination, cross-session retrieval support
      license_service.py      — Ed25519 license verification, tenant gating
      audit_service.py
      embedding_service.py
      rag_service.py          — RAG ingestion, semantic search, document mgmt (Issue #250)
      group_service.py
      upload_service.py
      usage_service.py
    /db
      base.py              — SQLAlchemy declarative base and async session factory
      /orm                 — SQLAlchemy ORM model definitions
        users.py
        tenants.py
        models.py
        tools.py
        erpnext_instances.py
        templates.py
        prompts.py
        skills.py
        sessions.py
        messages.py
        memory.py
        file_uploads.py
        rag.py
        usage_logs.py
        audit_logs.py
      /migrations          — Alembic migration scripts
        env.py
        versions/
    alembic.ini
    /core
      config.py
      security.py
      encryption.py     — Fernet encrypt/decrypt; the only module that imports cryptography
      jwt.py
      exceptions.py
  Dockerfile
```

---

## 5. ORM & Database Migrations

The backend uses **SQLAlchemy 2.0** as the ORM and **Alembic** for schema migrations.

### **5.1 SQLAlchemy ORM**
- All database tables are defined as SQLAlchemy model classes under `/db/orm/`
- The async session factory (`AsyncSession`) is configured in `/db/base.py` using `aiomysql` as the MariaDB driver
- Complex queries (e.g., message branching tree, full-text search) are written as raw SQL via `session.execute(text(...))` and called from the service layer
- JSON columns (`messages.content`, `messages.tool_calls`, `tools.config`, etc.) map to SQLAlchemy's `JSON` column type and are read/written as Python dicts

### **5.2 Alembic Migrations**
- Migration scripts live in `/db/migrations/versions/`
- Alembic tracks applied migrations in the `alembic_version` table it manages in MariaDB
- To generate a migration after changing a model: `alembic revision --autogenerate -m "description"`
- All generated migration files must be reviewed before applying — autogenerate does not detect changes inside JSON columns, custom check constraints, or complex index types
- To apply all pending migrations: `alembic upgrade head`

### **5.3 Migration on Startup**
- The backend container runs `alembic upgrade head` as part of its Docker entrypoint before starting the application server
- This ensures the schema is always up to date on every deployment without manual intervention
- The MariaDB container must be healthy before the backend starts; a health-check is used in `docker-compose.yml` for this purpose

---

## 6. DeepSeek Stabilization Layer

The backend includes a dedicated stabilization module responsible for:

- Stripping `<think>` reasoning tokens
- Repairing invalid JSON
- Validating tool calls
- Retrying failed model outputs
- Enforcing schema compliance
- Filtering hallucinated tool names
- Preventing infinite agent loops

This module is fully monkey‑patchable.

---

## 7. JWT Payload and Multi-Tenant Enforcement

Every protected request carries a JWT signed with `JWT_SECRET`. The payload contains:

```json
{
  "sub": "<user_id>",
  "tenant_id": "<tenant_id>",
  "role": "admin | manager | user",
  "exp": 1234567890,
  "iat": 1234567890
}
```

**Why no `permissions` field:** The three roles are rigid and fully defined — a manager always has exactly the same capabilities as every other manager. There is nothing a `permissions` array could express that `role` does not already cover. Putting a derived permissions list in the JWT would create a split-brain risk: if the token's claims diverge from DB state (e.g. a role change before token expiry), the backend could enforce stale permissions. All access decisions are derived from `role` at request time using a single authorisation dependency injected into every protected endpoint.

### Role enforcement rules

| Claim check | Enforcement point |
|---|---|
| `role == admin` | Admin-only endpoints (tenant management, system settings) |
| `role in (admin, manager)` | Admin area endpoints; manager is additionally scoped to own `tenant_id` |
| `tenant_id` matches resource | Every data query — no cross-tenant data ever returned |
| `role == user` OR any role | Chat area endpoints |

### Token lifetime and refresh

- Access token TTL: configurable via `JWT_EXPIRES_IN` (default 3600 seconds)
- Refresh token: issued alongside the access token; stored as an `httpOnly` cookie; TTL configurable via `JWT_REFRESH_EXPIRES_IN` (default 7 days)
- The `/auth/refresh` endpoint validates the refresh token cookie and issues a new access token
- On logout, the refresh token is invalidated server-side via a Redis denylist keyed by `jti` claim
- Role changes (e.g. a manager being demoted) take effect at the next token refresh

### Implementation

JWT logic lives exclusively in `/backend/src/core/jwt.py`. No other module encodes or decodes tokens directly.

---

## 8. Encryption

Sensitive fields (`models.api_key`, `erpnext_instances.api_key`, `erpnext_instances.api_secret`) are encrypted at the application layer using **Fernet symmetric encryption** (AES-128-CBC + HMAC-SHA256) from the Python `cryptography` library.

### How it works
- The encryption key is loaded from the `ENCRYPTION_KEY` environment variable at startup
- All encrypt/decrypt operations go through `/backend/src/core/encryption.py` — the only file that imports `cryptography`
- Values are encrypted before being written to MariaDB and decrypted after being read
- The SQLAlchemy ORM models for `models` and `erpnext_instances` use a custom `EncryptedString` column type that transparently handles encrypt/decrypt at the ORM layer, so service code never handles raw ciphertext

### Key generation
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
The output is a 32-byte base64url-encoded key. Store it in `.env` as `ENCRYPTION_KEY`.

### Key rotation
If the encryption key must be rotated:
1. Generate a new key
2. Run the provided key-rotation migration utility: `python -m backend.src.core.encryption rotate --old-key OLD --new-key NEW`
3. Replace `ENCRYPTION_KEY` in `.env` and redeploy

The key and the encrypted data live on the same server — this is acceptable for a self-hosted platform where physical server access already implies full compromise. For stricter requirements, the `encryption.py` module can be replaced with a Vault or Azure Key Vault adapter without changing any other code.

---

## 9. Deployment

The backend runs as a Docker container and depends on:

- MariaDB (embedding vectors stored as JSON for RAG)
- MinIO
- Nginx reverse proxy

It is designed for both single‑server and multi‑server deployments.

---

## 9. Goals of the Backend

- Provide a stable, extensible agent runtime
- Support DeepSeek and other advanced models
- Enable multi‑tenant AI applications
- Provide clean APIs for both frontend areas
- Allow safe monkey‑patching and customization
- Maintain strict separation of concerns
- Support dual-mode session persistence (permanent via MariaDB, temporary via Redis)
- Support non-destructive message branching for edits and regeneration
- Expose message feedback for model quality analytics
