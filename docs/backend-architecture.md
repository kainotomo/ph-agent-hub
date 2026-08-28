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
- MCP (Model Context Protocol) tools — connect to external MCP servers and sync their tools
- A2A (Agent-to-Agent) Protocol tools — discover remote A2A agents via Agent Cards and use their skills as tools
  - `services/a2a_service.py` — CRUD, connection test, tool sync, Agent Card resolution with version negotiation
  - `services/a2a_circuit_breaker.py` — Redis-backed circuit breaker for A2A servers (consecutive failure tracking, auto-recovery)
  - `services/a2a_client.py` — resilient `send_message` wrapper with configurable timeouts, retry with exponential backoff, structured logging, and call log persistence
  - `tools/a2a.py` — builds MAF-compatible tool callables from A2A Tool records; delegates to the resilient client
- ERPNext API tools (per‑tenant with optional per‑user credentials)
- Membrane tools
- Custom tools (Python modules)
- Tool permission enforcement based on user roles and tenant settings
- Session-level tool activation: users may activate tenant-approved tools per session; the active tool list is enforced at execution time
- Managers may create, edit, and delete tools within their tenant

**Parallel Tool Execution (Issue #447):** When the LLM returns multiple
`tool_calls` in a single response for independent operations, MAF executes
them concurrently via `asyncio.gather`. The system prompt includes guidance
encouraging the LLM to batch independent calls. The feature is gated by
`AGENT_PARALLEL_TOOLS_ENABLED` (default `True`) in `backend/src/core/config.py`.
Streaming SSE events include a `batch_id` field so the frontend can display
parallel execution indicators. Parallel batches count as a single step toward
`AGENT_MAX_STEPS`, preventing premature termination.

**Auto Tool Selection (Issues #287, #439):** Instead of requiring users to
manually activate tools per session, the agent can automatically select
relevant tools from the available pool. The LLM receives tool definitions
and chooses which to invoke based on the user's request. The number of tools
presented to the LLM is capped by `AUTO_SELECT_TOOLS_TOP_K` (default `8`).
This feature works alongside manual tool activation — both are supported.

**Autopilot Mode (Issue #446):** An autonomous multi-turn execution mode
where the agent continues working without requiring a user prompt after each
response. The autopilot controller manages turn boundaries, invokes the
agent in a loop, and can pause/resume execution. Protections include
`AUTOPILOT_MAX_TURNS` (default `20`) to prevent runaway sessions and
`AUTOPILOT_MAX_TOKENS` (default `0` = unlimited) for cumulative token limits.
Progress is streamed via SSE and displayed in a dedicated UI.

**Background Tasks (Issue #449):** Long-running agent executions that run
independently of the user's current chat session. Users can start a task in
background mode, navigate away, and receive a notification when it completes.
Configurable limits: `MAX_CONCURRENT_BACKGROUND_TASKS_PER_USER` (default `3`)
and `BACKGROUND_TASK_TIMEOUT_SECONDS` (default `3600`).

**Scheduled Tasks (Issue #297):** Time-based autonomous agent execution
scheduled at specific times or intervals. Supports one-shot (run at datetime)
and recurring (cron-like) schedules. Tasks are stored in the database with
states: `ACTIVE`, `PAUSED`. A background scheduler loop polls for due tasks
at `SCHEDULER_POLL_INTERVAL_SECONDS` (default `30`). Users and admins can
create, pause, resume, and delete scheduled tasks.

**Notifications:** Users receive notifications for background task completion,
scheduled task events, and other agent-triggered events. Notifications are
stored in the database with read/unread state. The API provides list, mark
read, mark all read, and unread count endpoints. The frontend displays a bell
icon with unread badge.

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
- A2A server configurations (encrypted auth tokens and headers stored at rest; cached Agent Cards)
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

> **`GET /chat/sessions/search?q=<term>&scope=<scope>`** — Full-text search across a user's permanent sessions.
> - `q` (required): the search term.
> - `scope` (optional, default `all`): limits which fields are searched — `all` (title + content + tag), `title`, `content`, or `tag`. Invalid values return 422.
> - Each result extends the standard session payload with `matched_fields` (e.g. `["title", "content"]`) indicating which scopes matched, so clients can show *why* a session matched when searching `all`.

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
GET  /skills          (supports ?page=&page_size=&search=&sort_by=&sort_dir=)
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

### **3.19 Background Tasks** *(admin/manager)*
```
GET    /background-tasks           # List tasks (paginated, filterable by state)
POST   /background-tasks/:id/cancel  # Cancel a running background task
```

Background tasks are long-running agent executions that run independently of the user's current chat session. They support states like `RUNNING`, `COMPLETED`, `FAILED`, `CANCELED`. Tasks are tracked in the `autopilot_runs` table and can be viewed in the admin panel or from the user's chat sidebar.

### **3.20 Scheduled Tasks** *(admin/manager)*
```
GET    /scheduled-tasks            # List tasks (paginated, filterable by state)
POST   /scheduled-tasks            # Create a scheduled task
GET    /scheduled-tasks/:id        # Get task details
PUT    /scheduled-tasks/:id        # Update a scheduled task
DELETE /scheduled-tasks/:id        # Delete a scheduled task
POST   /scheduled-tasks/:id/pause  # Pause a scheduled task
POST   /scheduled-tasks/:id/resume # Resume a paused scheduled task
```

Scheduled tasks support time-based autonomous agent execution. States: `ACTIVE`, `PAUSED`. They can be one-shot (run at a specific datetime) or recurring (cron-like schedule). A background scheduler loop polls for due tasks.

### **3.21 Notifications** *(user)*
```
GET    /notifications              # List notifications (paginated, unread_only filter)
POST   /notifications/read/:id     # Mark a notification as read
POST   /notifications/read-all     # Mark all notifications as read
GET    /notifications/unread-count # Get unread notification count
```

Notifications are created for background task completion, scheduled task events, and other agent-triggered events. They are user-scoped with read/unread state tracking.

### **3.22 Update Semantics**

All `PUT` endpoints follow a consistent convention for handling request bodies:

- **Omitted field** — The field is absent from the JSON body. The existing value is **preserved** unchanged.
- **Explicit `null`** — The field is sent as `{"field_name": null}`. The DB column is **set to NULL** (if nullable).

This is implemented via Pydantic's `model_dump(exclude_unset=True)`, which returns only the fields the caller explicitly provided. The utility function `collect_update_fields(body)` in `backend/src/core/schemas.py` wraps this pattern and is used by all `PUT` endpoints.

**Before this convention** (legacy pattern), `PUT` endpoints used `if val is not None` guards that treated explicit `null` the same as omission, making it impossible for clients to clear nullable fields.

> Use `PATCH` semantics with `PUT`: send only the fields you want to change. See `backend/src/core/schemas.py` for the shared utility.

### **3.23 A2A Server** *(A2A Protocol — Inbound)*

The A2A server implements the inbound side of the Agent-to-Agent protocol (Google A2A Spec §11 — HTTP+JSON/REST binding). It exposes ph-agent-hub agents as A2A-compatible agents that external clients can discover and invoke. These endpoints are served on the A2A binding path (not under `/api/`) and are only enabled when `A2A_SERVER_ENABLED=true`.

```
GET    /.well-known/agent-card.json         # Agent discovery (AgentCard per A2A §8)
POST   /message:send                        # Execute a task (sync or async)
POST   /message:stream                      # Execute a task (SSE streaming)
GET    /tasks/{task_id}                     # Poll task status
POST   /tasks/{task_id}:cancel              # Cancel a running task
```

**Task lifecycle** (Issue #411):
- **`returnImmediately: true`** — Spawns background agent execution and returns immediately with `TASK_STATE_SUBMITTED`. The client polls `GET /tasks/{id}` until completion.
- **`taskId` in request** — Resumes a suspended task (`TASK_STATE_INPUT_REQUIRED` or `TASK_STATE_AUTH_REQUIRED`) for multi-turn conversations. The follow-up message is appended to the existing session history.
- **Input-required flow** — The agent can request additional user input by calling the built-in `ask_user(question)` tool. This stores the question in Redis under `ask_user:{task_id}`. After `agent.run()` returns, the A2A layer detects the flag and transitions the task to `TASK_STATE_INPUT_REQUIRED` with the question as `status_message`. The client can then resume the task by sending a follow-up message with the original `taskId`.
- **Cancellation** — `POST /tasks/{id}:cancel` sets Redis-backed cancellation flags, transitions the task to `TASK_STATE_CANCELED`, and the agent runner aborts.
- **Persistence** — Tasks are stored in the `a2a_tasks` database table (ORM: `A2aTask`). Tasks survive server restarts.

Task states:
```
SUBMITTED → WORKING → COMPLETED
                    → FAILED
                    → CANCELED
                    → INPUT_REQUIRED → (resume) → WORKING → COMPLETED
                    → AUTH_REQUIRED  → (resume) → WORKING → COMPLETED
```

Key files:
- `api/a2a_server.py` — All 5 endpoints + background task processor
- `api/admin.py` — A2A server CRUD endpoints (under `/admin/a2a-servers/`), circuit breaker inspection/reset, call log listing
- `services/a2a_task_service.py` — CRUD + state management for `A2aTask`
- `services/a2a_service.py` — A2A server configuration CRUD (outbound admin)
- `services/a2a_client.py` — Resilient A2A client with retry, timeout, circuit breaker
- `services/a2a_circuit_breaker.py` — Redis-backed circuit breaker
- `tools/a2a.py` — A2A tool callable builder for outbound remote calls
- `core/redis.py` — A2A cancellation flags (`set_a2a_cancel`, `check_a2a_cancel`) and ask_user helpers (`store_a2a_question`, `get_a2a_question`)
- `db/orm/a2a_servers.py` — `A2aServer` ORM model with resilience columns
- `db/orm/a2a_tasks.py` — `A2aTask` ORM model
- `db/orm/a2a_call_logs.py` — `A2aCallLog` ORM model (denormalized call history)

Admin API endpoints (see `api/admin.py`):
| Endpoint | Method | Purpose |
|---|---|---|
| `/admin/a2a-servers` | GET | List A2A servers (paginated, filterable) |
| `/admin/a2a-servers` | POST | Create A2A server |
| `/admin/a2a-servers/{id}` | GET | Get A2A server details |
| `/admin/a2a-servers/{id}` | PUT | Update A2A server |
| `/admin/a2a-servers/{id}` | DELETE | Delete A2A server (cascades to tools) |
| `/admin/a2a-servers/{id}/test` | POST | Test connection to remote agent |
| `/admin/a2a-servers/{id}/sync-tools` | POST | Sync remote agent skills as Tool records |
| `/admin/a2a-servers/{id}/circuit-breaker` | GET | Get circuit breaker state |
| `/admin/a2a-servers/{id}/circuit-breaker/reset` | POST | Manually reset circuit breaker |
| `/admin/a2a-call-logs` | GET | List A2A call logs (filterable by server/status/date) |

---

## 4. Backend Folder Structure

```
/backend
  /src
    /api
      a2a_oauth.py         — A2A OAuth2 authentication endpoints
      a2a_server.py        — A2A protocol server (Agent Card, task lifecycle)
      admin.py             — admin/manager CRUD (users, tenants, models, tools, groups,
                              templates, skills, embed configs, MCP servers, analytics, audit)
      auth.py              — login, refresh, logout, me
      background_tasks.py  — list/cancel background agent tasks
      chat.py              — sessions, messages, streaming, branches, feedback, files
      credentials.py       — OAuth credential management (Google, Microsoft, GitHub)
      demo.py              — public demo endpoint
      memory.py            — user memory CRUD
      models.py            — user-facing model listing
      notifications.py     — list notifications, mark read, unread count
      prompts.py           — user prompt CRUD
      scheduled_tasks.py   — CRUD + pause/resume for scheduled agent tasks
      skills.py            — user skill CRUD
      templates.py         — user-facing template listing
      users.py             — user management
      widget.py            — embed widget endpoints (guest sessions)
    /agents
      runner.py            — agent assembly, execution, streaming
      stabilizer.py        — DeepSeek stabilizer middleware
      deepseek_patch.py    — DeepSeek monkey-patches
      registry.py          — MAF agent/workflow scanner (scans skills/ and workflows/)
      autopilot.py         — autonomous multi-turn agent controller
      stream_bridge.py     — SSE streaming bridge between agent loop and frontend
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
      autopilot_service.py   — background/autopilot task lifecycle management
      balance_service.py     — per-tenant spending limits and auto-blocking
      credential_service.py  — OAuth token management, per-user tool credentials
      embed_service.py       — embed configuration management
      embedding_service.py   — text embedding generation
      group_service.py       — model/tool group management
      license_service.py     — Ed25519 license verification, tenant gating
      mcp_service.py         — MCP server CRUD, encryption, tool sync
      memory_service.py      — pagination, cross-session retrieval
      model_service.py       — model CRUD
      notification_service.py — user notification CRUD and push
      prompt_service.py      — user prompt CRUD
      rag_service.py         — RAG ingestion, semantic search, doc management
      router_service.py      — LLM model routing and load balancing
      scheduled_task_service.py   — scheduled task CRUD and execution logic
      scheduler_executor.py  — background scheduler polling loop
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
        a2a_call_logs.py       — A2A call history
        a2a_servers.py          — A2A server config with resilience columns
        a2a_tasks.py            — A2A task persistence
        app_settings.py
        audit_logs.py
        autopilot_runs.py       — autopilot execution tracking
        balance_transactions.py — spending limit transactions
        embed_configs.py
        file_uploads.py
        groups.py
        mcp_servers.py
        memory.py
        message_embeddings.py
        messages.py
        models.py
        notifications.py        — user notification records
        prompts.py
        rag.py
        scheduled_tasks.py      — scheduled agent task definitions
        sessions.py
        skills.py
        tags.py
        templates.py
        tenants.py
        tools.py
        usage_logs.py
        user_tool_credentials.py — per-user OAuth/credential storage
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
- Object key format: `uploads/{user_id}/{session_id}/{file_id}-{safe_filename}`
  The filename portion is sanitized via `_sanitize_storage_filename()` in
  `upload_service.py` — Unicode characters are normalized (NFKD → ASCII),
  path separators (`/`, `\\`) replaced, and characters unsafe for HTTP
  headers removed. The original filename is preserved in the DB for display.
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

| Method | Endpoint | Purpose | Auth |
|---|---|---|---|
| `POST` | `/chat/session/:id/upload` | Upload a file (multipart/form-data) | JWT + session owner + temp guard (403) |
| `GET` | `/chat/session/:id/uploads` | List files for a session | JWT + session owner |
| `GET` | `/chat/session/:id/upload/:fileId/url` | Get presigned download URL | JWT + session owner + file owner (403) |
| `DELETE` | `/chat/session/:id/upload/:fileId` | Delete a file | JWT + session owner + file owner (403) |

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
| Allowed MIME types | `text/plain`, `text/csv`, `text/markdown`, `application/pdf`, `application/json`, `image/png`, `image/jpeg`, `image/gif`, `image/webp`, `image/svg+xml`, `image/bmp`, `image/tiff`, `image/avif`, Office formats | `UPLOAD_ALLOWED_TYPES` |

### 10.6 Authorization Model

Every file upload endpoint enforces a **two-layer authorization** model:

1. **Session-level** (`_require_session_owner` in `chat.py`): Verifies the JWT's `user_id` matches the session owner and the `tenant_id` matches the session tenant. Returns `403 Forbidden` on mismatch.
2. **File-level** (`get_upload_by_id` in `upload_service.py`): Verifies the requesting `user_id` matches `file_uploads.user_id`. Returns `403 Forbidden` on mismatch.

Additional guards:
- **Temporary session guard** (in `create_upload`): File uploads are rejected with `403 Forbidden` for sessions still in temporary (Redis-only) mode.
- **Tenant isolation**: The `list_uploads` function filters by both `user_id` and `session_id` in the SQL query; admin endpoints rely on the `require_admin` dependency for broader access.
- **All endpoints** go through JWT authentication first. Unauthenticated requests return `401 Unauthorized`.

Unauthorized access produces a controlled `403` response with a JSON body like:
```json
{"detail": "You do not own this session"}
```
or
```json
{"detail": "You do not own this file upload"}
```

This model ensures defense-in-depth: even if the session-level check were bypassed, the file-level ownership check would still reject unauthorized access.

### 10.7 Temporary Session Rules

- File uploads are **disabled** for temporary sessions (`403`)
- The frontend hides the upload button when the session is in temporary mode
- No file cleanup needed on temp session expiry — uploads were never permitted

### 10.8 File Lifecycle

| Event | Action |
|---|---|
| Session deleted (permanent) | All `file_uploads` rows deleted; objects removed from MinIO |
| User account deleted | All files owned by user deleted from MinIO and DB |
| Tenant deleted | All tenant bucket objects deleted; all rows deleted |
| Temporary session expires | No cleanup needed — uploads blocked |

### 10.9 Filename Sanitization

User-provided filenames are sanitized at two points in the upload lifecycle:

1. **S3/MinIO storage keys** (`upload_service.py:create_upload`): The `_sanitize_storage_filename()` function normalizes the filename to a safe ASCII-only form for use in object keys. This prevents path traversal (`../`), header injection characters (`"`, `;`), null bytes, and Unicode homoglyph attacks.

2. **Download `Content-Disposition` headers** (`chat.py:download_upload`): The `_encode_content_disposition_filename()` function uses **RFC 5987** encoding for filenames containing non-ASCII characters. The header emits both `filename` (ASCII fallback) and `filename*` (percent-encoded UTF-8), ensuring correct browser handling across all modern clients.

The `original_filename` column in the database is **never modified** — it retains the user's original name for display purposes.

### 10.9 Agent Tool Integration

When a message includes attached files, the backend extracts content before the agent loop runs:
- **Text-based files** (PDF, CSV, Markdown, JSON, plain text): extracted and injected into the agent's context window
- **Images**: passed as multi-modal message content if the selected model supports vision

### 10.10 Environment Variables

```
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_PREFIX=phhub-tenant
UPLOAD_MAX_SIZE_BYTES=104857600
UPLOAD_ALLOWED_TYPES=text/plain,text/csv,text/markdown,application/pdf,application/json,image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/bmp,image/tiff,image/avif,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/msword,application/vnd.ms-excel,application/vnd.ms-powerpoint
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
