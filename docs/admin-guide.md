# Administrator Guide — PH Agent Hub

This guide is for platform administrators (`admin` role) and tenant managers (`manager` role) who operate a PH Agent Hub instance. It covers deployment, configuration, and day-to-day management of tenants, users, AI models, tools, templates, and skills.

Quick links:
- Documentation index: [README.md](README.md)
- Architecture overview: [architecture-overview.md](architecture-overview.md)

![Dual UI and multi-tenant architecture](assets/dual-ui-multi-tenant.svg)

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
- Cross-tenant group member addition and model/tool assignment are explicitly blocked

#### Verifying Tenant Isolation

Run the tenant isolation test suite to verify separation is working correctly:

```bash
pytest backend/tests/ -m tenant_isolation -v
```

These tests verify:
- Cross-tenant data access is blocked for sessions, memories, prompts, and skills
- Cross-tenant group member addition raises `ForbiddenError`
- Cross-tenant model/tool assignment raises `ForbiddenError`
- Guest tokens are scoped to their correct tenant
- Embed configs are isolated per tenant

See [security-testing.md](security-testing.md) for the full test suite documentation.

### 3.4 Demo Tenant

PH Agent Hub supports a **demo tenant** for the "Try It Now" experience and embedded widget demo mode. Anonymous visitors are auto-provisioned a temporary session under this tenant.

#### Setup Steps

1. **Create or choose a tenant** that will serve as the demo tenant
2. **Mark it as the demo tenant**: Edit the tenant → set **Is Demo** to `true`
   - Only one tenant can be the demo tenant at a time
   - Marking a new tenant as demo automatically clears the flag on the previous one
3. **Configure models**: Add at least one model with valid credentials under the demo tenant
4. **Configure skills and templates**: Set up the chat experience visitors will see
   - Optionally set default model, skill, and template — these are auto-selected when a demo session starts
5. **Enable demo mode**: Go to **Admin → Settings** → toggle **Demo Mode** ON
   - When disabled, the "Try It Now" button is hidden and demo API endpoints return 503
6. **Configure demo features**: In **Admin → Settings** → **Demo Features**, toggle:
   - **File Upload** — allow visitors to attach files
   - **Follow-up Questions** — show suggested follow-ups after each response
   - *Note: Follow-up questions also require `follow_up_questions_enabled` to be ON
     on the model itself (edit the model in **Admin → Models**)*
7. **Rate limits**: Demo sessions are rate-limited (10 sessions/hour per IP, 30 messages/minute per session) to prevent abuse

#### What Visitors See

- A simplified chat interface (no sidebar, no settings, no model selector)
- A banner: *"You're trying the demo — sign up to save your conversations"*
- Sessions expire after **1 hour** of inactivity
- No persistence — closing the page loses the session
- Configurable features: file upload and follow-up questions (toggle in **Admin → Settings**)

#### Usage

- **Platform demo**: Visitors click **Try It Now** on the login page → redirected to `/demo`
- **Embedded demo**: Add `<script src="/embed.js" data-ph-demo="true"></script>` on any website

#### Limitations

- Only one demo tenant can exist at a time
- The demo tenant's model usage costs are borne by the platform owner
- Demo sessions are anonymous — there is no way to recover a session after expiry

---

## 4. Managing Tenant Balances

PH Agent Hub supports per-tenant euro balances for tracking and limiting spending on model API calls. This is an opt-in feature — tenants with no balance set (NULL) have unlimited usage.

### 4.1 Enable a Balance Limit

1. Go to **Admin Area → Tenants**
2. Click the **$** button next to the tenant
3. Enter an amount (€) and an optional reason
4. Click **Add Funds**

The first time funds are added to a tenant, the spending limit is **enabled**. The tenant's usage will now be deducted from this balance.

### 4.2 Deduct Funds

1. Open the same **$** modal for the tenant
2. Enter the amount to deduct and an optional reason
3. Click **Deduct Funds**

### 4.3 Remove the Limit

1. Open the **$** modal for the tenant
2. Click **Remove Limit**
3. Confirm — the balance is cleared (set to NULL) and the tenant becomes unlimited again

### 4.4 View Transaction History

Click the **history** button next to any tenant to see a paginated log of all balance changes (top-ups, deductions, limit removals).

### 4.5 Balance Display in Tenant List

The **Balance** column shows:
| Value | Meaning |
|---|---|
| **Unlimited** | No balance limit set (tenant can use unlimited API calls) |
| **€X.XX** | Current remaining balance with spending limit active |
| **€X.XX + ⚠️** | Balance is below the configured warning threshold |
| **€X.XX + Blocked** | Balance is €0 or below — subsequent API calls will be rejected |

When one or more tenants have low balances, a **warning banner** appears at the top of the Tenants page listing affected tenants.

### 4.6 Warning Threshold

You can configure a warning threshold per tenant via the `PUT /admin/tenants/{id}/balance/config` API. When balance drops at or below this value, the admin panel shows a ⚠️ badge next to the balance.

---

## 5. Managing Users

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

## 6. Managing AI Models

Models are configured per tenant. Each model row represents an AI provider + API key combination.

### 6.1 Add a Model

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

### 6.2 Enable / Disable Models

Toggle the **Enabled** flag. Disabled models are hidden from the user-facing model selector. Existing sessions that had the model selected will continue to work until the user switches.

### 6.3 API Key Security

- API keys are stored encrypted with Fernet symmetric encryption
- The encryption key is the `ENCRYPTION_KEY` env variable — **never lose this key**
- API keys are **never returned in API responses** (even to admins)
- To rotate a key, edit the model and enter the new key

---

## 7. Managing Tools

Tools extend agent capabilities — they can call external APIs, query ERPNext instances, or run custom code.

### 7.1 Tool Types

| Type | Category | Description | Configuration |
|---|---|---|---|
| **browser** | Web | Playwright headless Chromium — screenshot pages, extract text, extract tables | `timeout`, `viewport_width`, `viewport_height` |
| **calculator** | Utility | Safe AST expression evaluator | None |
| **calendar** | Productivity | Google Calendar or Microsoft Outlook — list/create events, find free slots. Supports tenant-level service accounts and per-user OAuth. | `provider`, `credentials`, `calendar_id`, `timezone` |
| **code_interpreter** | Utility | Docker-sandboxed Python execution (pandas, numpy, matplotlib, plotly) | `timeout`, `allow_network` |
| **currency_exchange** | Financial | Exchange rates via frankfurter.dev (ECB data) | `base_currency`, `timeout` |
| **custom** | Extensibility | Admin-authored sandboxed Python tools | `code` (Python), `config` (JSON) |
| **datetime** | Utility | Timezone-aware date/time queries | `timezone` |
| **document_generation** | Utility | Markdown→PDF (weasyprint), list→Excel (openpyxl), list→CSV | `company_logo_url` |
| **email** | Communication | Send, read, search, and manage emails. Supports SMTP/SendGrid (tenant-level) and per-user connected accounts (IMAP, Gmail API, Microsoft Graph). Users connect their own accounts via Account Settings. | `provider`, `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`, `api_key`, `from_email`, `from_name`, `allowed_recipients` |
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
| **tasks** | Productivity | Create, update, and list tasks via Google Tasks or Microsoft To Do. Requires per-user OAuth. Users connect via Account Settings. | None |
| **weather** | Utility | Weather via wttr.in | None |
| **web_search** | Web | SearXNG-backed web search | `searxng_url` |
| **wikipedia** | Knowledge | Article lookup and summary | `language` |

### 7.2 Add a Tool

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

> **Note:** API keys and secrets in the config JSON are **not** automatically encrypted. Use the `EncryptedString` format in the database, or encrypt values manually with the Fernet key before storing them in config JSON. Tools that expect encrypted values (`github.token`, `image_generation.api_key`, `slack.bot_token`, `email.smtp_password`, `email.api_key`, `calendar.credentials`, `sql_query.connection_string`) will attempt decryption at runtime and fall back to plaintext if decryption fails. Per-user credentials stored in `user_tool_credentials` are encrypted at the ORM level automatically.

### 7.3 Tool-Specific Notes

**Financial tools** (`stock_data`, `market_overview`, `etf_data`, `sec_filings`, `portfolio`, `currency_exchange`): No API keys required. All data comes from free public sources (yfinance, SEC EDGAR, ECB).

**Code Interpreter**: Executes user-submitted Python code in a subprocess. AST-validated for safety — blocks `os`, `sys`, `subprocess`, `eval`, `exec`. Configurable timeout (default 60s) and network access (default off).

**Browser**: Uses Playwright with headless Chromium. Blocks internal/private IPs for security. Screenshots stored in MinIO/S3.

**RAG Search**: By default, the system attempts to use an OpenAI-compatible
embedding API (`text-embedding-3-small`) to generate vector embeddings for
semantic search. When no embedding API key is configured, it falls back to
local TF-IDF-like hashing (256‑dim).

> **Fallback warning**: File uploads will still succeed (HTTP 201), but the
> response will include an `embedding_warning` field when fallback is active.
> Upgrade search quality by configuring an embedding API key via the
> `OPENAI_API_KEY` environment variable or in the RAG tool config JSON
> (`api_key` field).

**Calendar**: Currently supports Google Calendar (API key, OAuth, service account) and Microsoft Graph Calendar (OAuth). Admins can configure tenant-level service accounts; users can connect personal calendars via Account Settings.

**Email**: When using per-user accounts (IMAP, Gmail, Outlook), users connect their own credentials in Account Settings. The admin only needs to create one Email tool (type=`email`, config=`{}`).

**Tasks**: Requires per-user OAuth (Google Tasks or Microsoft To Do). Users connect their accounts in Account Settings. Admins create one Tasks tool (type=`tasks`, config=`{}`).

---

## 8. Managing MCP Servers

MCP (Model Context Protocol) servers let you connect external tools without writing code. Instead of building each integration manually, you configure a connection to any MCP-compliant server and sync its tools into the platform. Synced MCP tools appear in the **Tools** list with type `mcp` and category `MCP`, and work identically to built-in tools in the chat area.

### 8.1 Supported Transports

| Transport | Use Case |
|---|---|
| **Streamable HTTP** | Remote MCP servers over HTTP/SSE. Best for Docker deployments — the server runs as a separate service (sidecar, SaaS, or serverless). |
| **Stdio** | Local MCP servers spawned as a subprocess (e.g., `npx @modelcontextprotocol/server-github`). Requires the server runtime (Node.js, Python, etc.) to be available in the backend container, or route through an HTTP proxy sidecar (`supergateway`). |
| **WebSocket** | Persistent bidirectional connections for streaming use cases. |

### 8.2 Add an MCP Server

1. Go to **Admin Area → MCP Servers**
2. Click **Add MCP Server**
3. Fill in the fields:
   - **Server Name** — a label to identify this connection
   - **Transport** — select `Streamable HTTP`, `Stdio`, or `WebSocket`
   - **Server URL** (HTTP/WS) — the MCP server endpoint, e.g., `https://learn.microsoft.com/api/mcp`
   - **Command** (stdio) — the program to run, e.g., `npx`
   - **Arguments** (stdio) — one per line, e.g., `-y`, `@modelcontextprotocol/server-github`
   - **Environment Variables** — `KEY=VALUE` per line; encrypted at rest via Fernet
   - **HTTP Headers** — `KEY=VALUE` per line for Authorization or API keys; encrypted at rest
   - **Allowed Tools** — leave empty to allow all tools from this server, or type specific tool names to filter

4. Click **Test Connection** to verify the server is reachable and see what tools it exposes
5. Click **OK** to save the configuration
6. Click **Sync Tools** to discover the server's tools and register them as Tool records

### 8.3 Syncing Tools

When you click **Sync Tools**, the platform:
1. Connects to the MCP server and calls its `tools/list` endpoint
2. For each discovered tool, creates or updates a record in the **Tools** table with:
   - `type`: `mcp`
   - `name`: `{Server Name}: {tool_name}`
   - `config.mcp_server_id`: reference to the MCP server config
   - `config.tool_name`: the tool's original name on the server
3. Tools that were previously synced but no longer appear on the server are soft-deprecated (`enabled = false`)

Synced tools immediately become available for:
- Group assignment (via **Groups**)
- Skill assignment (via **Skills**)
- Session activation (via the chat area's tool selector)

### 8.4 Synced Tools vs Built-in Tools

Synced MCP tools function identically to built-in tools:
- They appear in the **Tools** list as type `mcp`
- They can be enabled/disabled, made public, assigned to groups, and added to skills
- They appear in the chat area's **Session Tools** drawer under the **MCP** category
- The agent calls them the same way as any other tool

The only operational difference is that synced MCP tools trigger an outbound connection to the configured MCP server when invoked, which adds network latency.

### 8.5 Managing MCP Servers

From the **MCP Servers** list you can:
- **Edit** — change the server name, transport, URL, auth headers, or allowed tools filter
- **Toggle enabled** — disable a server without deleting its configuration or synced tools
- **Test Connection** — re-verify connectivity at any time
- **Sync Tools** — refresh the synced tool list (e.g., after an MCP server adds new tools)
- **Delete** — removes the server configuration AND all its synced tools from the Tools table

> **Note:** Deleting an MCP server also deletes all associated Tool records. To preserve the tools but stop the server, disable it instead.

### 8.6 Security Considerations

- **Secrets at rest**: Environment variables and HTTP headers are encrypted using the Fernet key (`ENCRYPTION_KEY`). The admin API returns masked values (`ghp_****`).
- **Network access**: The backend container must be able to reach the MCP server URL. For local servers, use the Docker host address (`host.docker.internal` on Docker Desktop, `172.17.0.1` on Linux).
- **Stdio in Docker**: Running stdio-based MCP servers (`npx`, `uvx`) requires the server runtime to be installed in the backend container. Consider using an HTTP proxy sidecar (`supergateway`) instead.

---

## 9. Managing A2A Servers

A2A (Agent-to-Agent) Protocol servers let you connect to external AI agents, discover their capabilities via Agent Cards, and use their declared skills as tools in ph-agent-hub. This enables your agents to collaborate with remote agents across different platforms.

### 9.1 A2A Client vs A2A Server

ph-agent-hub implements both sides of the protocol:

| Role | Description |
|---|---|
| **A2A Client** | Connect to remote A2A agents, fetch their Agent Card (`/.well-known/agent-card.json`), and sync their skills as Tool records. This works just like MCP server integration. |
| **A2A Server** | Expose ph-agent-hub agents to the A2A ecosystem. Other A2A-compliant clients can discover your platform via `GET /.well-known/agent-card.json` and execute tasks via `POST /message:send`. Requires `A2A_SERVER_ENABLED=true`. |

### 9.2 Add an External A2A Agent

1. Go to **Admin Area → A2A Servers**
2. Click **Add A2A Server**
3. Fill in the fields:
   - **Server Name** — a label to identify this connection
   - **Base URL** — the remote agent's base URL, e.g., `https://research-agent.example.com`
   - **Agent Card Path** — defaults to `/.well-known/agent-card.json` (the IANA-registered well-known URI per A2A spec). Change if the remote agent uses a non-standard path.
   - **Protocol Binding** — the A2A protocol binding: `HTTP+JSON/REST` (default), `JSON-RPC 2.0`, or `gRPC`
   - **Authentication Scheme** — how the remote agent expects authentication: `None`, `Bearer Token`, or `API Key`
   - **Auth Token** — the bearer token or API key (encrypted at rest via Fernet)
   - **Custom HTTP Headers** — additional headers sent with every request; `KEY=VALUE` per line
   - **Allowed Skills** — leave empty to allow all skills from this agent, or type specific skill IDs to restrict

4. Click **Test Connection** to verify the agent is reachable — this fetches and validates the Agent Card
5. Click **OK** to save
6. Click **Sync Skills** to discover the agent's skills and register them as Tool records

### 9.3 Syncing Skills

When you click **Sync Skills**, the platform:
1. Fetches the remote agent's Agent Card via the configured path
2. For each declared skill, creates or updates a Tool record with:
   - `type`: `a2a`
   - `name`: `{Server Name}: {skill_name}`
   - `config.a2a_server_id`: reference to the A2A server config
   - `config.skill_id`: the skill's original ID on the remote agent
3. Skills that were previously synced but no longer appear on the Agent Card are soft-deprecated (`enabled = false`)

Synced A2A skills appear in the **Tools** list with type `a2a` and category `Communication`. They can be assigned to groups, added to skills, and activated in sessions just like any other tool.

### 9.4 Managing A2A Servers

From the **A2A Servers** list you can:
- **Edit** — change the server name, URL, auth settings, or allowed skills filter
- **Toggle enabled** — disable a server without deleting its configuration or synced tools
- **Test Connection** — re-fetch and validate the Agent Card at any time
- **Sync Skills** — refresh the synced tool list (e.g., after a remote agent adds new skills)
- **Delete** — removes the server configuration AND all its synced tools

> **Note:** Deleting an A2A server also deletes all associated Tool records. To preserve the tools but stop the server, disable it instead.

### 9.5 Security Considerations

- **Secrets at rest**: Auth tokens and custom headers are encrypted using the Fernet key (`ENCRYPTION_KEY`). The admin API returns masked values.
- **Network access**: The backend container must be able to reach the remote agent's URL.
- **Agent Card validation**: The connection test fetches and validates the Agent Card against the A2A specification schema. Unreachable or malformed endpoints return descriptive errors.

### 9.6 Resilience Configuration (Issue #409)

Each A2A server has configurable resilience parameters under the **Advanced / Resilience** section in the edit form. These control retry behavior, timeouts, and circuit breaker thresholds when your agents call remote A2A skills.

| Field | Default | Description |
|---|---|---|
| **Max Retry Attempts** | 3 | Number of times to retry on transient errors (timeout, 5xx, connection reset) |
| **Retry Backoff Base (s)** | 1.0 | Exponential backoff base: `base × 2^attempt` |
| **Retry Backoff Max (s)** | 60.0 | Maximum seconds between retries |
| **Connect Timeout (s)** | 30.0 | HTTP connection timeout |
| **Read Timeout (s)** | 300.0 | HTTP read timeout for non-streaming calls |
| **Stream Timeout (s)** | 600.0 | HTTP read timeout for streaming responses |
| **Circuit Breaker Threshold** | 5 | Consecutive failures within the window to trip the circuit |
| **Circuit Breaker Window (s)** | 60 | Time window for counting consecutive failures |
| **Circuit Breaker Cooldown (s)** | 300 | Cooldown before the circuit allows a probe request |

### 9.7 Tool Management

Tools have a **Description** field (visible and editable in the admin Tool form) that allows administrators to document what each tool does. This description is stored in the database and displayed as a tooltip on the tool's name in the list view.

When creating or editing an **A2A-type tool**, the form shows structured fields for:

| Field | Description |
|---|---|
| **A2A Server** | The remote A2A server to connect to |
| **Skill ID** | Identifier of the remote skill |
| **Skill Name** | Human-readable name for the skill |
| **Skill Description** | Description of what the remote skill does |
| **Input Modes** | Accepted media types (`text/plain`, `application/json`) |
| **Output Modes** | Response media types (`text/plain`, `application/json`) |
| **Examples** | Usage examples (one per line) |
| **Tags** | Comma-separated tags for categorization |

These fields are stored in the tool's `config` JSON and used by the A2A tool wrapper to build the rich docstring and negotiate media types with the remote agent.

#### Circuit Breaker Behaviour

1. **Normal operation**: All calls proceed normally
2. **Threshold reached**: After N consecutive failures (within the window), the circuit opens — the server is marked **Degraded** in the admin UI
3. **Cooldown**: While degraded, all calls are immediately rejected with a clear error
4. **Probe**: After the cooldown elapses, the next call is allowed as a probe
5. **Auto-recovery**: If the probe succeeds, the circuit resets; if it fails, the cooldown restarts

The circuit breaker state is stored in Redis and auto-clears after 2× cooldown of inactivity.

#### Observability

Every A2A call is logged with:
- **Trace ID** — unique correlation ID for the call chain
- **Server name** + **skill ID** — which remote skill was called
- **Latency** — call duration in milliseconds
- **Status** — `success`, `timeout`, `error`, or `circuit_open`
- **Retry count** — how many retries were attempted

##### A2A Call Logs UI

**Admin Area → A2A Call Logs** provides a read-only table view of all A2A call records. The page displays:

| Column | Description |
|--------|-------------|
| **Timestamp** | When the call occurred (sorted newest-first by default) |
| **Server** | The A2A server name the call was routed to |
| **Skill ID** | The remote skill identifier |
| **Status** | Colored tag: <Tag color="green">Success</Tag>, <Tag color="red">Error</Tag>, <Tag color="orange">Timeout</Tag>, <Tag color="purple">Circuit Open</Tag> |
| **Latency (ms)** | Call duration in milliseconds |
| **Retries** | Number of retry attempts |
| **Error** | Truncated error message (if any) |

**Filters** are available above the table:
- **Server** — dropdown populated from configured A2A servers
- **Status** — dropdown with all four status values
- **Date range** — date picker to narrow results by creation date

Click any row to **expand** it and view the full **Trace ID** and **Error Message** details.

Call logs are immutable append-only records that survive A2A server deletion. They can be filtered by server, status, or date range.

---

## 10. Configuring OAuth for Personal Accounts (Issue #312)

<!-- existing OAuth section follows -->

Some tools (Email, Calendar, Tasks) support **per-user credentials** — each user connects their own account (Gmail, Outlook, etc.) instead of sharing a single tenant-level configuration.

### 9.1 How It Works

1. **Admin creates one tool** per type (e.g., one "Email" tool, one "Calendar" tool)
2. **Users connect their own accounts** in Account Settings (gear icon in the chat sidebar)
3. **Credentials are stored per-user** in the `user_tool_credentials` table, encrypted at rest
4. **At runtime**, the agent uses each user's credentials automatically — no manual tool activation needed

> **Security note (Issue #345):** The OAuth state parameter is a server-side nonce stored in
> Redis with a 10-minute TTL. It is single-use (consumed atomically on callback) and
> tamper-evident — forged or replayed states are rejected. Users must complete the OAuth
> consent flow within 10 minutes. If the state expires, the callback fails with a clear
> error and the user must retry.

Tools with connected accounts are **always available** in the agent's tool set, regardless of auto-selection keyword matching.

### 9.2 Google OAuth Setup

To allow users to connect Gmail, Google Calendar, and Google Tasks:

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services**
2. Create a project (or select an existing one)
3. Enable these APIs:
   - `Gmail API`
   - `Google Calendar API`
   - `Google Tasks API`
4. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add the authorized redirect URI: `{API_BASE_URL}/api/credentials/oauth/google/callback`
7. Copy the **Client ID** and **Client Secret**
8. Add them to the environment configuration:

```env
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
API_BASE_URL=https://api.your-domain.com
FRONTEND_URL=https://app.your-domain.com
```

### 9.3 Microsoft OAuth Setup

To allow users to connect Outlook, Outlook Calendar, and Microsoft To Do:

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**
3. Name: `PH Agent Hub` (or any name)
4. Supported account types: **Accounts in any organizational directory and personal Microsoft accounts**
5. Redirect URI: `{API_BASE_URL}/api/credentials/oauth/microsoft/callback`
6. Click **Register**
7. Copy the **Application (client) ID**
8. Go to **Certificates & secrets** → **New client secret** → copy the value
9. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**
10. Add these scopes:
    - `Mail.Read`
    - `Mail.Send`
    - `Calendars.ReadWrite`
    - `Tasks.ReadWrite`
    - `offline_access`
11. Click **Grant admin consent** (optional for personal accounts, required for organizational accounts)
12. Add to environment:

```env
MS_CLIENT_ID=your-application-id
MS_CLIENT_SECRET=your-client-secret
```

### 9.4 User Setup (No Action Required from Admin)

Once OAuth is configured, users:
1. Click the gear icon ⚙️ in the chat sidebar → **Account Settings**
2. Click **Connect Account** for Email, Calendar, or Tasks
3. Choose Google or Microsoft — the OAuth popup handles authentication
4. Their accounts appear with a green status dot

Manual IMAP setup (for providers without OAuth) is available from Account Settings → Connect Account → Other Email (IMAP).

---

## 10. Managing Templates & Skills

### 10.1 Templates

Templates define reusable system prompts and default configurations for agent sessions. They include:
- System prompt text
- Default model selection
- Default skill
- Allowed tools

Users select templates when creating or configuring chat sessions.

### 9.2 Skills

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

### 9.3 MAF Target Keys

The `maf_target_key` is only required for **Workflow Based** skills. It must match a registered workflow module in the backend codebase (`src/agents/workflows/`). If the key doesn't match any registered target, the backend logs a warning on startup but does not crash.

For Prompt Based skills, the key is auto-generated from the title if left empty (e.g., "Sales Assistant" → `sales_assistant`). It is not used at runtime for this execution type.

---

## 10. Embed Widget Configurations

Embed configurations let you offer the AI chat assistant on external websites via
a `<script>` tag. See the dedicated [`embed-widget.md`](embed-widget.md) guide for
full details.

### 8.1 Creating an Embed Config

1. Go to **Admin → Embed Widget**
2. Click **New Embed Config** and fill in the form
3. Copy the generated embed snippet **immediately** — the token is shown only once
4. Paste the `<script>` tag on your website

### 8.2 Configuration Options

- **Name** — descriptive label for admin reference
- **Allowed Origins** — optional domain whitelist (comma-separated)
- **Theme** — primary color, logo URL, greeting text, position (bubble/inline)
- **Default Model / Skill / Template** — optional overrides (falls back to tenant defaults)
- **Feature Toggles** — enable/disable file upload, model selection, feedback,
  follow-up questions, and cross-session memory per embed

### 8.3 Managing Tokens

- **Regenerate** invalidates the old token immediately — update your website's snippet
- **Copy Snippet** copies the current `<script>` tag to your clipboard
- **Delete** permanently removes the embed config (existing sessions continue until
  their 24h TTL expires)

---

## 11. Licensing & Tenant Gating *(v1.10)*

PH Agent Hub supports a free/pro licensing model that controls how many tenants can be created.

### 11.1 Free Tier

- By default, a fresh deployment allows up to **3 tenants** (configurable via `MAX_FREE_TENANTS`)
- When the limit is reached, attempting to create a new tenant returns `402 Payment Required`
- No license key is required — the free tier works out of the box

### 11.2 Pro License

To remove the tenant limit, install a Pro license:

1. Obtain a license key from the PH Agent Hub team (Ed25519-signed token)
2. Set the `LICENSE_PUBLIC_KEY` environment variable (base64-encoded Ed25519 public key)
3. Enter the license key in **Admin Area → Settings → Licensing**
4. The license is verified using Ed25519 signature verification. If valid, the tenant creation limit is removed.

**Security notes:**
- The `LICENSE_PUBLIC_KEY` must match the key used to sign the license
- License verification is performed server-side on every `POST /admin/tenants` request
- If the license key is invalid, expired, or tampered with, the system falls back to the free tier limit
- Leaving `LICENSE_PUBLIC_KEY` empty disables license verification entirely (free tier only)

### 11.3 License Key Input

When entering a license key:
- Internal whitespace is automatically stripped
- Expiration dates are formatted for readability
- The key is stored encrypted at rest

---

## 12. Groups (Access Control)

Groups let you control which users can access specific models and tools. Instead of making every model and tool available to an entire tenant, you can restrict access to subsets of users.

### 12.1 How Groups Work

- **Create a group** — a named container (e.g., "Finance Team", "Developers")
- **Add members** — assign users to the group
- **Assign models** — restrict which AI models the group can use
- **Assign tools** — restrict which tools the group can access

A user can belong to multiple groups. When group-based access is active, users see only:
- **Models** that are assigned to at least one of their groups (or marked `is_public`)
- **Tools** that are assigned to at least one of their groups (or marked `is_public`)

### 12.2 Create a Group

1. Go to **Admin Area → Groups**
2. Click **Create**
3. Enter a group name
4. Save

### 12.3 Manage Group Members

1. Open a group
2. Go to the **Members** tab
3. Add or remove users

### 12.4 Assign Models and Tools

1. Open a group
2. Go to the **Models** or **Tools** tab
3. Add the resources you want this group to access

---

## 13. Admin Memory & Session Management

### 13.1 Memory Management

**Admin Area → Memories** shows all memory entries across the platform:
- **Admins**: See all memory entries, optionally filtered by tenant or user
- **Managers**: See entries in their own tenant only

You can view and delete any memory entry. Deleting a memory entry removes it permanently — the user will no longer see it in their Memory Manager.

### 13.2 Session Management

**Admin Area → Sessions** provides a read-only view of all permanent chat sessions:
- **Admins**: See all sessions across all tenants
- **Managers**: See sessions in their own tenant only

You can view session metadata (title, user, tags, pin status) and delete sessions. Deleting a session permanently removes all its messages, file uploads, and feedback.

---

## 14. Analytics & Monitoring

### 13.1 Usage Analytics

**Admin Area → Analytics** shows token usage:
- **Admins**: See all tenants
- **Managers**: See their own tenant only

Usage logs are written automatically on every completed agent run (both streaming and non-streaming).

### 13.2 Audit Logs

**Admin Area → Audit** shows a read-only log of all administrative mutations:
- Who performed the action
- What was changed (tenant, user, model, tool, template, skill)
- When it happened

Audit logs are **immutable** — they cannot be deleted or modified. Only admins can view them.

### 13.3 System Logs

**Admin Area → Logs** provides a view of agent activity and error logs. (Currently a stub — detailed log strategy is planned for a future release.)

---

## 15. Security Best Practices

1. **Change the default admin password** immediately after first deployment
2. **Use strong, unique values** for `JWT_SECRET` and `ENCRYPTION_KEY`
3. **Back up your `ENCRYPTION_KEY`** — losing it means all stored API keys are unrecoverable
4. **Never share your `env` file** — it contains secrets
5. **Rotate JWT secrets periodically** — this invalidates all existing tokens
6. **Use HTTPS in production** — Traefik handles this automatically with Let's Encrypt
7. **Restrict admin role** — only assign `admin` to trusted operators
8. **Review audit logs regularly** for suspicious activity

---

## 16. Troubleshooting

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
