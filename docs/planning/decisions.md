# Design Decisions — PH Agent Hub

A log of key design decisions made during the design phase, with rationale. Ordered chronologically. Each entry links to the doc where the decision is implemented.

---

## D-01 — Microsoft Agent Framework (MAF) as the agent runtime

**Date:** 2026-05-07
**Decision:** Use the Microsoft Agent Framework (Python, `pip install agent-framework`) as the agent execution runtime.
**Rationale:** Production-grade, open source (MIT), supports multi-agent workflows, graph-based orchestration, middleware, streaming, and OpenTelemetry out of the box. Supports migration from AutoGen and Semantic Kernel. Active community (10k+ stars, 139 contributors).
**Alternatives considered:** AutoGen, Semantic Kernel, custom agent loop.
**Reference:** [agent-framework-integration.md](../agent-framework-integration.md)

---

## D-02 — SSE over WebSocket for streaming

**Date:** 2026-05-07
**Decision:** Use Server-Sent Events (SSE) for streaming agent responses to the frontend. WebSocket is not used.
**Rationale:** Chat streaming is unidirectional (server → client). SSE is designed for this. Works through nginx without special proxy configuration. Easier to debug and test than WebSocket. Stop-generation is handled by a separate `DELETE` HTTP request.
**Alternatives considered:** WebSocket (bidirectional, adds proxy complexity with no benefit for this workload).
**Reference:** [backend-architecture.md](../backend-architecture.md) §11

---

## D-03 — sse-starlette (backend) and @microsoft/fetch-event-source (frontend)

**Date:** 2026-05-07
**Decision:** Use `sse-starlette` for SSE responses in FastAPI and `@microsoft/fetch-event-source` for SSE consumption in React.
**Rationale:** `sse-starlette` is the standard SSE library for Starlette/FastAPI with minimal boilerplate. `@microsoft/fetch-event-source` is required (over native `EventSource`) because it supports SSE over POST requests — necessary since the message is sent in the request body. The native `EventSource` API only supports GET.
**Alternatives considered:** Native browser `EventSource` (does not support POST).
**Reference:** [backend-architecture.md](../backend-architecture.md) §11

---

## D-04 — Docker Compose for deployment (not Kubernetes)

**Date:** 2026-05-07
**Decision:** Use Docker Compose as the production deployment method. No Kubernetes.
**Rationale:** The platform is designed for single-VPS deployment. Docker Compose is simpler to operate, has lower resource overhead, and is sufficient for the target deployment scale. Kubernetes would add operational complexity (cluster management, ingress controllers, Helm) with no benefit for a single-server workload. If horizontal scaling is needed later, a load balancer in front of replicated backend services is sufficient.
**Alternatives considered:** Kubernetes (rejected — overkill for single-server, adds operational complexity), raw Docker (docker-compose provides better orchestration for multi-container stacks).
**Reference:** [deployment.md](../deployment.md)

---

## D-05 — MinIO for file upload object storage

**Date:** 2026-05-07
**Decision:** Use MinIO (self-hosted, S3-compatible) as the object storage backend for file uploads.
**Rationale:** S3-compatible API from day one means migrating to AWS S3 or Cloudflare R2 in the future requires only changing env vars, not code. Works for single-server and multi-server deployments. Supports presigned URLs. Runs as a Docker container inside the existing stack. Local disk was rejected because it breaks multi-container deployments and has no presigned URL support.
**Alternatives considered:** Local disk (rejected — not multi-container safe, no migration path to cloud without code rewrite), AWS S3/Cloudflare R2 (premature for a self-hosted system), Ceph (too heavy).
**Reference:** [backend-architecture.md](../backend-architecture.md) §10

---

## D-06 — Single storage module rule (boto3 calls only in s3.py)

**Date:** 2026-05-07
**Decision:** All MinIO/boto3 interactions are contained in `/backend/src/storage/s3.py`. No service, agent, or API handler calls `boto3` directly.
**Rationale:** Ensures future migration to AWS S3, Cloudflare R2, or Azure Blob Storage requires changes in exactly one file. Azure Blob Storage is not S3-compatible, so a storage abstraction layer may be needed in the future — keeping calls in one module makes that refactor straightforward.
**Alternatives considered:** Full storage abstraction interface now (deferred — not needed until a second backend is required).
**Reference:** [backend-architecture.md](../backend-architecture.md) §10.1

---

## D-07 — Fernet/AES-128-CBC for API key encryption

**Date:** 2026-05-07
**Decision:** Use application-level Fernet symmetric encryption (from the Python `cryptography` library) for sensitive DB fields: `models.api_key`.
**Rationale:** Simple, no extra infrastructure, well-understood. The encryption key lives in an env var (`ENCRYPTION_KEY`). For a self-hosted platform where physical server access already implies full compromise, this threat model is appropriate. All encrypt/decrypt calls are in one module (`encryption.py`), making it replaceable with Vault or Azure Key Vault later without changing service code.
**Alternatives considered:** DB-level encryption (same single-server trust problem), HashiCorp Vault (adds operational complexity, overkill for this threat model).
**Reference:** [data-model.md](../data-model.md) §8, [backend-architecture.md](../backend-architecture.md) §8

> **Note:** The original decision also listed `erpnext_instances.api_key` and `erpnext_instances.api_secret`. The dedicated `erpnext_instances` table was later dropped — ERPNext credentials are now stored in `tools.config` and encrypted using the same Fernet mechanism.

---

## D-08 — No permissions field in JWT; role-only claims

**Date:** 2026-05-07
**Decision:** The JWT payload contains only `sub` (user_id), `tenant_id`, `role`, `exp`, and `iat`. There is no `permissions` array.
**Rationale:** The three roles (`admin`, `manager`, `user`) are rigid and fully defined — a manager always has exactly the same capabilities as every other manager. A permissions array would be redundant and creates a split-brain risk: if the token's claims diverge from DB state (e.g. a role change before token expiry), the backend would enforce stale permissions. All access decisions are derived from `role` at request time via a FastAPI dependency.
**Alternatives considered:** Fine-grained permissions array in JWT (rejected — nothing to express that role doesn't already cover; introduces staleness risk).
**Reference:** [backend-architecture.md](../backend-architecture.md) §7

---

## D-09 — Refresh token as httpOnly cookie with Redis jti denylist

**Date:** 2026-05-07
**Decision:** Refresh tokens are issued as `httpOnly` cookies. Logout invalidates the token via a Redis denylist keyed by `jti` claim. Access tokens are stored in memory (not localStorage).
**Rationale:** `httpOnly` cookies are not accessible to JavaScript, eliminating XSS token theft. Redis denylist enables immediate server-side invalidation on logout. Memory-only access tokens mean no persistent token survives a browser close.
**Alternatives considered:** localStorage (vulnerable to XSS), stateless JWT-only logout (cannot revoke before expiry).
**Reference:** [backend-architecture.md](../backend-architecture.md) §7

---

## D-10 — Append-only audit log; no delete endpoint

**Date:** 2026-05-07
**Decision:** The `audit_logs` table is append-only. No API endpoint exposes a delete operation on audit records. Retention purge is handled by a scheduled background job only.
**Rationale:** Audit logs that can be deleted are not audit logs. Admin-triggered deletion would undermine the forensic value of the log. A scheduled purge with a configurable retention period satisfies storage concerns without exposing a delete API.
**Reference:** [data-model.md](../data-model.md) §6.2

---

## D-11 — MariaDB full-text indexes for chat search (no dedicated search engine)

**Date:** 2026-05-07
**Decision:** Full-text search across sessions and messages is implemented via MariaDB full-text indexes on `sessions.title` and text parts within `messages.content`. No dedicated search engine (Elasticsearch, Meilisearch) is introduced.
**Rationale:** Search is scoped to a single user's own data within their tenant — the result set is small. MariaDB full-text search is sufficient for this scope and avoids adding another service to the stack. A dedicated search engine can be introduced later if requirements grow.
**Alternatives considered:** Meilisearch, Elasticsearch (deferred — not justified by scope).
**Reference:** [data-model.md](../data-model.md) §3.3

---

## D-12 — MariaDB JSON embeddings for RAG (no dedicated vector DB)

**Date:** 2026-05-25
**Decision:** RAG embedding vectors are stored as JSON arrays in the `rag_documents.embedding_json` column. Cosine similarity is computed in Python. No dedicated vector database (Qdrant, pgvector, Milvus) is introduced for v1.
**Rationale:** For a self-hosted platform where users upload dozens to hundreds of documents (not millions), MariaDB JSON with in-application cosine similarity is fast enough. Loading all embeddings for a tenant (even 10,000 chunks × 256-dim = ~20 MB) and computing similarity in Python completes in milliseconds. This avoids adding Qdrant or another Docker service, saving RAM, disk, and operational complexity. The `embedding_json` column is a drop-in replacement — swapping to a vector DB in the future requires only changing the search backend in `rag_service.py`, with zero changes to the ingestion pipeline or data model.
**Alternatives considered:** Qdrant (deferred — infrastructure cost not justified by current scale), pgvector (not applicable — MariaDB, not PostgreSQL).
**Reference:** [data-model.md](../data-model.md) §4.2

---

## D-13 — Server-side OAuth state nonce store (Redis)

**Date:** 2026-06-16
**Decision:** OAuth state parameters are stored as server-side nonces in Redis using `SETEX` and consumed atomically via `GETDEL`. The nonce is a random UUID v4 — no user/tool information is embedded in the plaintext state parameter. Each nonce has a 10-minute TTL and is single-use (deleted on first callback consumption).
**Rationale:** The previous state format (`user_id:tool_id:8-char-hex`) was unsigned, non-expiring, and replayable. An attacker could forge or replay state to bind credentials to the wrong user context. A Redis-backed nonce store provides tamper evidence (no embedded info), automatic expiry (TTL), and one-time use (atomic GETDEL) without introducing new cryptographic primitives or infrastructure. The codebase already uses Redis for session storage, rate limiting, and JTI denylist — this follows the same established patterns.
**Alternatives considered:** Fernet-signed state (replayable within TTL without server-side tracking; still requires server-side nonce to enforce one-time use), local in-memory dict (lost on reload, not shared across workers).
**Reference:** `backend/src/core/redis.py` (`store_oauth_state`, `get_oauth_state`), [Issue #345](https://github.com/kainotomo/ph-agent-hub/issues/345)

---

## D-14 — Per-user ERPNext credentials with tenant fallback

**Date:** 2026-07-02
**Decision:** ERPNext tool credentials can be configured at two levels: tenant-level `tool.config` (admin setup) and per-user `UserToolCredential` (user connects their own account via Account Settings). Per-user credentials take precedence; tenant-level config acts as the fallback default.
**Rationale:** In multi-tenant deployments, different users connect to different ERPNext sites or have distinct API keys/permissions. Sharing a single tenant-level API key loses audit trails and forces all users into the same identity. The email/calendar/tasks tools already follow this pattern (Issue #312), proving the architecture. Existing tenant-level setups continue working unchanged.
**Alternatives considered:** Tenant-level only (rejected — breaks multi-user ERPNext use cases), per-user only with no fallback (rejected — breaks backward compatibility for existing setups).
**Reference:** `backend/src/tools/erpnext.py` (`_resolve_erpnext_credentials`, `build_erpnext_tools`), `backend/src/agents/runner.py` (`_build_erpnext_callables`), [Issue #432](https://github.com/kainotomo/ph-agent-hub/issues/432)

---

## D-15 — Auto tool selection with top-K diversity

**Date:** 2026-07-03
**Decision:** The LLM can automatically select relevant tools from the available pool without requiring manual activation per session. The number of tools presented is capped by `AUTO_SELECT_TOOLS_TOP_K` (default 8). Tools are randomly sampled from the pool to improve selection diversity.
**Rationale:** Manual tool activation is tedious for users with many tools. Auto-selection lets the LLM pick dynamically based on the user's request. Random sampling prevents the same tools from always being selected (Issue #439).
**Alternatives considered:** Always-on all tools (rejected — too many tools degrades LLM quality), strict keyword matching (rejected — too brittle).
**Reference:** `backend/src/core/config.py` (`AUTO_SELECT_TOOLS_TOP_K`), [Issue #439](https://github.com/kainotomo/ph-agent-hub/issues/439)

---

## D-16 — Parallel tool execution via asyncio.gather

**Date:** 2026-07-20
**Decision:** When the LLM returns multiple `tool_calls` in a single response for independent operations, MAF executes them concurrently via `asyncio.gather`. System prompt guidance encourages the LLM to batch independent calls.
**Rationale:** Reduces total execution time for multi-tool workflows. MAF natively supports concurrent tool execution. Parallel batches count as a single step toward `AGENT_MAX_STEPS`, preventing premature termination.
**Alternatives considered:** Sequential execution (rejected — slower for independent operations).
**Reference:** `backend/src/core/config.py` (`AGENT_PARALLEL_TOOLS_ENABLED`), [Issue #447](https://github.com/kainotomo/ph-agent-hub/issues/447)

---

## D-17 — Agent Autopilot with pause/resume

**Date:** 2026-07-21
**Decision:** An autonomous multi-turn execution mode where the agent loops without requiring user prompts. Autopilot supports pause, resume, and stop controls. Limits: `AUTOPILOT_MAX_TURNS` (default 20), `AUTOPILOT_MAX_TOKENS` (default 0 = unlimited).
**Rationale:** Users need the agent to work through complex multi-step tasks without constant prompting. Pause/resume allows users to inspect progress and redirect the agent mid-task. Turn and token limits prevent runaway execution.
**Alternatives considered:** Single-turn only (rejected — too limiting for complex tasks), always-on autopilot (rejected — safety concerns).
**Reference:** `backend/src/agents/autopilot.py`, `backend/src/core/config.py`, [Issue #446](https://github.com/kainotomo/ph-agent-hub/issues/446)

---

## D-18 — Background tasks with progress tracking

**Date:** 2026-07-23
**Decision:** Long-running agent executions can run as background tasks independent of the user's current chat session. Tasks support progress tracking, cancellation, and completion notifications.
**Rationale:** Users should be able to start a long-running task and navigate away without losing progress. Background tasks use the same autopilot execution infrastructure but run in a detached mode.
**Alternatives considered:** Foreground-only execution (rejected — blocks user from doing other work).
**Reference:** `backend/src/api/background_tasks.py`, `backend/src/core/config.py` (`MAX_CONCURRENT_BACKGROUND_TASKS_PER_USER`, `BACKGROUND_TASK_TIMEOUT_SECONDS`), [Issue #449](https://github.com/kainotomo/ph-agent-hub/issues/449)

---

## D-19 — Scheduled tasks with cron-based scheduling

**Date:** 2026-07-23
**Decision:** Time-based autonomous agent execution supporting one-shot (run at datetime) and recurring (cron expression) schedules. A polling loop checks for due tasks at `SCHEDULER_POLL_INTERVAL_SECONDS` (default 30).
**Rationale:** Users need agents to run autonomously at specific times (e.g., daily reports, weekly analyses) without manual initiation. Cron expressions provide flexible scheduling.
**Alternatives considered:** Dedicated scheduler service (rejected — adds infrastructure complexity; in-process polling is sufficient).
**Reference:** `backend/src/api/scheduled_tasks.py`, `backend/src/services/scheduler_executor.py`, `backend/src/core/config.py`, [Issue #297](https://github.com/kainotomo/ph-agent-hub/issues/297)

---

## D-20 — In-app notifications for agent events

**Date:** 2026-07-23
**Decision:** Persistent in-app notification records for background task completion, scheduled task events, and other agent-triggered events. Notifications are stored in MariaDB with read/unread state. API provides list, mark read, mark all read, and unread count endpoints.
**Rationale:** Users need asynchronous notification when background tasks complete. In-app notifications are simpler than email/push infrastructure and don't require external services. DB storage ensures notifications survive server restarts.
**Alternatives considered:** Email notifications (rejected — adds SMTP infrastructure), push notifications (rejected — requires service worker and FCM/APNs, not available on all platforms).
**Reference:** `backend/src/api/notifications.py`, `backend/src/services/notification_service.py`, `backend/src/db/orm/notifications.py`

---

## D-21 — Goal-based skills execution type

**Date:** 2026-07-23
**Decision:** Added `goal_based` execution type to skills. Users define an objective (e.g., "Analyze Q3 reports"), and the agent autonomously plans and executes steps to achieve it.
**Rationale:** Complex tasks are better expressed as goals rather than detailed prompts. The agent's planning capabilities can break down objectives into actionable steps.
**Alternatives considered:** Prompt-based only (rejected — requires users to specify every step).
**Reference:** `backend/src/db/orm/skills.py` (execution_type enum), frontend skill creation UI, [Issue #448](https://github.com/kainotomo/ph-agent-hub/issues/448)
