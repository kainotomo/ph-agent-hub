# Administrator Guide — PH Agent Hub

This guide is for platform administrators (`admin` role) and tenant managers (`manager` role) who operate a PH Agent Hub instance. It covers deployment, configuration, and day-to-day management of tenants, users, AI models, tools, templates, and skills.

---

## 1. Roles and Permissions

PH Agent Hub has three roles:

| Role | Scope | Capabilities |
|---|---|---|
| **admin** | Platform-wide | Manage all tenants, users, models, tools, templates, skills, groups. View all analytics, audit logs, and sessions. |
| **manager** | Single tenant | Manage users, models, tools, templates, skills, and groups within their own tenant. View tenant-scoped analytics, sessions, and memory entries. |
| **user** | Single tenant | Chat only. Access the chat area within their tenant. No admin access. |

All authorization is enforced by the backend. Frontend route guards are for UX only.

---

## 2. Deployment

### 2.1 Prerequisites

- Docker and Docker Compose v2
- A domain name (production only, for Traefik + Let's Encrypt)

### 2.2 First-Time Setup

```bash
cd infrastructure
cp env.example env
```

Edit the `env` file and set the following **required** values:

| Variable | Purpose |
|---|---|
| `JWT_SECRET` | Random string (≥32 chars) for signing JWTs |
| `ENCRYPTION_KEY` | Fernet key for encrypting API keys at rest. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ADMIN_EMAIL` | Initial admin user email (default: `admin@phagent.local`) |
| `ADMIN_PASSWORD` | Initial admin password — **change before production** |

### 2.3 Start the Platform

**Development:**
```bash
docker compose up --build
```

**Production:**
```bash
docker compose -f docker-compose.prod.yml up -d
```

The seed script runs automatically on first start, creating the default tenant and admin user. Subsequent runs are idempotent.

### 2.4 Access

| Service | Dev URL | Production |
|---|---|---|
| App (frontend) | http://localhost | Your configured `APP_DOMAIN` |
| phpMyAdmin | http://localhost:8080 | Your configured `PMA_DOMAIN` |
| MinIO Console | http://localhost:9001 | Not exposed (use CLI) |

---

## 3. Managing Tenants

Tenants are isolated environments. Each tenant has its own users, models, tools, templates, skills, and sessions.

### 3.1 Create a Tenant

1. Go to **Admin Area → Tenants**
2. Click **Create**
3. Enter a unique tenant name
4. Save

### 3.2 Delete a Tenant

A tenant can only be deleted if it has no users. Remove or reassign all users first, then delete the tenant.

### 3.3 Tenant Isolation

- Users in Tenant A cannot see or access Tenant B's models, tools, sessions, or data
- Managers are scoped to their own tenant — they cannot create tenants or see cross-tenant data
- Admins have full visibility across all tenants

---

## 4. Managing Users

### 4.1 Create a User

1. Go to **Admin Area → Users**
2. Click **Create**
3. Fill in email, display name, role, and select a tenant
4. The user can log in immediately with the password you set

### 4.2 User Roles

- **admin**: Platform superuser. Assign sparingly.
- **manager**: Tenant operator. Can manage their tenant's resources and users.
- **user**: End user. Chat access only.

### 4.3 Deactivate a User

Toggle the user's **Active** status off. Deactivated users cannot log in. Their data is preserved.

### 4.4 Reset a User's Password

1. Go to the user's edit screen
2. Enter a new password
3. Save — the user can log in with the new password immediately

---

## 5. Managing AI Models

Models are configured per tenant. Each model row represents an AI provider + API key combination.

### 5.1 Add a Model

1. Go to **Admin Area → Models**
2. Click **Create**
3. Configure:

| Field | Description |
|---|---|
| **Name** | Display name, e.g. "DeepSeek R1" |
| **Provider** | `deepseek`, `openai`, or `anthropic` |
| **API Key** | Provider API key — **encrypted at rest** (Fernet). Never appears in API responses. |
| **Base URL** | Optional. Custom endpoint for self-hosted or proxied models. |
| **Enabled** | Toggle on to make available to users |
| **Max Tokens** | Maximum output tokens per response |
| **Temperature** | 0.0–2.0. Lower = more deterministic. |
| **Routing Priority** | Integer. Lower numbers are preferred when multiple models match. |
| **Tenant** | Which tenant this model belongs to |

### 5.2 Enable / Disable Models

Toggle the **Enabled** flag. Disabled models are hidden from the user-facing model selector. Existing sessions that had the model selected will continue to work until the user switches.

### 5.3 API Key Security

- API keys are stored encrypted with Fernet symmetric encryption
- The encryption key is the `ENCRYPTION_KEY` env variable — **never lose this key**
- API keys are **never returned in API responses** (even to admins)
- To rotate a key, edit the model and enter the new key

---

## 6. Managing Tools

Tools extend agent capabilities — they can call external APIs, query ERPNext instances, or run custom code.

### 6.1 Tool Types

| Type | Category | Description | Configuration |
|---|---|---|---|
| **browser** | Web | Playwright headless Chromium — screenshot pages, extract text, extract tables | `timeout`, `viewport_width`, `viewport_height` |
| **calculator** | Utility | Safe AST expression evaluator | None |
| **calendar** | Productivity | Google Calendar — list/create events, find free slots | `provider`, `credentials`, `calendar_id`, `timezone` |
| **code_interpreter** | Utility | Docker-sandboxed Python execution (pandas, numpy, matplotlib, plotly) | `timeout`, `allow_network` |
| **currency_exchange** | Financial | Exchange rates via frankfurter.dev (ECB data) | `base_currency`, `timeout` |
| **custom** | Extensibility | Admin-authored sandboxed Python tools | `code` (Python), `config` (JSON) |
| **datetime** | Utility | Timezone-aware date/time queries | `timezone` |
| **document_generation** | Utility | Markdown→PDF (weasyprint), list→Excel (openpyxl), list→CSV | `company_logo_url` |
| **email** | Communication | Send emails via SMTP or SendGrid API | `provider`, `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`, `api_key`, `from_email`, `from_name`, `allowed_recipients` |
| **erpnext** | Enterprise | ERPNext full CRUD, file upload, doctype metadata | `base_url`, `api_key`, `api_secret` |
| **etf_data** | Financial | ETF holdings and profiles (yfinance) | None |
| **fetch_url** | Web | HTTP GET fetching with HTML→text conversion | `timeout`, `user_agent` |
| **github** | DevOps | GitHub/GitLab — search code, list issues/PRs, read files, create issues | `provider`, `token`, `api_base`, `allowed_repos` |
| **image_generation** | Creative | DALL·E 3 / Stable Diffusion — text prompt → image (stored in MinIO/S3) | `provider`, `api_key`, `model`, `default_size`, `default_quality` |
| **market_overview** | Financial | Global index quotes, market movers (yfinance) | None |
| **membrane** | Enterprise | Membrane framework integration | (provider-specific) |
| **portfolio** | Financial | Portfolio analysis, optimization, efficient frontier (numpy+scipy) | None |
| **rag_search** | Web | Semantic search across uploaded documents (embedding API + fallback TF-IDF) | `embedding_model`, `api_key`, `base_url`, `chunk_size`, `top_k` |
| **rss_feed** | Web | RSS/Atom feed reader | `timeout` |
| **sec_filings** | Financial | SEC EDGAR filing search and retrieval (US gov, free) | None |
| **slack** | Communication | Send messages to Slack channels | `webhook_url`, `bot_token`, `default_channel`, `allowed_channels` |
| **sql_query** | Enterprise | Read-only SQL against tenant-configured DB (PostgreSQL, MySQL, MariaDB) | `connection_string`, `row_limit` |
| **stock_data** | Financial | Stock quotes, historical prices, financials, analyst ratings (yfinance) | None |
| **weather** | Utility | Weather via wttr.in | None |
| **web_search** | Web | SearXNG-backed web search | `searxng_url` |
| **wikipedia** | Knowledge | Article lookup and summary | `language` |

### 6.2 Add a Tool

1. Go to **Admin Area → Tools**
2. Click **Create**
3. Set the tool **Name**, **Type**, and **Tenant**
4. Depending on the tool type, fill in the **Configuration (JSON)** field:

**ERPNext example:**
```json
{"base_url": "https://erp.example.com", "api_key": "...", "api_secret": "..."}
```

**SQL Query example:**
```json
{"connection_string": "mysql://user:pass@host:3306/dbname", "row_limit": 1000}
```

**GitHub example:**
```json
{"provider": "github", "token": "ghp_...", "allowed_repos": ["myorg/*"]}
```

**Image Generation example:**
```json
{"provider": "openai", "api_key": "sk-...", "model": "dall-e-3", "default_size": "1024x1024"}
```

**Slack example:**
```json
{"webhook_url": "https://hooks.slack.com/services/...", "default_channel": "#general"}
```

**Email example:**
```json
{"provider": "smtp", "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_username": "...", "smtp_password": "...", "from_email": "noreply@example.com"}
```

5. Set **Enabled** to ON
6. Configure **Public** access — when ON, all tenant users can use the tool regardless of group membership

> **Note:** API keys and secrets in the config JSON are **not** automatically encrypted. Use the `EncryptedString` format in the database, or encrypt values manually with the Fernet key before storing them in config JSON. Tools that expect encrypted values (`github.token`, `image_generation.api_key`, `slack.bot_token`, `email.smtp_password`, `email.api_key`, `calendar.credentials`, `sql_query.connection_string`) will attempt decryption at runtime and fall back to plaintext if decryption fails.

### 6.3 Tool-Specific Notes

**Financial tools** (`stock_data`, `market_overview`, `etf_data`, `sec_filings`, `portfolio`, `currency_exchange`): No API keys required. All data comes from free public sources (yfinance, SEC EDGAR, ECB).

**Code Interpreter**: Executes user-submitted Python code in a subprocess. AST-validated for safety — blocks `os`, `sys`, `subprocess`, `eval`, `exec`. Configurable timeout (default 60s) and network access (default off).

**Browser**: Uses Playwright with headless Chromium. Blocks internal/private IPs for security. Screenshots stored in MinIO/S3.

**RAG Search**: Falls back to local TF-IDF embeddings when no embedding API key is configured. For production use, configure an OpenAI-compatible embedding endpoint.

**Calendar**: Currently supports Google Calendar only (API key for read-only, OAuth/service account for write). CalDAV support planned.

---

## 7. Managing Templates & Skills

### 7.1 Templates

Templates define reusable system prompts and default configurations for agent sessions. They include:
- System prompt text
- Default model selection
- Default skill
- Allowed tools

Users select templates when creating or configuring chat sessions.

### 7.2 Skills

Skills are named agent execution profiles that bundle model, template, and tool defaults. There are two types:

**Prompt Based** (`execution_type = prompt_based`):
- Runs a single conversational agent using the MAF `Agent` class.
- Requires a **Template** (provides the system prompt that defines the agent's behavior).
- Optionally link a **Default Model** and **Tools**.
- MAF Target Key is hidden — not used at runtime for this type.

**Workflow Based** (`execution_type = workflow_based`):
- Delegates to a registered MAF Workflow module for multi-step orchestration.
- Requires a **MAF Target Key** that matches a registered workflow module in the backend (`src/agents/workflows/`).
- Template is hidden — workflows carry their own orchestration logic.

Both types share:
- **Title** (required) and **Description** (optional)
- **Visibility**: `tenant` (available to all users in the tenant) or `personal` (owned by the creating user)
- **Enabled** toggle

**Tenant skills** (created in Admin Area, `visibility=tenant`) are available to all users in the tenant. **Personal skills** (created by users in the chat area) are owned by the creating user.

### 7.3 MAF Target Keys

The `maf_target_key` is only required for **Workflow Based** skills. It must match a registered workflow module in the backend codebase (`src/agents/workflows/`). If the key doesn't match any registered target, the backend logs a warning on startup but does not crash.

For Prompt Based skills, the key is auto-generated from the title if left empty (e.g., "Sales Assistant" → `sales_assistant`). It is not used at runtime for this execution type.

---

## 8. Groups (Access Control)

Groups let you control which users can access specific models and tools. Instead of making every model and tool available to an entire tenant, you can restrict access to subsets of users.

### 8.1 How Groups Work

- **Create a group** — a named container (e.g., "Finance Team", "Developers")
- **Add members** — assign users to the group
- **Assign models** — restrict which AI models the group can use
- **Assign tools** — restrict which tools the group can access

A user can belong to multiple groups. When group-based access is active, users see only:
- **Models** that are assigned to at least one of their groups (or marked `is_public`)
- **Tools** that are assigned to at least one of their groups (or marked `is_public`)

### 8.2 Create a Group

1. Go to **Admin Area → Groups**
2. Click **Create**
3. Enter a group name
4. Save

### 8.3 Manage Group Members

1. Open a group
2. Go to the **Members** tab
3. Add or remove users

### 8.4 Assign Models and Tools

1. Open a group
2. Go to the **Models** or **Tools** tab
3. Add the resources you want this group to access

---

## 9. Admin Memory & Session Management

### 9.1 Memory Management

**Admin Area → Memories** shows all memory entries across the platform:
- **Admins**: See all memory entries, optionally filtered by tenant or user
- **Managers**: See entries in their own tenant only

You can view and delete any memory entry. Deleting a memory entry removes it permanently — the user will no longer see it in their Memory Manager.

### 9.2 Session Management

**Admin Area → Sessions** provides a read-only view of all permanent chat sessions:
- **Admins**: See all sessions across all tenants
- **Managers**: See sessions in their own tenant only

You can view session metadata (title, user, tags, pin status) and delete sessions. Deleting a session permanently removes all its messages, file uploads, and feedback.

---

## 10. Analytics & Monitoring

### 10.1 Usage Analytics

**Admin Area → Analytics** shows token usage:
- **Admins**: See all tenants
- **Managers**: See their own tenant only

Usage logs are written automatically on every completed agent run (both streaming and non-streaming).

### 10.2 Audit Logs

**Admin Area → Audit** shows a read-only log of all administrative mutations:
- Who performed the action
- What was changed (tenant, user, model, tool, template, skill)
- When it happened

Audit logs are **immutable** — they cannot be deleted or modified. Only admins can view them.

### 10.3 System Logs

**Admin Area → Logs** provides a view of agent activity and error logs. (Currently a stub — detailed log strategy is planned for a future release.)

---

## 11. Security Best Practices

1. **Change the default admin password** immediately after first deployment
2. **Use strong, unique values** for `JWT_SECRET` and `ENCRYPTION_KEY`
3. **Back up your `ENCRYPTION_KEY`** — losing it means all stored API keys are unrecoverable
4. **Never share your `env` file** — it contains secrets
5. **Rotate JWT secrets periodically** — this invalidates all existing tokens
6. **Use HTTPS in production** — Traefik handles this automatically with Let's Encrypt
7. **Restrict admin role** — only assign `admin` to trusted operators
8. **Review audit logs regularly** for suspicious activity

---

## 10. Troubleshooting

### Backend won't start

Check the logs:
```bash
docker compose logs backend
```

Common issues:
- **Missing `ENCRYPTION_KEY`**: Generate one and add it to `env`
- **Database connection refused**: Ensure MariaDB is healthy (`docker compose ps`)
- **Import errors**: Rebuild the image (`docker compose up --build`)

### Migration fails

```bash
docker compose exec backend alembic upgrade head
```

Check for manual migration conflicts in `backend/src/db/migrations/versions/`.

### Can't log in

- Verify the seed script ran: `docker compose logs backend | grep "\[seed\]"`
- If no `[seed]` output, run manually: `docker compose exec backend python scripts/seed.py`
- Check that the user is active in the database (phpMyAdmin → `users` table → `is_active`)

### Models don't appear in the chat

- Verify the model is **enabled** in Admin Area → Models
- Verify the model belongs to the correct tenant
- Verify the user's tenant matches the model's tenant
