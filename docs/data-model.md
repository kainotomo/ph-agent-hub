# Data Model — PH Agent Hub

This document defines the database schema for PH Agent Hub.  
The platform uses MariaDB as the primary relational database and Redis for caching, queues, and ephemeral memory.

The schema is implemented as **SQLAlchemy 2.0 ORM models** (one file per entity group under `/backend/src/db/orm/`). Schema changes are versioned and applied via **Alembic** migration scripts. The tables, columns, and constraints defined below map directly to those ORM models.

Because MariaDB is the primary store, relationships that require joins or referential integrity are normalized into dedicated tables. JSON columns are reserved for flexible payloads that are typically stored and retrieved as complete documents.

The data model supports:
- multi‑tenant architecture
- user/role management (admin, manager, user)
- model and tool configuration
- ERPNext instance routing
- curated templates, user prompts, and reusable skills
- personal user-owned skills
- chat sessions and messages
- memory and RAG documents
- session-level tool activation per user

---

# 1. Core Entities

## 1.1 Users

Users belong to a single tenant and authenticate via JWT.
Roles:
- **admin** — platform-wide superuser; manages all tenants, platform settings, and global configuration
- **manager** — tenant-scoped operator; can create and manage tools, models, templates, skills, and users within their own tenant only
- **user** — end user; accesses the chat area within their tenant
**Table: users**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- email (string, unique)
- password_hash (string)
- display_name (string)
- role (enum: admin, manager, user)
- is_active (boolean)
- created_at (timestamp)
- updated_at (timestamp)

---

## 1.2 Tenants

Tenants isolate:
- users
- models
- tools
- ERPNext instances
- templates
- prompts
- skills
- sessions

**Table: tenants**
- id (UUID, PK)
- name (string, unique)
- is_demo (boolean, default false) — marks this tenant as the demo tenant (only one can be true at a time)
- balance_euros (decimal(12,6), nullable) — current euro balance; NULL means unlimited (no limit enforced), any numeric value means a spending limit is active
- warning_threshold_eur (decimal(12,6), nullable) — when the balance drops at or below this value, the admin dashboard shows a warning banner and the tenant row displays a ⚠️ badge
- created_at (timestamp)
- updated_at (timestamp)

### 1.2.1 Balance Transactions

Every balance change (admin top-up, usage deduction, limit disable) is recorded as an immutable audit log entry. Positive amounts are top-ups; negative amounts are deductions.

**Table: balance_transactions**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id, indexed) — the affected tenant
- admin_user_id (UUID, FK → users.id, nullable) — the admin who performed the change; NULL for system-triggered deductions
- amount_eur (decimal(12,6)) — positive = funds added, negative = funds deducted
- balance_after (decimal(12,6)) — the tenant's balance immediately after this transaction
- reason (string(255)) — human-readable reason (e.g., `admin_top_up`, `usage_deduction`, `admin_adjustment`, `admin_disabled`)
- reference_type (string(50), nullable) — e.g., `usage_log` for auto-deductions
- reference_id (UUID, nullable) — ID of the referenced entity (e.g., the usage log entry)
- created_at (timestamp)

---

## 1.3 Models

Administrators configure models per tenant.

**Table: models**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- name (string) — e.g., "deepseek-r1"
- model_id (string) — provider's model identifier, e.g., "deepseek-reasoner"
- provider (string) — e.g., "deepseek", "openai", "anthropic"
- api_key (string) — stored encrypted using Fernet symmetric encryption; decrypted in memory at runtime by `/backend/src/core/encryption.py`
- base_url (string, nullable)
- enabled (boolean)
- is_public (boolean, default false) — when true, model is available to all users regardless of group membership
- max_tokens (int)
- temperature (float)
- thinking_enabled (boolean) — supports reasoning/thinking mode (DeepSeek R1)
- follow_up_questions_enabled (boolean) — generates follow-up question suggestions
- context_length (int, nullable) — model's maximum context window in tokens
- routing_priority (int)
- created_at (timestamp)
- updated_at (timestamp)

---

## 1.4 Tools

Tools represent external integrations (ERPNext, Membrane, custom tools).

**Table: tools**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- name (string)
- type (enum: erpnext, membrane, custom, datetime, web_search, fetch_url, weather, calculator, wikipedia, rss_feed, currency_exchange, market_overview, etf_data, stock_data, portfolio, sec_filings, code_interpreter, sql_query, document_generation, browser, rag_search, github, calendar, image_generation, slack, email, mcp, tasks)
- config (JSON)
- enabled (boolean)
- is_public (boolean, default false) — when true, tool is available to all users regardless of group membership
- created_at (timestamp)
- updated_at (timestamp)

---

## 1.5 ERPNext (Tool Type)

ERPNext integration is handled through the tool system. An ERPNext tool instance is created via the `/admin/tools` endpoint with `type=erpnext`. Credentials can be provided at two levels:

1. **Tenant-level** — Admin sets `base_url`, `api_key`, `api_secret` in `tools.config` (the original approach). This serves as the fallback default for all users.
2. **Per-user** — Users connect their own ERPNext site via **Account Settings** (`/settings`). Per-user credentials are stored in `user_tool_credentials` and take precedence over tenant-level config.

Tenant-level `tools.config` example:

```json
{
  "base_url": "https://erpnext.example.com",
  "api_key": "encrypted...",
  "api_secret": "encrypted...",
  "version": "v15"
}
```

Credentials in `tools.config` are encrypted at rest via the `EncryptedString` ORM column type when written through the tool management API. The dedicated `erpnext_instances` table was dropped in an earlier migration — all ERPNext configuration is now unified under the tools table.

---

## 1.6 User Tool Preferences

Users can mark tools as "always on" so they're automatically activated in new sessions.

**Table: user_tool_preferences**
- user_id (UUID, FK → users.id, PK)
- tool_id (UUID, FK → tools.id, PK)
- always_on (boolean, default false)
- created_at (timestamp)
- updated_at (timestamp)

---

## 1.7 User Tool Credentials (Issue #312)

Per-user credentials for tools that require personal authentication (email, calendar, tasks, erpnext). Each row represents one connected account — a user can have multiple accounts for the same tool type (e.g., Work Gmail + Personal Gmail).

Credentials and OAuth tokens are encrypted at rest via the `EncryptedString` ORM column type, using the same Fernet encryption as the `models.api_key` field.

**Table: user_tool_credentials**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id, CASCADE) — tenant scope for tenant-isolation filtering (added in migration e9f8d7c6b5a4)
- user_id (UUID, FK → users.id, CASCADE) — who owns this credential
- tool_id (UUID, FK → tools.id, CASCADE) — which tool this credential is for
- label (string, 255, NOT NULL) — user-defined display name (e.g., "Work Gmail")
- provider (enum: gmail, outlook, imap, google, microsoft, erpnext) — the authentication provider
- email_address (string, 255, nullable) — primary email address for this account
- credentials (Text, nullable) — encrypted JSON: IMAP/SMTP passwords, client IDs, etc.
- oauth_tokens (Text, nullable) — encrypted JSON: access_token, refresh_token, expires_at
- is_default (boolean, default false) — when true, this account is used when no account_label is specified
- status (enum: active, expired, revoked, error) — whether the credential is usable
- created_at (timestamp)
- updated_at (timestamp)

**Unique constraint:** `(user_id, tool_id, email_address)` — prevents registering the same email for the same tool twice.

**Relationships:**
- Belongs to a `Tenant` (CASCADE delete) — enables direct tenant-scoped queries
- Belongs to a `User` (CASCADE delete)
- Belongs to a `Tool` (CASCADE delete)

**Migration note:** The `tenant_id` column was added via migration `e9f8d7c6b5a4`. Existing rows were backfilled by joining through `users.tenant_id`. The `create_credential()` service function populates `tenant_id` from the creating user's tenant on every new insert.

**Credential resolution at runtime:**
1. When the agent run resolves tools, it queries `user_tool_credentials` for the current user
2. Active credentials are passed to the tool factory (`build_email_tools`, `build_calendar_tools`, `build_tasks_tools`)
3. User credentials override tenant-level `tool.config` for the same fields
4. Tools with connected accounts are always included in the agent's tool set (even when auto-select would otherwise exclude them)

---

MCP servers allow connecting external tool sources without writing custom integration code. Each server exposes tools discovered via the MCP protocol that are registered as individual Tool records with `type="mcp"`.

**Table: mcp_servers**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- name (string, 255)
- transport (enum: stdio, streamable_http, websocket)
- url (string, 2048, nullable) — for HTTP/WebSocket transports
- command (string, 1024, nullable) — for stdio transport
- args (JSON, nullable)
- env_vars (Text, nullable) — Fernet-encrypted JSON dict of environment variables
- headers (Text, nullable) — Fernet-encrypted JSON dict of HTTP headers
- allowed_tools (JSON, nullable) — null = all tools allowed; list = subset of tool names
- enabled (boolean, default true)
- created_at (timestamp)
- updated_at (timestamp)

**Table: mcp_synced_tools** (virtual — synced tools are Tool records with `type='mcp'` and `config.mcp_server_id` referencing the MCP server)

When synced, each discovered tool creates or updates a Tool record with:
- `type`: `mcp`
- `name`: `{Server Name}: {tool_name}`
- `config.mcp_server_id`: reference to the MCP server record
- `config.tool_name`: the tool's original name on the server

Tools that were previously synced but no longer appear on the server are soft-deprecated (`enabled = false`).

---

## 1.8 Groups (Access Control)

Groups restrict which models and tools specific users can access. Models and tools marked `is_public` are available to all users regardless of group membership.

**Table: user_groups**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- name (string, 255)
- created_at (timestamp)
- updated_at (timestamp)

**Table: user_group_members**
- user_id (UUID, FK → users.id, PK)
- group_id (UUID, FK → user_groups.id, PK)
- created_at (timestamp)

**Table: model_groups**
- model_id (UUID, FK → models.id, PK)
- group_id (UUID, FK → user_groups.id, PK)
- created_at (timestamp)

**Table: tool_groups**
- tool_id (UUID, FK → tools.id, PK)
- group_id (UUID, FK → user_groups.id, PK)
- created_at (timestamp)

---

## 1.8 Embed Configurations

**Table: embed_configs**

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Primary key |
| `tenant_id` | UUID (FK → tenants) | Owning tenant |
| `name` | VARCHAR(255) | Display name |
| `guest_token_hash` | VARCHAR(255) | SHA-256 hash of the guest token (raw token never stored) |
| `is_active` | BOOLEAN | Whether the config is active |
| `theme` | JSON | Widget theme: `primary_color`, `logo_url`, `greeting_text`, `position` |
| `feature_flags` | JSON | Feature toggles: `file_upload`, `model_selection`, `feedback`, `follow_up_questions`, `memory` |
| `default_model_id` | UUID (FK → models) | Optional default AI model |
| `default_skill_id` | UUID (FK → skills) | Optional default skill |
| `default_template_id` | UUID (FK → templates) | Optional default template |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

**Relationships:**
- Belongs to a `Tenant`
- Optionally refers to a default `Model`, `Skill`, and `Template`

---

# 2. Templates, Prompts & Skills

## 2.1 Templates

Templates are curated prompt assets managed by administrators. They provide approved starting configurations for end users and skills.

Templates can be:
- tenant‑wide
- role‑restricted
- explicitly assigned to a specific user

**Table: templates**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- title (string)
- description (string)
- system_prompt (text)
- default_model_id (UUID, FK → models.id, nullable)
- scope (enum: tenant, role, user)
- assigned_user_id (UUID, FK → users.id, nullable)
- created_at (timestamp)
- updated_at (timestamp)

Allowed tools are normalized through a join table rather than stored as a JSON array.

**Table: template_allowed_tools**
- template_id (UUID, FK → templates.id)
- tool_id (UUID, FK → tools.id)
- created_at (timestamp)

## 2.2 Prompts

Prompts are reusable user-authored instruction assets. They are personal by default, but the model allows future tenant sharing.

**Table: prompts**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- template_id (UUID, FK → templates.id, nullable)
- title (string)
- description (string)
- content (text)
- visibility (enum: private, tenant)
- created_at (timestamp)
- updated_at (timestamp)

## 2.3 Skills

Skills are reusable execution profiles for the Microsoft Agent Framework. A skill can point to a registered agent or workflow and bundle together defaults such as model, prompt, template, and allowed tools.

Skills can be:
- tenant‑shared and administrator managed
- user‑specific and privately owned

**Table: skills**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id, nullable)
- title (string)
- description (string)
- execution_type (enum: agent, workflow)
- maf_target_key (string) — registered Microsoft Agent Framework agent or workflow identifier
- template_id (UUID, FK → templates.id, nullable)
- default_prompt_id (UUID, FK → prompts.id, nullable)
- default_model_id (UUID, FK → models.id, nullable)
- visibility (enum: tenant, user)
- enabled (boolean)
- created_at (timestamp)
- updated_at (timestamp)

**Table: skill_allowed_tools**
- skill_id (UUID, FK → skills.id)
- tool_id (UUID, FK → tools.id)
- created_at (timestamp)

---

# 3. Chat Sessions & Messages

## 3.1 Sessions

A session belongs to a user and a tenant.

**Table: sessions**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- title (string)
- is_temporary (boolean, default false) — temporary sessions are not persisted to MariaDB; they are stored in Redis with a TTL and purged on logout or expiry
- is_pinned (boolean, default false) — pinned sessions appear at the top of the session list
- selected_template_id (UUID, FK → templates.id, nullable)
- selected_prompt_id (UUID, FK → prompts.id, nullable)
- selected_skill_id (UUID, FK → skills.id, nullable)
- selected_model_id (UUID, FK → models.id, nullable)
- thinking_enabled (bool, nullable) — session-level override for reasoning mode; null means use model default
- reasoning_effort (string, nullable) — session-level reasoning-effort level (e.g. low/high/max for DeepSeek thinking mode); null means use model default
- cross_session_retrieval_enabled (boolean, default false) — when enabled, the agent can semantically retrieve memory entries from other sessions during the conversation
- temperature (float, nullable) — session-level temperature override
- created_at (timestamp)
- updated_at (timestamp)

Sessions have a many-to-many relationship with tags via the `session_tags` join table. Tags are generated automatically after each agent response (3–5 topic tags).

**Table: session_tags**
- session_id (UUID, FK → sessions.id)
- tag_id (UUID, FK → tags.id)
- created_at (timestamp)

**Table: tags**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- name (string, 50) — unique per tenant
- color (string, nullable) — hex color for badge display
- created_at (timestamp)

Active tools for a session are tracked via a join table. A user can only activate tools that are enabled for their tenant. Users can also mark tools as "always on" to automatically activate them in new sessions.

**Table: session_active_tools**
- session_id (UUID, FK → sessions.id)
- tool_id (UUID, FK → tools.id)
- created_at (timestamp)

---

## 3.2 Messages

Messages belong to a session and support branching. Editing a message or regenerating a response creates a new branch rather than overwriting the original. The active branch is tracked per session.

Message content is a structured JSON array of parts to support multi-modal messages (text, images, tool output). This allows mixed content and rich rendering without a schema migration later.

**Table: messages**
- id (UUID, PK)
- session_id (UUID, FK → sessions.id)
- parent_message_id (UUID, FK → messages.id, nullable) — null for the root message of each branch
- branch_index (int, default 0) — position within sibling branches sharing the same parent
- sender (enum: user, assistant, system)
- content (JSON array of parts) — each part has a `type` (text | image | tool_output) and corresponding payload
- model_id (UUID, FK → models.id, nullable)
- tool_calls (JSON)
- tokens_in (int, nullable) — input token count for this message
- tokens_out (int, nullable) — output token count for this message
- is_deleted (boolean, default false) — soft delete; message is hidden but branch integrity is preserved
- summarized (boolean, default false) — whether this message has been compressed into a summary
- created_at (timestamp)
- updated_at (timestamp)

**Table: message_feedback**
- id (UUID, PK)
- message_id (UUID, FK → messages.id)
- user_id (UUID, FK → users.id)
- rating (enum: up, down)
- comment (text, nullable)
- created_at (timestamp)

Feedback is recorded for assistant messages only and is used for model quality analytics.

---

## 3.3 Search

Full-text search across a user's sessions and messages is supported via MariaDB full-text indexes on `sessions.title` and the text parts within `messages.content`. Search is scoped to the authenticated user's own data within their tenant.

---

# 4. Memory & RAG

## 4.1 Memory Items

Memory is stored per user and optionally scoped to a session. Users can view, delete, and manually add memory entries via the chat area.

**Cross-session memory retrieval** (v1.10): When a session has `cross_session_retrieval_enabled = true`, the agent performs semantic embedding-based search across all of the user's memory entries (from all sessions) and injects relevant matches into the context window. This allows the AI to remember information learned in past conversations.

Memory listing supports pagination via `?page=&page_size=` query parameters. When a `session_id` filter is provided, global memory entries (`session_id IS NULL`) are also included alongside session-scoped ones.

**Table: memory**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- session_id (UUID, FK → sessions.id, nullable)
- key (string)
- value (text)
- source (enum: automatic, manual) — whether the entry was created by the agent or manually by the user
- created_at (timestamp)

---

## 4.2 RAG Documents

Implemented in v1.11 (Issue #250). Document chunk-level storage with MariaDB JSON embeddings. No external vector DB required — cosine similarity computed in Python. Swappable to Qdrant or pgvector later by replacing the `rag_service` backend.

**Table: rag_documents**
- id (UUID, PK) — formatted as `{file_id}_{chunk_index}`
- tenant_id (UUID, FK → tenants.id)
- file_id (UUID, FK → file_uploads.id, nullable) — source file upload
- title (string) — document name (original filename)
- content (text) — chunk text (not full document)
- chunk_index (int) — position of this chunk within the source document
- metadata (JSON) — includes original_filename, content_type, size_bytes, chunk_count
- embedding_json (JSON, nullable) — embedding vector stored as JSON array of floats
- model (string, nullable) — embedding model used (e.g. "text-embedding-3-small")
- created_at (timestamp)

**Ingestion flow:** File upload → `markitdown` text extraction → chunking (500 char, 50 overlap) → embedding via OpenAI-compatible API → store in `rag_documents` → cosine similarity search on query.

---

# 5. File Uploads

Files are stored in MinIO (S3-compatible object storage). The `file_uploads` table tracks metadata; the actual objects live in MinIO.

**Table: file_uploads**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- session_id (UUID, FK → sessions.id, nullable)
- message_id (UUID, FK → messages.id, nullable) — linked after the message is persisted
- original_filename (string)
- content_type (string) — MIME type
- size_bytes (int)
- storage_key (string) — full object key within the tenant bucket
- bucket (string) — MinIO bucket name
- is_temporary (boolean, default false) — mirrors the parent session's temporary flag
- created_at (timestamp)

> Uploads are blocked for temporary sessions at the API level. The `is_temporary` flag is stored for auditing purposes only.

---

# 6. Audit & Logging

## 6.1 Usage Logs

Tracks model usage for analytics and quotas.

**Table: usage_logs**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- model_id (UUID, FK → models.id)
- tokens_in (int)
- tokens_out (int)
- created_at (timestamp)

---

## 6.2 A2A Call Logs

Tracks A2A call reliability metrics — status, latency, retries, and error details. Append-only; survives A2A server deletion for post-mortem analysis.

**Table: a2a_call_logs**
- id (UUID, PK)
- tenant_id (UUID) — denormalised
- a2a_server_id (UUID) — denormalised; survives server deletion
- a2a_server_name (string, nullable)
- skill_id (string, nullable)
- session_id (UUID, nullable)
- trace_id (UUID) — correlation ID for the call chain
- status (string) — one of: success, timeout, error, circuit_open
- latency_ms (int, nullable)
- retry_count (int, default 0)
- error_message (text, nullable)
- created_at (timestamp)

---

## 6.3 Audit Logs

Tracks administrative and sensitive actions across the platform. Written on every mutating admin operation. Never updated or deleted — append-only.

**Table: audit_logs**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id, nullable) — null for platform-level actions (e.g. tenant creation)
- actor_id (UUID, FK → users.id) — the user who performed the action
- actor_role (enum: admin, manager, user) — role at time of action; denormalised so log is self-contained if user is later deleted
- action (string) — machine-readable action key, e.g. `user.created`, `model.deleted`, `tenant.updated`, `tool.enabled`
- target_type (string, nullable) — the entity type acted upon, e.g. `user`, `model`, `tool`, `template`
- target_id (UUID, nullable) — the ID of the entity acted upon
- payload (JSON, nullable) — relevant changed fields (new values only; secrets and encrypted fields are never included)
- ip_address (string, nullable) — request IP for forensic purposes
- created_at (timestamp)

**Action keys (examples):**

| Action | Description |
|---|---|
| `user.created` | A user was created |
| `user.deleted` | A user was deleted |
| `user.role_changed` | A user's role was changed |
| `user.deactivated` | A user was deactivated |
| `tenant.created` | A tenant was created |
| `tenant.deleted` | A tenant was deleted |
| `model.created` | A model was configured |
| `model.deleted` | A model was removed |
| `model.api_key_updated` | A model API key was rotated (payload omits the key itself) |
| `tool.created` | A tool was created |
| `tool.deleted` | A tool was deleted |
| `tool.enabled` / `tool.disabled` | A tool was enabled or disabled |
| `template.created` | A template was created |
| `template.deleted` | A template was deleted |
| `skill.created` | A skill was created |
| `skill.deleted` | A skill was deleted |
| `erpnext.created` | An ERPNext instance was configured |
| `erpnext.deleted` | An ERPNext instance was removed |
| `system.encryption_key_rotated` | The encryption key was rotated |

> The audit log is append-only. Rows are never updated or deleted, even by admins. Retention policy is configurable but deletion is via a scheduled purge job, not via API.

---

## 6.4 Scheduled Tasks

Stores time-based autonomous agent executions (Issue #297). Tasks can be one-shot (run at a specific datetime) or recurring (cron-like schedule).

**Table: scheduled_tasks**
- id (UUID, PK)
- tenant_id (UUID, FK → tenants.id)
- user_id (UUID, FK → users.id)
- goal (text) — the agent goal to execute
- schedule_description (string) — human-readable description (e.g. "Every Friday at 8pm")
- cron_expression (string) — standard cron expression (e.g. "0 20 * * 5")
- timezone (string, default: "UTC") — IANA timezone name
- state (enum: ACTIVE, PAUSED, DELETED)
- next_run_at (timestamp, nullable, indexed)
- last_run_at (timestamp, nullable)
- last_run_status (string, nullable: SUCCESS / FAILED)
- last_run_session_id (UUID, nullable, FK → sessions.id)
- last_run_error (text, nullable)
- template_session_id (UUID, nullable, FK → sessions.id)
- run_count (integer, default: 0)
- created_at (timestamp)
- updated_at (timestamp)

## 6.5 Notifications

Persistent in-app notification records. Created when background tasks complete/fail and when scheduled tasks execute.

**Table: notifications**
- id (UUID, PK)
- user_id (UUID, FK → users.id, indexed)
- tenant_id (UUID, FK → tenants.id, indexed)
- type (string: TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED, TASK_SCHEDULED_COMPLETED, TASK_SCHEDULED_FAILED)
- title (string) — short human-readable title
- body (text, nullable) — optional description or result summary
- reference_id (UUID, nullable) — ID of the related entity (e.g. AutopilotRun.id)
- is_read (boolean, default: false)
- created_at (timestamp)

## 6.6 Autopilot Runs

Tracks the full lifecycle of autopilot and background task executions. Each run is backed by a Session for conversation history.

**Table: autopilot_runs**
- id (UUID, PK)
- session_id (UUID, FK → sessions.id, indexed)
- goal (text) — the original user goal
- state (enum: EXECUTING, COMPLETED, FAILED, CANCELLED, PAUSED, indexed)
- current_turn (integer, default: 0) — 1-based, 0 = not started
- max_turns (integer, default: 20)
- cumulative_tokens (integer, default: 0) — total input + output tokens
- findings (longtext, nullable) — JSON array of per-turn findings
- created_at (timestamp)
- updated_at (timestamp)

---

# 7. Relationships Summary

```
Tenant
 ├── Users (admin | manager | user)
 ├── Models
 ├── Tools
 │     └── ERPNext Instances
 ├── Templates
 │     └── Template Allowed Tools
 ├── Prompts
 ├── Skills (tenant-shared or user-owned)
 │     └── Skill Allowed Tools
 ├── Sessions (permanent or temporary)
 │     ├── Messages (branching tree)
 │     │     └── Message Feedback
 │     ├── Session Active Tools
 │     └── File Uploads
 ├── Memory (per user, optionally per session)
 ├── RAG Documents
 ├── Usage Logs
 └── Audit Logs
```

---

---

# 8. Encryption of Sensitive Fields

Fields marked as encrypted in this schema use **application-level Fernet symmetric encryption** (AES-128-CBC with HMAC-SHA256) provided by the Python [`cryptography`](https://cryptography.io) library.

- The encryption key is derived from the `ENCRYPTION_KEY` environment variable using `base64.urlsafe_b64encode`
- Encryption and decryption are performed exclusively in `/backend/src/core/encryption.py`
- No other module in the codebase calls the `cryptography` library directly
- Encrypted values are stored as base64-encoded ciphertext strings in the database
- The encryption key must be 32 bytes, base64url-encoded; generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- If the `ENCRYPTION_KEY` is rotated, all encrypted fields must be re-encrypted; a migration utility must be provided

**Encrypted fields:**
- `models.api_key`
- `tools.config` (when type is `erpnext` or `mcp` — contains credentials)

---

# 9. Goals of the Data Model

- Support multi‑tenant isolation
- Support three-tier role model (admin, manager, user)
- Support flexible model and tool configuration
- Support curated templates, personal prompts, and reusable skills (tenant-shared and user-owned)
- Enable session-level tool activation by end users within tenant-approved boundaries
- Enable user-managed memory (view, delete, manually add, paginate)
- Support cross-session memory retrieval — semantic search across all user memories
- Support temporary sessions via Redis with TTL alongside permanent MariaDB sessions
- Allow temporary session finalization (conversion to permanent)
- Support MCP server configurations with encrypted secrets (env vars, headers)
- Support session pinning and title editing
- Support message branching for edits and regeneration without data loss
- Support multi-modal message content via structured JSON parts
- Support message feedback (thumbs up/down) for analytics
- Enable full-text search across sessions and messages
- Enable DeepSeek‑compatible agent workflows
- Provide clean storage for sessions, messages, and memory
- Allow future expansion (billing, quotas, analytics)