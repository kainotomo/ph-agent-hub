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
**Reference:** [streaming-protocol.md](../streaming-protocol.md)

---

## D-03 — sse-starlette (backend) and @microsoft/fetch-event-source (frontend)

**Date:** 2026-05-07
**Decision:** Use `sse-starlette` for SSE responses in FastAPI and `@microsoft/fetch-event-source` for SSE consumption in React.
**Rationale:** `sse-starlette` is the standard SSE library for Starlette/FastAPI with minimal boilerplate. `@microsoft/fetch-event-source` is required (over native `EventSource`) because it supports SSE over POST requests — necessary since the message is sent in the request body. The native `EventSource` API only supports GET.
**Alternatives considered:** Native browser `EventSource` (does not support POST).
**Reference:** [streaming-protocol.md](../streaming-protocol.md)

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
**Reference:** [file-upload-architecture.md](../file-upload-architecture.md)

---

## D-06 — Single storage module rule (boto3 calls only in s3.py)

**Date:** 2026-05-07
**Decision:** All MinIO/boto3 interactions are contained in `/backend/src/storage/s3.py`. No service, agent, or API handler calls `boto3` directly.
**Rationale:** Ensures future migration to AWS S3, Cloudflare R2, or Azure Blob Storage requires changes in exactly one file. Azure Blob Storage is not S3-compatible, so a storage abstraction layer may be needed in the future — keeping calls in one module makes that refactor straightforward.
**Alternatives considered:** Full storage abstraction interface now (deferred — not needed until a second backend is required).
**Reference:** [file-upload-architecture.md](../file-upload-architecture.md) §10

---

## D-07 — Fernet/AES-128-CBC for API key encryption

**Date:** 2026-05-07
**Decision:** Use application-level Fernet symmetric encryption (from the Python `cryptography` library) for sensitive DB fields: `models.api_key`, `erpnext_instances.api_key`, `erpnext_instances.api_secret`.
**Rationale:** Simple, no extra infrastructure, well-understood. The encryption key lives in an env var (`ENCRYPTION_KEY`). For a self-hosted platform where physical server access already implies full compromise, this threat model is appropriate. All encrypt/decrypt calls are in one module (`encryption.py`), making it replaceable with Vault or Azure Key Vault later without changing service code.
**Alternatives considered:** DB-level encryption (same single-server trust problem), HashiCorp Vault (adds operational complexity, overkill for this threat model).
**Reference:** [data-model.md](../data-model.md) §8, [backend-architecture.md](../backend-architecture.md) §8

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
