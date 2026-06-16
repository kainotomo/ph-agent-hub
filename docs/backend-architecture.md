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
- Security and tenant-isolation behaviour is validated by an automated test suite (see [security-testing.md](security-testing.md))

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

### **1.7 Spending Limit Enforcement**
Tenants with a numeric `balance_euros` have a spending limit enforced on every agent run:

1. **Pre-flight check**: Before any LLM call, the agent runner queries the tenant's balance. If `balance_euros <= 0`, the request is rejected with HTTP 402 (`InsufficientBalanceError`) and a clear error message.
2. **Unlimited tenants**: Tenants with `balance_euros IS NULL` are skipped entirely — no check, no deduction.
3. **Post-call deduction**: After a successful agent run (both streaming and non-streaming), the computed cost from the usage log is atomically deducted: `UPDATE tenants SET balance_euros = balance_euros - cost WHERE id = :tenant_id`.
4. **Last-call grace**: A call is allowed if `balance > 0` at the time of the check, even if the cost exceeds the remaining balance — the balance may go negative after that one call, blocking subsequent requests.
5. **Audit trail**: Every balance change is recorded in the `balance_transactions` table with a snapshot of the balance after the transaction.

The balance is stored and updated directly in MariaDB using atomic `UPDATE` statements — no Redis dependency is needed for this feature.

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

Tools now support a `mcp` type (alongside the existing `erpnext`, `membrane`, `datetime`, and many more — see the full list in §4). MCP tools are synced automatically from MCP server configurations via the `POST /admin/mcp-servers/:id/sync-tools` endpoint.

### **3.12 Admin Groups**
```
GET    /admin/groups
POST   /admin/groups
PUT    /admin/groups/:id
DELETE /admin/groups/:id
POST   /admin/groups/:id/models
DELETE /admin/groups/:id/models/:modelId
POST   /admin/groups/:id/tools
DELETE /admin/groups/:id/tools/:toolId
```

Groups are collections of models and tools that can be assigned to templates or skills for simplified configuration.

### **3.13 Admin Embed Configurations**
```
GET    /admin/embed-configs
POST   /admin/embed-configs
GET    /admin/embed-configs/:id
PUT    /admin/embed-configs/:id
DELETE /admin/embed-configs/:id
POST   /admin/embed-configs/:id/regenerate-token
```

Embed configurations control the embeddable chat widget appearance and behavior. See [`embed-widget.md`](embed-widget.md) for details.

### **3.14 Admin RAG Documents**
```
GET    /admin/rag/documents
DELETE /admin/rag/documents/:id
POST   /admin/rag/documents/:fileId/reindex
```

RAG document management — list, delete, and re-index uploaded documents. See [data-model.md](data-model.md) §4.2 for the data model.

### **3.15 Admin Sessions**
```
GET    /admin/sessions
GET    /admin/sessions/:id
DELETE /admin/sessions/:id
```

Administrative session viewing and cleanup. Admins see all sessions; managers see sessions in their own tenant.

### **3.16 Admin / Manager Templates**
```
GET    /admin/templates
POST   /admin/templates
PUT    /admin/templates/:id
DELETE /admin/templates/:id
```

### **3.17 Admin / Manager Skills**
```
GET    /admin/skills
POST   /admin/skills
PUT    /admin/skills/:id
DELETE /admin/skills/:id
```

### **3.18 Analytics and Audit**
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
      admin.py             — admin/manager CRUD (users, tenants, models, tools, groups,
                              templates, skills, embed configs, MCP servers, analytics, audit)
      auth.py              — login, refresh, logout, me
      chat.py              — sessions, messages, streaming, branches, feedback, files
      demo.py              — public demo endpoint
      memory.py            — user memory CRUD
      models.py            — user-facing model listing
      prompts.py           — user prompt CRUD
      skills.py            — user skill CRUD
      templates.py         — user-facing template listing
      users.py             — user management
      widget.py            — embed widget endpoints (guest sessions)
    /agents
      runner.py            — agent assembly, execution, streaming
      stabilizer.py        — DeepSeek stabilizer middleware
      deepseek_patch.py    — DeepSeek monkey-patches
      registry.py          — MAF agent/workflow scanner (scans skills/ and workflows/)
      identity.txt         — bot identity/system prompt
      skills/              — MAF skill definitions (scanned at startup)
      workflows/           — MAF workflow definitions (scanned at startup)
    /models
      base.py              — provider client factory
      deepseek.py          — DeepSeek ChatClient adapter
      openai.py            — OpenAI ChatClient adapter
      anthropic.py         — Anthropic ChatClient adapter
      ollama.py            — Ollama ChatClient adapter
    /tools                 — 30+ MAF tool implementations
      mcp.py               — MCP tool runner (dynamically invokes MCP server tools)
      erpnext.py           — ERPNext REST API tools
      membrane.py          — Membrane tools
      browser.py           — web scraping tool
      calculator.py        — arithmetic tool
      calendar.py          — date/time calculation tool
      code_interpreter.py  — Python code execution tool
      currency_exchange.py — FX rate lookup tool
      custom_tool_executor.py  — user-defined code tool runner
      datetime.py          — current date/time tool
      document_generation.py   — document creation tool
      email.py             — email sending tool
      etf_data.py          — ETF market data tool
      fetch_url.py         — HTTP fetch tool
      file_list.py         — file listing tool
      github.py            — GitHub API tool
      image_generation.py  — AI image generation tool
      market_overview.py   — market summary tool
      memory.py            — memory management tool
      pdf_extractor.py     — PDF text extraction tool
      portfolio.py         — portfolio analysis tool
      rag_search.py        — RAG document search tool
      rss_feed.py          — RSS feed reader tool
      sec_filings.py       — SEC filing lookup tool
      slack.py             — Slack messaging tool
      sql_query.py         — SQL query execution tool
      stock_data.py        — stock price tool
      weather.py           — weather forecast tool
      web_search.py        — web search tool
      wikipedia.py         — Wikipedia lookup tool
    /storage
      s3.py              — all MinIO/boto3 interactions; single module rule
    /services
      audit_service.py       — audit log writes/queries
      embed_service.py       — embed configuration management
      embedding_service.py   — text embedding generation
      group_service.py       — model/tool group management
      license_service.py     — Ed25519 license verification, tenant gating
      mcp_service.py         — MCP server CRUD, encryption, tool sync
      memory_service.py      — pagination, cross-session retrieval
      model_service.py       — model CRUD
      prompt_service.py      — user prompt CRUD
      rag_service.py         — RAG ingestion, semantic search, doc management
      session_service.py     — session CRUD, temp→permanent conversion
      settings_service.py    — system settings management
      skill_service.py       — skill CRUD
      template_service.py    — template CRUD
      tenant_service.py      — tenant CRUD
      tool_service.py        — tool CRUD
      upload_service.py      — file upload handling
      usage_service.py       — usage analytics
      user_service.py        — user CRUD
    /db
      base.py              — SQLAlchemy declarative base and async session factory
      /orm                 — SQLAlchemy ORM model definitions
        app_settings.py
        audit_logs.py
        embed_configs.py
        file_uploads.py
        groups.py
        mcp_servers.py
        memory.py
        message_embeddings.py
        messages.py
        models.py
        prompts.py
        rag.py
        sessions.py
        skills.py
        tags.py
        templates.py
        tenants.py
        tools.py
        usage_logs.py
        user_tool_preferences.py
        users.py
      /migrations          — Alembic migration scripts
        env.py
        versions/
    alembic.ini
    /core
      config.py
      dependencies.py    — FastAPI dependency injection helpers
      encryption.py      — Fernet encrypt/decrypt; only module that imports cryptography
      exceptions.py      — shared exception types and HTTP error handlers
      jwt.py             — JWT encode/decode; single module rule
      limiter.py         — rate-limiting helpers
      pagination.py      — paginated query helpers
      redis.py           — Redis client singleton
      security.py        — password hashing
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

Sensitive fields (`models.api_key`, MCP server env vars and headers) are encrypted at the application layer using **Fernet symmetric encryption** (AES-128-CBC + HMAC-SHA256) from the Python `cryptography` library.

### How it works
- The encryption key is loaded from the `ENCRYPTION_KEY` environment variable at startup
- All encrypt/decrypt operations go through `/backend/src/core/encryption.py` — the only file that imports `cryptography`
- Values are encrypted before being written to MariaDB and decrypted after being read
- The `models` ORM model uses a custom `EncryptedString` column type for `api_key` that transparently handles encrypt/decrypt at the ORM layer, so service code never handles raw ciphertext
- MCP server secrets (env vars and HTTP headers) are also encrypted via `mcp_service.py` using the same Fernet key

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

---

## 10. File Uploads

PH Agent Hub uses **MinIO** as the object storage backend for all file uploads. The S3-compatible API enables future migration to AWS S3 or Cloudflare R2 with only environment variable changes.

### 10.1 Storage Architecture

- All `boto3` calls are contained in `/backend/src/storage/s3.py` — the only module that interacts with MinIO directly
- One bucket per tenant, created automatically on tenant provisioning: `phhub-tenant-{tenant_id}`
- Object key format: `uploads/{user_id}/{session_id}/{file_id}-{original_filename}`
- All objects are private; access is always via presigned URLs (15-minute validity)
- Migration to Azure Blob Storage would require adding an adapter to `s3.py` only

### 10.2 Data Model

**Table: `file_uploads`**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `tenant_id` | UUID, FK | |
| `user_id` | UUID, FK | |
| `session_id` | UUID, FK, nullable | Null if uploaded outside a session |
| `message_id` | UUID, FK, nullable | Linked when attached to a message |
| `original_filename` | string | |
| `content_type` | string | MIME type |
| `size_bytes` | int | |
| `storage_key` | string | Full object key within the tenant bucket |
| `bucket` | string | MinIO bucket name |
| `is_temporary` | boolean | True if parent session is temporary |
| `created_at` | timestamp | |

### 10.3 API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/chat/session/:id/upload` | Upload a file (multipart/form-data) |
| `GET` | `/chat/session/:id/uploads` | List files for a session |
| `GET` | `/chat/session/:id/upload/:fileId/url` | Get presigned download URL |
| `DELETE` | `/chat/session/:id/upload/:fileId` | Delete a file |

### 10.4 Upload Flow

```
User selects file → Frontend POSTs multipart/form-data → Backend validates
(JWT, file type, file size, session ownership) → Stores object in MinIO →
Inserts file_uploads row → Returns file_id → Frontend attaches file_id(s)
to next message → Backend links message_id after message is persisted
```

### 10.5 Limits

| Setting | Default | Env var |
|---|---|---|
| Max file size | 100 MB | `UPLOAD_MAX_SIZE_BYTES` |
| Allowed MIME types | `text/plain`, `text/csv`, `text/markdown`, `application/pdf`, `application/json`, `image/png`, `image/jpeg`, `image/gif`, `image/webp` | `UPLOAD_ALLOWED_TYPES` |

### 10.6 Temporary Session Rules

- File uploads are **disabled** for temporary sessions (`403`)
- The frontend hides the upload button when the session is in temporary mode
- No file cleanup needed on temp session expiry — uploads were never permitted

### 10.7 File Lifecycle

| Event | Action |
|---|---|
| Session deleted (permanent) | All `file_uploads` rows deleted; objects removed from MinIO |
| User account deleted | All files owned by user deleted from MinIO and DB |
| Tenant deleted | All tenant bucket objects deleted; all rows deleted |
| Temporary session expires | No cleanup needed — uploads blocked |

### 10.8 Agent Tool Integration

When a message includes attached files, the backend extracts content before the agent loop runs:
- **Text-based files** (PDF, CSV, Markdown, JSON, plain text): extracted and injected into the agent's context window
- **Images**: passed as multi-modal message content if the selected model supports vision

### 10.9 Environment Variables

```
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_PREFIX=phhub-tenant
UPLOAD_MAX_SIZE_BYTES=104857600
UPLOAD_ALLOWED_TYPES=text/plain,text/csv,text/markdown,application/pdf,application/json,image/png,image/jpeg,image/gif,image/webp
```

---

## 11. Streaming Protocol

PH Agent Hub uses **Server-Sent Events (SSE)** over HTTP for streaming agent responses to the frontend. WebSocket is not used — chat streaming is unidirectional (server → client), and SSE works transparently through nginx without special proxy configuration.

### 11.1 Libraries

| Layer | Library | Reason |
|---|---|---|
| Backend | [`sse-starlette`](https://github.com/sysid/sse-starlette) | SSE support for FastAPI/Starlette |
| Frontend | [`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source) | SSE over POST requests (native `EventSource` only supports GET) |

### 11.2 Streaming Endpoints

**Authenticated users:**
```
POST /chat/session/:id/message
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <jwt>
```

**Widget (anonymous guests):**
```
POST /widget/session/message
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <guest_jwt>
```

**Stop generation (both modes):**
```
DELETE /chat/session/:id/stream
DELETE /widget/session/stream
```

The response is `text/event-stream`. The connection stays open until the agent finishes or the client aborts. Stop generation cancels the MAF agent run and saves the partially generated message as-is.

### 11.3 SSE Event Types

| Event | Payload | When |
|---|---|---|
| `token` | `{ delta: "token text" }` | Each streamed token from the model. `<think>` tokens are stripped before emission. |
| `tool_start` | `{ tool_call_id, tool_name, arguments }` | Agent begins executing a tool call |
| `tool_result` | `{ tool_call_id, tool_name, success, result_summary }` | Tool call completes |
| `step_complete` | `{ step_index, total_steps_so_far }` | End of each MAF agent step |
| `message_complete` | `{ branch_index, total_tokens, model_id }` | Agent finishes; message persisted |
| `follow_up_questions` | `{ questions: [...] }` | After `message_complete` if follow-up questions are enabled |
| `summarized` | `{ summary, summarized_message_count, tokens_saved }` | Conversation history was auto-summarized |
| `error` | `{ code, message }` | Non-recoverable error; connection closes after |
| `heartbeat` | `{}` | Every 15s on idle to keep connection alive |

**Error codes:** `model_timeout`, `model_error`, `tool_error`, `max_steps_exceeded`, `invalid_output`, `auth_error`, `internal_error`

### 11.4 Backend Implementation (FastAPI)

```python
from sse_starlette.sse import EventSourceResponse

async def stream_agent_response(session_id, message_id, agent_stream):
    async def event_generator():
        async for maf_event in agent_stream:
            if isinstance(maf_event, TokenEvent):
                yield {"event": "token", "data": json.dumps({...})}
            # ... other event types
        yield {"event": "message_complete", "data": json.dumps({...})}
    return EventSourceResponse(event_generator())
```

### 11.5 Frontend Implementation

```typescript
import { fetchEventSource } from '@microsoft/fetch-event-source';

await fetchEventSource(`/api/chat/session/${sessionId}/message`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${jwt}` },
  body: JSON.stringify({ content: userMessage }),
  signal: abortController.signal,
  onmessage(event) {
    const payload = JSON.parse(event.data);
    switch (event.event) {
      case 'token': appendToken(payload.delta); break;
      case 'tool_start': showToolProgress(payload); break;
      case 'tool_result': updateToolProgress(payload); break;
      case 'step_complete': updateStepCount(payload); break;
      case 'message_complete': finalizeMessage(payload); break;
      case 'error': showError(payload); break;
      case 'heartbeat': break;
    }
  },
});
```

### 11.6 Nginx Configuration

SSE requires disabling response buffering in nginx:

```nginx
location /api/ {
  proxy_pass http://backend:8000/;
  proxy_http_version 1.1;
  proxy_buffering off;
  proxy_cache off;
  proxy_read_timeout 300s;
  proxy_set_header Connection '';
  chunked_transfer_encoding on;
}
```

Without `proxy_buffering off`, nginx buffers the entire response before sending it, defeating streaming entirely.
