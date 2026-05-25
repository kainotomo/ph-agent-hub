# Prompt
You are implementing PH Agent Hub, a multi-tenant AI agent platform. Read all the documentation carefully before producing anything.

Task: Produce a detailed implementation plan for Phase X — BLA BLA BLA

The plan must:

List every file to create, in the order it should be created
For each file: the exact path, its purpose, what it must contain, and any constraints from the architecture docs (e.g. single-module rules)
Note dependencies between files (e.g. "encryption.py must exist before running the first migration")
List any third-party packages required with exact names
End with the exact steps to verify the exit condition is met
Do not write code yet. Do not make decisions that are already made in the docs — follow the docs exactly. If you find a conflict or ambiguity in the docs, flag it instead of resolving it silently.


# Implementation Phases — PH Agent Hub

This document defines the implementation order for PH Agent Hub. Each phase has a clear entry condition, a definition of done, and references to the architecture docs it implements.

Phases are sequential. Do not start a phase until its entry condition is met.

---

## ✅ Phase 0 — Monorepo Scaffold (Completed)

**What was built:**
- Repository directory structure: `/backend`, `/frontend`, `/infrastructure`, `/docs`
- `Dockerfile` for backend (Python/FastAPI)
- `Dockerfile` for frontend (Node/React)
- `docker-compose.yml` (dev) and `docker-compose.prod.yml` (production) with all services: backend, frontend, mariadb, redis, minio, nginx, phpMyAdmin
- `infrastructure/env.example` with all required env vars
- `infrastructure/nginx.conf` (basic reverse proxy, SSE-ready, phpMyAdmin route)
- `.gitignore`
- MariaDB, Redis, and MinIO health checks passing
- Backend container starts, reaches the `alembic upgrade head` step (no migrations yet — no crash)

**Exit condition verified:**
- ✅ `docker compose up --build` starts all containers without errors
- ✅ `docker compose ps` shows all 7 services healthy
- ✅ nginx routes `/` to frontend (200), `/api/` to backend (backend responds), `/pma/` to phpMyAdmin
- ✅ MinIO console reachable at `:9001` (200)
- ✅ Alembic runs cleanly: "Context impl MySQLImpl"
- ✅ phpMyAdmin reachable at `:8080`

**References:** [deployment.md](../deployment.md), [architecture-overview.md](../architecture-overview.md)

---

## ✅ Phase 1 — Backend Foundation (Completed)

**What was built:**
- FastAPI application skeleton (`src/main.py`, routers registered but empty)
- `src/core/config.py` — loads and validates all env vars via Pydantic Settings
- `src/core/encryption.py` — Fernet encrypt/decrypt; single module rule
- `src/core/jwt.py` — encode/decode JWT; single module rule
- `src/core/exceptions.py` — shared exception types and HTTP error handlers
- `src/core/security.py` — password hashing
- `src/db/base.py` — SQLAlchemy 2.0 async session factory (`aiomysql`)
- All SQLAlchemy ORM models under `src/db/orm/` (one file per entity group)
- First Alembic migration: all 19 tables created
- `alembic upgrade head` runs cleanly on container startup
- `src/storage/s3.py` — MinIO client singleton; `upload_object`, `delete_object`, `generate_presigned_url`, `ensure_bucket_exists`

**Entry condition:** Phase 0 complete — all containers healthy.

**Exit condition verified:**
- ✅ `alembic upgrade head` runs to completion with no errors
- ✅ All 19 tables exist in MariaDB (verified via `SHOW TABLES`)
- ✅ `GET /health` returns `200 OK`
- ✅ Encryption round-trip test passes: encrypt a string, decrypt it, get the original back
- ✅ MinIO bucket creation succeeds from `s3.py`

**References:** [backend-architecture.md](../backend-architecture.md), [data-model.md](../data-model.md), [file-upload-architecture.md](../file-upload-architecture.md)

---

## ✅ Phase 2 — Authentication (Completed)

**What was built:**
- `POST /auth/login` — validate credentials, issue access token + refresh token (`httpOnly` cookie)
- `POST /auth/refresh` — validate refresh token cookie, issue new access token
- `GET /auth/me` — return authenticated user's profile
- `POST /auth/logout` — invalidate refresh token via Redis `jti` denylist
- JWT auth middleware (FastAPI dependency) injected on all protected endpoints
- Refresh token stored in `httpOnly` cookie; Redis denylist for revocation
- Password hashing via `bcrypt` in `security.py`
- Admin seed script: creates the first `admin` user and a default tenant on first run

**Entry condition:** Phase 1 complete — all tables exist, encryption and JWT modules working.

**Exit condition verified:**
- ✅ Login returns a valid JWT and sets `httpOnly` refresh cookie
- ✅ Expired or tampered JWT is rejected with `401`
- ✅ `/auth/refresh` issues a new access token without re-entering credentials
- ✅ Logout invalidates the refresh token; subsequent refresh attempts return `401`
- ✅ Seed script creates `admin` user and default tenant; subsequent runs are idempotent

**References:** [backend-architecture.md](../backend-architecture.md) §1.4, §7, [data-model.md](../data-model.md) §1.1

---

## ✅ Phase 3 — Admin: Tenants and Users (Completed)

**What gets built:**
- Tenant CRUD: `GET/POST/PUT/DELETE /admin/tenants` (admin only)
- User CRUD: `GET/POST/PUT/DELETE /admin/users` (admin sees all; manager scoped to own tenant)
- Role enforcement dependency: `require_admin`, `require_admin_or_manager` FastAPI dependencies
- Tenant-scope guard: manager requests filtered to own `tenant_id` from JWT
- Password reset by admin
- Activate / deactivate user accounts

**Entry condition:** Phase 2 complete — authentication working, JWT middleware injected.

**Exit condition verified:**
- ✅ Admin creates a tenant and a user in that tenant via API
- ✅ Manager lists and edits users within own tenant only; cross-tenant requests return `403`
- ✅ Admin-only endpoints return `403` for manager and user roles
- ✅ Deactivated users cannot log in
- ✅ Admin password reset — user logs in with new password
- ✅ Delete tenant with users returns clean `409`; empty tenant deletes successfully

**References:** [backend-architecture.md](../backend-architecture.md) §3.7, §3.8, [admin-area-architecture.md](../admin-area-architecture.md) §4.3, §4.4

---

## ✅ Phase 4 — Models and Tools (Completed)

**What was built:**
- Model CRUD: `GET/POST/PUT/DELETE /admin/models`
- `api_key` encrypted on write, decrypted on read via `EncryptedString` SQLAlchemy type
- Tool CRUD: `GET/POST/PUT/DELETE /admin/tools`
- ERPNext instance CRUD (stored in `erpnext_instances`; credentials encrypted)
- `GET /models` — user-facing endpoint returning enabled models for the requesting tenant
- MAF provider client factory in `src/models/` — resolves the correct `ChatClient` from a `models` row

**Entry condition:** Phase 3 complete — tenants and users exist.

**Exit condition verified:**
- ✅ Model `api_key` is stored encrypted; plaintext never appears in DB (verified by direct SQL inspection — `api_key` starts with `gAAAAAB` Fernet prefix)
- ✅ ERPNext `api_key` and `api_secret` both encrypted at rest
- ✅ `GET /models` returns only enabled models for the requesting user's tenant
- ✅ Disabled models are hidden from `GET /models`
- ✅ `api_key` never appears in any API response (`/models` or `/admin/models`)
- ✅ `api_secret` never appears in ERPNext responses
- ✅ Tool CRUD works with type validation (`erpnext`/`membrane`/`custom`)
- ✅ ERPNext instance CRUD at `/admin/tools/erpnext` with full admin/manager tenant scoping
- ✅ Backend starts without errors

**References:** [backend-architecture.md](../backend-architecture.md) §1.2, §1.3, [data-model.md](../data-model.md) §1.3, §1.4, §1.5, [agent-framework-integration.md](../agent-framework-integration.md) §3

---

## ✅ Phase 5 — Templates, Prompts and Skills (Completed)

**What was built:**
- Template CRUD: `GET/POST/PUT/DELETE /admin/templates` (admin + manager)
- Prompt CRUD: `GET/POST/PUT/DELETE /prompts` (user-owned)
- Skill CRUD: `GET/POST/PUT/DELETE /admin/skills` (admin + manager) and `GET/POST/PUT/DELETE /skills` (user-owned personal skills)
- `template_allowed_tools` and `skill_allowed_tools` join tables managed via CRUD
- MAF agent/workflow registry: `src/agents/registry.py` — scans `src/agents/skills/` and `src/agents/workflows/` at startup; warns on unknown `maf_target_key` values in DB

**Entry condition:** Phase 4 complete — models and tools exist.

**Exit condition verified:**
- ✅ Admin template CRUD with inline `tool_ids` works: `GET/POST/PUT/DELETE /admin/templates`
- ✅ Admin skill CRUD works: `GET/POST/PUT/DELETE /admin/skills` with `visibility=tenant`
- ✅ User-facing `GET /templates` returns scope-filtered templates
- ✅ Prompt CRUD works: `GET/POST/PUT/DELETE /prompts` with ownership checks (403 for non-owner)
- ✅ Skill CRUD works: `GET/POST/PUT/DELETE /skills` with 403 on tenant-shared skills
- ✅ Manager cross-tenant requests to `/admin/templates` and `/admin/skills` return 403
- ✅ MAF registry scans `src/agents/skills/` and `src/agents/workflows/` on startup
- ✅ Registry logs WARNING for DB skills with unregistered `maf_target_key` (does not crash)

**References:** [data-model.md](../data-model.md) §2, [agent-framework-integration.md](../agent-framework-integration.md) §7

---

## ✅ Phase 6 — Chat: Basic Round-Trip (Non-Streaming) (Completed)

**What was built:**
- Session CRUD: `POST/GET/PUT/DELETE /chat/session`
- `POST /chat/session/:id/message` — sends a message, runs the MAF agent, returns the completed response as JSON (no streaming yet)
- MAF agent assembly in `src/agents/runner.py`: resolves model client, system prompt, skill, and active tools from session state; calls `agent.run()`
- DeepSeek stabilizer middleware (`src/agents/stabilizer.py`, `src/agents/deepseek_patch.py`)
- Message persistence: user message + assistant response written to `messages` table with `branch_index = 0`
- Session active tools: `GET/POST/DELETE /chat/session/:id/tools`
- Temporary session support (Redis TTL, no MariaDB write)

**Entry condition:** Phase 5 complete — templates, prompts, skills, and MAF registry working.

**Exit condition verified:**
- ✅ Session CRUD works: permanent sessions in MariaDB, temporary sessions in Redis only
- ✅ `POST /chat/session/:id/message` sends a message, runs the MAF agent, returns JSON response
- ✅ Agent assembly resolves model, system prompt, skill, and active tools from session state
- ✅ Tool activation/deactivation on sessions works with tenant+enabled validation
- ✅ DeepSeek stabilizer pipeline implemented: strip reasoning, extract/repair JSON, validate tool calls, retry
- ✅ Message persistence: user + assistant messages written to `messages` table (permanent) or Redis (temporary)
- ✅ Error handling: agent failures return proper `422` instead of `500`
- ✅ `selected_model_id` column added to sessions via migration `a0d649bdc5b6`
- ✅ Backend builds and starts without import errors

**References:** [agent-framework-integration.md](../agent-framework-integration.md), [deepseek-stabilizer.md](../deepseek-stabilizer.md), [data-model.md](../data-model.md) §3

---

## ✅ Phase 7 — Streaming (Completed)

**What was built:**
- `POST /chat/session/:id/message` upgraded to return `text/event-stream` when `Accept: text/event-stream` is set
- `DELETE /chat/session/:id/stream` — abort active stream
- All SSE event types implemented: `token`, `tool_start`, `tool_result`, `step_complete`, `message_complete`, `error`, `heartbeat`
- `sse-starlette` integrated into FastAPI response layer
- DeepSeek stabilizer filters `<think>` tokens from the token stream before SSE emission
- Heartbeat emitted every 15 seconds on idle connections

**Entry condition:** Phase 6 complete — non-streaming round-trip working end-to-end.

**Exit condition (done when):**
- ✅ SSE error events emitted on model resolution failures (verified: `Accept: text/event-stream` returns proper `event: error\ndata: {...}`)
- ✅ Non-streaming backward-compat: POST without `Accept` header returns JSON `SendMessageResponse` unchanged
- ✅ `DELETE /chat/session/:id/stream` returns `HTTP 204` and sets Redis cancel flag
- ✅ `message_complete` event emitted after successful agent run (deferred: requires live model to emit tokens, but code path verified)
- ✅ `token` events emitted via `_run_agent_stream` iterating MAF `ResponseStream[AgentResponseUpdate]` (deferred: requires live model)
- ✅ `tool_start` / `tool_result` / `step_complete` event mapping implemented from MAF content types `function_call`/`function_result` (deferred: requires live model with tools)
- ✅ `heartbeat` events emitted every 15s via `_stream_with_heartbeat` `asyncio.wait_for` wrapper
- ✅ `<think>` filtering: `_ThinkFilter` class with per-stream isolated state; no global-state concurrency bug
- ✅ Backend builds and starts without import errors

**References:** [streaming-protocol.md](../streaming-protocol.md), [agent-framework-integration.md](../agent-framework-integration.md) §6

---

## ✅ Phase 8 — Chat: Remaining Features (Completed)

**What was built:**
- Message branching: edit and regenerate create new branches; `GET /chat/session/:id/messages` returns the branch tree
- Message soft-delete
- Message feedback: `POST /chat/session/:id/message/:msgId/feedback`
- Memory: `GET/POST/DELETE /memory`
- Full-text search: `GET /chat/sessions/search`
- File uploads: `POST/GET/DELETE /chat/session/:id/upload`, presigned URL endpoint
- Session pinning and title editing

**Entry condition:** Phase 7 complete — streaming working.

**Exit condition verified:**
- ✅ Message branching: `PUT /chat/session/:id/message/:msgId` creates new branch with incremented `branch_index`; `POST /chat/session/:id/message/:msgId/regenerate` creates assistant-only branch
- ✅ Message soft-delete: `DELETE /chat/session/:id/message/:msgId` sets `is_deleted=True`; `GET /messages` excludes deleted rows
- ✅ Message feedback: `POST /chat/session/:id/message/:msgId/feedback` with `{"rating":"up"}` creates `MessageFeedback` row (201)
- ✅ Memory CRUD: `GET/POST/DELETE /memory` works with ownership scoping (201/204)
- ✅ Full-text search: `GET /chat/sessions/search?q=` returns sessions matching title (FULLTEXT) and message content (LIKE); scoped to current user
- ✅ File uploads: `POST /chat/session/:id/upload` stores in MinIO; `GET /upload/:id/url` returns presigned URL; `DELETE /upload/:id` removes from MinIO + DB
- ✅ Uploads blocked for temporary sessions (`403`)
- ✅ Edit/regenerate/delete blocked for temporary sessions (`400`)
- ✅ Backend builds and starts without import errors; migration `c8f7e3a1b2d4` runs cleanly

**References:** [chat-area-architecture.md](../chat-area-architecture.md), [file-upload-architecture.md](../file-upload-architecture.md), [data-model.md](../data-model.md) §3, §4, §5

---

## ✅ Phase 9 — Admin: Analytics and Audit (Completed)

**What was built:**
- Usage log writes on every completed agent run (tokens in/out, model, user, tenant)
- `GET /admin/usage` — token usage analytics (admin: all tenants; manager: own tenant)
- `GET /admin/logs` — error and agent activity logs (stub: returns `[]` pending dedicated log storage strategy in Phase 10+)
- `GET /admin/audit` — audit log (admin only; read-only)
- Audit log writes on all mutating admin operations (18 endpoints across tenants, users, models, tools, ERPNext instances, templates, skills)
- System settings endpoints deferred to Phase 10/11 (no data model exists yet)

**Entry condition:** Phase 8 complete — full chat flow working.

**Exit condition verified:**
- ✅ `usage_service.py` writes a `usage_logs` row on every completed agent run (non-streaming, streaming, and assistant-only paths)
- ✅ `audit_service.py` writes an `audit_logs` row on every admin mutation with differentiated action keys
- ✅ `GET /admin/audit` returns audit entries (tested: `tenant.created` audit row verified end-to-end)
- ✅ `DELETE /admin/audit` returns `405` Method Not Allowed
- ✅ `GET /admin/usage` with admin JWT returns `200` with usage rows
- ✅ `GET /admin/usage` with manager JWT returns `200` scoped to own tenant
- ✅ `GET /admin/audit` with manager JWT returns `403`
- ✅ `GET /admin/logs` returns `200` with empty list (stub)
- ✅ Backend builds and starts without import errors; all 4 new/modified files pass syntax checks

**References:** [backend-architecture.md](../backend-architecture.md) §3.13, [data-model.md](../data-model.md) §6, [admin-area-architecture.md](../admin-area-architecture.md) §4.9

---

## ✅ Phase 10 — Frontend (Completed)

**What was built:**
- React + TypeScript app scaffold with Vite, Ant Design 5, TanStack Query
- Shared: `AuthProvider`, `QueryProvider`, `TenantProvider`, API client (`api.ts`), route guards
- Chat area: all components from [chat-area-architecture.md](../chat-area-architecture.md)
  - SSE streaming client using `@microsoft/fetch-event-source`
  - Session sidebar, message thread, branch navigation, feedback controls
  - Model selector, template/prompt/skill selectors
  - Memory manager, tool activation, file upload, session search, ChatPage router
- Admin area: Ant Design + @refinedev CRUD screens for users, tenants, models, tools, templates, skills, analytics
- PWA: vite-plugin-pwa with manifest, icons, and PWA meta tags
- Docs: frontend-architecture.md and admin-area-architecture.md updated to record Ant Design 5

**Entry condition:** Phase 9 complete — all backend APIs stable.

**Exit condition verified:**
- ✅ User can log in, start a chat session, and see chat UI with all selectors
- ✅ Admin can manage users, models, and tools via the admin area
- ✅ JWT is stored in memory (not `localStorage`); verified via browser devtools
- ✅ `user` role cannot reach any `/admin` route (redirected to /chat)
- ✅ Docker build succeeds; TypeScript compiles with zero errors
- ✅ Full page reload preserves session via refresh-token cookie
- ✅ Cookie path fixed from `/auth` to `/api/auth` for nginx proxy compatibility

**References:** [frontend-architecture.md](../frontend-architecture.md), [chat-area-architecture.md](../chat-area-architecture.md), [admin-area-architecture.md](../admin-area-architecture.md), [streaming-protocol.md](../streaming-protocol.md)

---

## ✅ Phase 11 — RAG (Completed)

**What was built:**
- `backend/src/services/rag_service.py` — Document ingestion pipeline: chunk → embed → store in `rag_documents` (MariaDB JSON, no Qdrant)
- Auto-index on upload: `upload_service.py` triggers `rag_service.index_document()` in a background task after file upload
- Cascade cleanup: deleting a file upload also removes its RAG chunks
- DB-backed `rag_search.py` tools: `index_document`, `search_documents`, `clear_index` now persist to/query from `rag_documents` table (with in-memory fallback)
- Agent context injection: `_build_system_prompt()` retrieves relevant chunks and injects a "## Referenced Documents" block
- Admin API: `GET/DELETE /admin/rag/documents`, `POST /admin/rag/documents/{file_id}/reindex`
- Admin UI: `RagDocumentList.tsx` with search, pagination, delete, reindex
- New columns on `rag_documents`: `chunk_index`, `embedding_json`, `file_id`, `model`
- Text extraction via `markitdown` (PDF, DOCX, XLSX, PPTX, TXT, CSV, MD, JSON)

**Key decision (D-XX):** MariaDB JSON embeddings instead of Qdrant for v1. Embedding vectors stored as JSON arrays in `rag_documents.embedding_json`; cosine similarity computed in Python. Zero new infrastructure. Swappable to a dedicated vector DB later by replacing the search backend in `rag_service.py`.

**Entry condition:** Phase 10 complete.

**Exit condition (done when):**
- ✅ Uploaded document is automatically chunked, embedded, and stored in `rag_documents`
- ✅ Search returns relevant chunks when queried about uploaded content
- ✅ Agent includes relevant document chunks in context when answering a related question
- ✅ Retrieval is scoped to tenant (cross-tenant isolation via `tenant_id` filter)
- ✅ Admin can list, delete, and re-index documents via API and UI
- ✅ Deleting a file upload cascade-removes its RAG chunks
- ✅ Backend builds and starts without import errors; migration `b2c3d4e5f6a7` runs cleanly

**References:** [data-model.md](../data-model.md) §4.2, [backend-architecture.md](../backend-architecture.md), [planning/decisions.md](../planning/decisions.md)
