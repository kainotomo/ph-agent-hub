# Admin Area Architecture — PH Agent Hub

The admin area is the operational control surface inside the single React frontend of PH Agent Hub. It provides administrators and tenant managers with visibility and control over tenants, users, models, tools, templates, skills, and system configuration.

This document defines the structure, responsibilities, and integration points of the admin area.

---

## 1. Purpose of the Admin Area

The admin area serves two roles:

**Platform administrators (role: admin)** can:
- manage all tenants and platform-level settings
- manage users across any tenant
- configure models, tools, templates, and skills globally
- manage MCP server connections and sync their tools
- manage platform-wide memory entries
- configure system-level settings (including licensing)
- view platform-wide analytics and logs
- monitor agent activity and operational errors

**Tenant managers (role: manager)** can:
- manage users within their own tenant only
- create, edit, and delete tools within their tenant
- manage MCP server connections within their tenant
- enable and disable models for their tenant
- manage templates and skills within their tenant
- view analytics scoped to their tenant

Managers cannot create or delete tenants, access other tenants, or modify platform-level configuration.

The area is designed for clarity, security, and operational efficiency.

---

## 2. Technology Stack

The admin area lives inside the shared React frontend application.

Recommended stack:

- **React**
- **TypeScript**
- **Refine Core** for CRUD-heavy admin resources
- **REST API client** for backend communication
- **Ant Design 5 + @refinedev/antd** for administrative components
- **JWT authentication** shared with the rest of the frontend

Refine should be used where it accelerates resource management. It should not be treated as the architecture for the entire web app.

---

## 3. High-Level Architecture

```
┌──────────────────────────────────────────────┐
│      Admin Area (Frontend Route Space)       │
│  - User management                           │
│  - Model configuration                       │
│  - Tool configuration                        │
│  - Tenant settings                           │
│  - Templates & skills                        │
│  - Analytics                                 │
└───────────────────────────────┬──────────────┘
                                │ REST API
                                ▼
┌──────────────────────────────────────────────┐
│        Backend + Agent Runtime Services      │
│  - Auth                                      │
│  - Models                                    │
│  - Tools                                     │
│  - Tenants                                   │
│  - Templates                                 │
│  - Skills                                    │
│  - Usage logs                                │
└──────────────────────────────────────────────┘
```

---

## 4. Core Features

### **4.1 Authentication**
- Login page shared with the main frontend
- JWT-based session
- Role-based access: `admin` sees the full admin area; `manager` sees a tenant-scoped restricted view
- The backend enforces role boundaries on every endpoint; the frontend adapts navigation accordingly

### **4.2 Role Capabilities Summary**

| Capability | admin | manager |
|---|:---:|:---:|
| Create / delete tenants | ✓ | ✗ |
| Manage users across all tenants | ✓ | ✗ |
| Manage users within own tenant | ✓ | ✓ |
| Create / edit / delete tools (own tenant) | ✓ | ✓ |
| Manage MCP servers (own tenant) | ✓ | ✓ |
| Enable / disable models (own tenant) | ✓ | ✓ |
| Manage templates and skills (own tenant) | ✓ | ✓ |
| Manage platform-wide memory entries | ✓ | ✗ |
| View analytics (own tenant) | ✓ | ✓ |
| View platform-wide analytics | ✓ | ✗ |
| System settings (including licensing) | ✓ | ✗ |

### **4.3 User Management**
- Create and delete users
- Assign roles (admin can assign any role; manager can assign `user` role only within their tenant)
- Reset passwords
- Activate and deactivate accounts
- Assign users to tenants (admin only)

### **4.4 Tenant Management** *(admin only)*
- Create tenants
- Configure tenant defaults
- Assign models and tools to tenants
- Manage tenant-specific settings

### **4.5 Model Management**
- Add and remove models (admin only across all tenants; manager within own tenant)
- Configure API keys and base URLs
- Enable and disable models per tenant
- Set routing priority
- Set default model per tenant

### **4.6 Tool Management**
- Create, edit, and delete tools within the tenant (available to both admin and manager)
- Configure ERPNext instances
- Configure Membrane tools
- Upload custom tool definitions
- Enable and disable tools per tenant
- View MCP-synced tools (created automatically when MCP server tools are synced)

### **4.7 MCP Server Management** *(v1.10)*
- Create, edit, and delete MCP server configurations
- Support for three transports: Streamable HTTP, Stdio, WebSocket
- Test connection to MCP servers and preview discovered tools
- Sync tools from MCP servers (creates/updates Tool records with `type=mcp`)
- Environment variables and HTTP headers stored encrypted at rest (Fernet)
- Allowed tools filtering — restrict which server tools are synced
- **Security**: Secrets are encrypted using the Fernet key and masked in API responses (`ghp_****`). Synced tools are available for group assignment, skill assignment, and session activation just like built-in tools.
- Deleting an MCP server also removes all its synced tools. To preserve tools but stop the server, disable it instead.

### **4.7 Template Management**
- Create, edit, and delete curated templates
- Assign templates to tenants, roles, or specific users
- Mark templates available for chat sessions and skill composition
- Set default templates

### **4.8 Skill Management**
- Create, edit, and delete skills
- Map skills to Microsoft Agent Framework agents or workflows
- Configure default template, prompt, model, and allowed tools for a skill
- Publish skills tenant-wide or restrict them to specific users later

### **4.9 Usage Analytics**
- Tokens per user
- Tokens per tenant
- Model usage breakdown
- Tool usage statistics
- Error logs
- Managers see only their own tenant's data

### **4.10 Memory Management** *(v1.10)*
The admin area provides a read-only (with delete capability) view of all memory entries across the platform:
- **Admins**: See all memory entries, optionally filtered by tenant or user
- **Managers**: See entries in their own tenant only

Entries can be viewed and deleted. Deleting a memory entry removes it permanently.

### **4.11 System Settings** *(admin only)*
- Global configuration
- Logging level
- Vector DB settings (optional)
- **Licensing configuration**:
  - `MAX_FREE_TENANTS` — max tenants allowed on the free tier (default: 3)
  - `LICENSE_PUBLIC_KEY` — Ed25519 public key for license verification; leave empty to disable verification (free tier only)
  - Tenant creation is gated: `POST /admin/tenants` returns `402 Payment Required` when the free tier limit is exceeded and no Pro license is installed
- Maintenance mode

---

## 5. UI Layout Structure

```
/frontend
  /src
    /features
      /admin
        /resources
          /users
          /tenants
          /models
          /tools
          /templates
          /skills
        /pages
          /analytics
          /settings
        /components
        /services
          admin.ts
        /layouts
```

Refine resources should be defined inside the admin feature module, while shared auth, routing, and theming stay at the app level.

---

## 6. Navigation Structure

### **Sidebar**
- Dashboard
- Users
- Tenants *(admin only)*
- Models
- Tools
- MCP Servers *(v1.10)*
- Templates
- Skills
- Memories *(v1.10)*
- Analytics
- Settings *(admin only)*
- Logout

### **Top Bar**
- Tenant selector when relevant *(admin only)*
- User profile
- Role badge (admin / manager)
- Notifications (optional)

---

## 7. API Integration

The admin area communicates with the backend via REST endpoints:

### **Authentication**
```
POST /auth/login
GET  /auth/me
```

### **Users**
```
GET    /admin/users
POST   /admin/users
PUT    /admin/users/:id
DELETE /admin/users/:id
```

### **Tenants**
```
GET    /admin/tenants
POST   /admin/tenants
PUT    /admin/tenants/:id
DELETE /admin/tenants/:id
```

### **Models**
```
GET    /admin/models
POST   /admin/models
PUT    /admin/models/:id
DELETE /admin/models/:id
```

### **Tools**
```
GET    /admin/tools
POST   /admin/tools
PUT    /admin/tools/:id
DELETE /admin/tools/:id
```

### **MCP Servers** *(v1.10)*
```
GET    /admin/mcp-servers
POST   /admin/mcp-servers
PUT    /admin/mcp-servers/:id
DELETE /admin/mcp-servers/:id
POST   /admin/mcp-servers/:id/test-connection
POST   /admin/mcp-servers/:id/sync-tools
```

### **Templates**
```
GET    /admin/templates
POST   /admin/templates
PUT    /admin/templates/:id
DELETE /admin/templates/:id
```

### **Skills**
```
GET    /admin/skills
POST   /admin/skills
PUT    /admin/skills/:id
DELETE /admin/skills/:id
```

### **Memories** *(v1.10)*
```
GET    /admin/memories
DELETE /admin/memories/:id
```

### **Analytics**
```
GET /admin/usage
GET /admin/logs
GET /admin/audit
```

---

## 8. Security Considerations

- Admin routes are visible only to users with `admin` or `manager` roles
- Manager routes show only tenant-scoped data; cross-tenant access is blocked at the backend
- Backend role enforcement remains authoritative; frontend visibility adjustments are UX only
- Managers cannot escalate their own role or assign `admin` or `manager` roles to other users
- JWT is stored in memory, not localStorage
- Sensitive fields such as API keys and secrets are masked by default
- Audit logs are recorded for administrative actions

---

## 9. Goals of the Admin Area

- provide a clean, intuitive interface for managing the platform
- support multi-tenant operations at scale
- let administrators configure models and tools without backend code changes
- provide visibility into system usage and performance
- remain clearly separated from the end-user chat experience
