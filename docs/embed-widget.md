# Embeddable Chat Widget

PH Agent Hub's embeddable chat widget lets your tenants offer an AI chat assistant
on their own website via a simple `<script>` tag.  No PH Agent Hub account required
for the website visitors — they chat as anonymous guests using temporary sessions.

---

## How It Works

```
Visitor's Browser                    PH Agent Hub Backend
┌──────────────────┐                ┌──────────────────────┐
│ Website           │               │                      │
│  ┌────────────┐   │               │  ┌────────────────┐  │
│  │ <script>   │   │               │  │ Widget API     │  │
│  │ embed.js   │───┼─(1) load ────│─▶│ /widget/config │  │
│  └─────┬──────┘   │               │  │ /widget/session│  │
│        │          │               │  └────────┬───────┘  │
│  ┌─────▼──────┐   │               │           │          │
│  │ Iframe     │   │               │  ┌────────▼───────┐  │
│  │ WidgetPage │───┼─(2) SSE ─────│─▶│ Agent Runner   │  │
│  │ (React)    │   │               │  │ (MAF)          │  │
│  └────────────┘   │               │  └────────────────┘  │
└──────────────────┘                └──────────────────────┘
```

1. **Admin** creates an embed configuration in the admin panel → gets a raw guest token
2. **Admin** pastes the `<script>` snippet on their website
3. **Visitor** loads the page → `embed.js` injects a chat bubble (or inline iframe)
4. **Visitor** clicks the bubble → iframe loads WidgetPage with `?token=...`
5. **WidgetPage** calls `GET /api/widget/config/{token}` → gets a short-lived guest JWT + session
6. **WidgetPage** uses the guest JWT for all subsequent API calls
7. **Visitor** sends a message → `POST /api/widget/session/message` → SSE streaming response
8. **Session** expires after 24 hours (Redis TTL) — zero database impact

---

## Architecture

### Backend

| Component | File | Purpose |
|-----------|------|---------|
| ORM Model | `backend/src/db/orm/embed_configs.py` | Embed configuration (tenant, token hash, theme, feature flags) |
| Service | `backend/src/services/embed_service.py` | CRUD for embed configs; token generation/hashing |
| JWT | `backend/src/core/jwt.py` | `create_guest_token()` / `decode_guest_token()` — separate secret from user JWTs |
| Auth | `backend/src/core/dependencies.py` | `GuestContext` class + `get_guest_context()` dependency |
| API | `backend/src/api/widget.py` | Public widget endpoints (config, session, message, stream) |
| Admin API | `backend/src/api/admin.py` | Admin CRUD endpoints for embed configs |
| Runner | `backend/src/agents/runner.py` | `run_agent_stream()` / `run_agent()` accept `current_user=None` for guests |

### Frontend (Iframe)

| Component | File | Purpose |
|-----------|------|---------|
| WidgetPage | `frontend/src/features/chat/routes/WidgetPage.tsx` | Iframe page: fetches config, applies theme, renders ChatWindow |
| ChatWindow | `frontend/src/features/chat/components/ChatWindow.tsx` | `embedded` prop hides sidebar, selectors, settings drawers |
| Loader | `frontend/public/embed.js` | Vanilla JS (~3 KB): bubble/inline iframe injection, postMessage |
| Admin UI | `frontend/src/features/admin/resources/embed/` | EmbedConfigList + EmbedConfigForm |

---

## Authentication Flow

Embedded chat uses **three layers of authentication**:

1. **Raw guest token** (`embed_<hex>`): Embedded in the `<script>` tag. Looked up by SHA-256 hash in the database. Used only once to bootstrap a session.
2. **Guest JWT**: Short-lived (5 minute) JWT issued by `/widget/config`. Signed with `EMBED_GUEST_TOKEN_SECRET` (separate from user `JWT_SECRET`). Carries `tenant_id`, `embed_config_id`, `session_id`.
3. **Temporary Redis session**: Created on config bootstrap, lives for 24 hours. No user account required.

```
<script data-ph-token="embed_abc123..." src="/embed.js">
         │
         ▼
  GET /widget/config/embed_abc123...    ───▶  DB lookup by SHA-256
         │                                      Creates temporary session
         │                                      Issues guest JWT (5 min TTL)
         ▼
  POST /widget/session/message          ───▶  Guest JWT validated
  Authorization: Bearer <guest_jwt>            Agent runs for tenant
         │                                      SSE stream returned
         ▼
  Session auto-expires after 24h        ───▶  Redis TTL, no DB cleanup
```

---

## Demo Mode

The embed widget supports a **demo mode** that does not require an existing embed config
or guest token.  Instead, it auto-provisions a temporary session under the platform's
configured demo tenant (see [Admin Guide: Demo Tenant](admin-guide.md#3-demo-tenant)).

### Usage

Add the `data-ph-demo="true"` attribute to the script tag (omit `data-ph-token`):

```html
<script src="https://your-domain.com/embed.js"
        data-ph-demo="true"
        data-ph-position="bubble"
></script>
```

### How Demo Mode Differs from a Standard Embed

| Feature | Standard Embed | Demo Mode |
|---------|---------------|-----------|
| Requires embed config | Yes | No (uses demo tenant) |
| Guest token | `data-ph-token="embed_..."` | Not needed |
| Session TTL | 24 hours | 1 hour |
| Tenant | Config-specific tenant | Demo tenant |
| Use case | Production deployments | Try before you sign up |

### Prerequisites

The platform admin must:
1. Create a tenant and mark it as the **demo tenant** (see [Admin Guide](admin-guide.md#3-demo-tenant))
2. Configure at least one model, skill, and template for that tenant
3. Enable the demo mode in **Admin → Settings** (`demo_enabled = true`)

---

## Admin Panel

Navigate to **Admin → Embed Widget** to manage configurations.

### Creating an Embed Config

1. Click **New Embed Config**
2. Fill in:
   - **Name**: A descriptive label (e.g., "Support Widget for example.com")
   - **Active**: Toggle on/off
3. Configure **Theme**:
   - Primary Color: Hex color for the chat bubble and accents
   - Widget Position: Bubble (floating corner) or Inline (embedded in page)
   - Logo URL: Optional logo shown in the chat header
   - Greeting Text: Default welcome message
4. Select **Default Model/Skill/Template** (optional — uses tenant defaults)
5. Configure **Features** (toggles):
   - File Upload, Model Selection, Message Feedback, Follow-up Questions, Cross-session Memory
6. Click **Create** → the embed snippet is shown **once** inside the form. Copy it, then click **OK** to close.

### Managing Configs

- **Edit**: Change name, theme, features, defaults
- **Regenerate Token** (🔑): Invalidates the old token immediately. A modal shows the new full `<script>` snippet — copy it and update your website.
- **Delete**: Permanently removes the config. Existing sessions continue until their 24h TTL expires.

---

## Embed Options

### Bubble Mode (default)

```html
<script src="https://your-domain.com/embed.js" data-ph-token="embed_xxx"></script>
```

Creates a fixed-position chat bubble in the bottom-right corner. Click to open/close the chat drawer.

### Inline Mode

```html
<script src="https://your-domain.com/embed.js" data-ph-token="embed_xxx" data-ph-position="inline"></script>
```

Renders the chat widget inline where the script tag is placed. Useful for dedicated support pages.

### API URL (custom domain)

```html
<script src="https://your-domain.com/embed.js" data-ph-token="embed_xxx" data-ph-api-url="https://your-domain.com/api"></script>
```

---

## Security

- **Token hashing**: Raw guest tokens are never stored in the database — only SHA-256 hashes
- **Separate JWT secret**: `EMBED_GUEST_TOKEN_SECRET` is independent from `JWT_SECRET`
- **Short TTL**: Guest JWTs expire after 5 minutes. Session-level auth is the actual credential.
- **Temporary sessions only**: Guest sessions are Redis-only with 24h TTL. No database persistence.
- **Feature flags enforced server-side**: The embed config's `feature_flags` are loaded into the guest JWT and enforced by the backend — UI-only hiding is not sufficient
- **CSP frame-ancestors**: The nginx config sets a `Content-Security-Policy: frame-ancestors` header to prevent clickjacking. Configure `WIDGET_ALLOWED_ORIGINS` in your env to allow specific domains to embed the widget.

---

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EMBED_GUEST_TOKEN_SECRET` | Yes | — | Separate JWT secret for guest token signing. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |


### Embed Config Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name for admin reference |
| `is_active` | boolean | Whether the config is active |
| `theme.primary_color` | string | Hex color for UI accents (default: `#1677ff`) |
| `theme.logo_url` | string | URL to logo image shown in header |
| `theme.greeting_text` | string | Welcome message (default: "Hi! How can I help?") |
| `theme.position` | string | `"bubble"` or `"inline"` |
| `feature_flags.file_upload` | boolean | Allow file attachments |
| `feature_flags.model_selection` | boolean | Let visitors choose model |
| `feature_flags.feedback` | boolean | Enable thumbs up/down |
| `feature_flags.follow_up_questions` | boolean | Suggest follow-up questions (default: true) |
| `feature_flags.memory` | boolean | Enable cross-session memory |
| `default_model_id` | uuid | Default AI model (tenant default if unset) |
| `default_skill_id` | uuid | Default skill/workflow |
| `default_template_id` | uuid | Default system prompt template |

---

## API Endpoints

### Widget API (public, guest token / guest JWT)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/widget/config/{raw_token}` | Bootstrap: returns theme + features + guest JWT + session_id |
| `GET` | `/api/widget/session` | Get current session info (requires guest JWT) |
| `GET` | `/api/widget/session/messages` | List session messages (requires guest JWT) |
| `POST` | `/api/widget/session/message` | Send message (requires guest JWT). Returns SSE stream or JSON |
| `DELETE` | `/api/widget/session/stream` | Stop active stream (requires guest JWT) |

### Admin API (requires admin/manager auth)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/embed-configs` | List configs (paginated, searchable) |
| `POST` | `/api/admin/embed-configs` | Create config (returns raw token once) |
| `GET` | `/api/admin/embed-configs/{id}` | Get config details |
| `PUT` | `/api/admin/embed-configs/{id}` | Update config |
| `DELETE` | `/api/admin/embed-configs/{id}` | Delete config |
| `POST` | `/api/admin/embed-configs/{id}/regenerate-token` | Regenerate guest token |

---

## Troubleshooting

### Widget not appearing on my website

1. Check the browser console for errors (CORS, network, JavaScript)
2. Verify the guest token is correct in the `<script>` tag
3. Ensure `embed.js` is accessible at the configured URL
4. Check that the embed config is set to **Active** in the admin panel

### "Invalid guest token" error

1. The token may have been regenerated — update your snippet
2. The embed config may have been deleted — create a new one
3. Check for leading/trailing whitespace in the `data-ph-token` attribute

### Cross-origin issues

1. If using production, verify the SSL certificate is valid
2. The iframe communicates via `postMessage` — no additional CORS needed for the iframe

### SSE streaming not working

1. Verify the backend nginx has `proxy_buffering off` for `/api/` routes
2. Check that `proxy_read_timeout` is set to at least 600s
3. Ensure the firewall isn't blocking long-lived connections

---

## Limitations (v1)

- **Temporary sessions only**: Guest sessions expire after 24 hours (Redis TTL). No persistent chat history.
- **No guest→registered migration**: Anonymous visitors cannot convert their session to a registered account.
- **No file upload for guests**: Configurable via feature flags but disabled by default.
- **No usage analytics per embed**: Usage is attributed to the tenant but not tracked per-embed-config (planned for v2).
- **No proactive messaging**: The widget only responds to user-initiated messages (planned for v2).

---

## Future (v2) Ideas

- Standalone lightweight widget bundle (without Ant Design, ~300 KB instead of ~2 MB)
- Proactive chat triggers (time on page, scroll depth, exit intent)
- Pre-chat forms (collect name, email before chatting)
- Guest→registered user session migration
- Per-embed analytics dashboard
- Multiple widget instances on the same page
- Web Component-based embed for framework-agnostic loading
