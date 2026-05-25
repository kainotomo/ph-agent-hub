# Frontend Architecture — PH Agent Hub

The frontend of PH Agent Hub is a single React web application that exposes two protected product areas:

- **Chat Area** for end users
- **Admin Area** for administrators

This document defines the shared frontend architecture that sits above both areas.

---

## 1. Frontend Role in the System

The frontend is a thin client over the backend and the Microsoft Agent Framework runtime.

The frontend is responsible for:

- authentication and token refresh UX
- route protection and role-aware navigation
- rendering chat conversations and streamed agent output
- rendering administrative resources and CRUD screens
- file upload UX
- prompt, template, skill, and configuration selection UIs

The frontend is **not** responsible for:

- running agents or workflows
- executing tools
- enforcing authorization rules
- storing authoritative conversation or configuration state

Those responsibilities remain in the backend.

---

## 2. Guiding Principles

### **2.1 One App, Two Areas**
- One React codebase
- One deployable frontend service
- Separate route domains, layouts, and feature modules for chat and admin

### **2.2 Backend-Authoritative Behavior**
- The backend owns sessions, prompts, templates, skills, tools, permissions, and agent execution
- Frontend role checks are for UX only; backend authorization is authoritative

### **2.3 Shared Foundation**
- Shared authentication provider
- Shared API client
- Shared design system and UI primitives
- Shared query, mutation, and error handling patterns

### **2.4 Controlled Specialization**
- Chat area uses fully custom React components
- Admin area uses Ant Design 5 components with responsive Table/Card layouts
- Analytics, logs, and agent-specific operational views remain custom when needed

---

## 3. Technology Stack

Recommended stack:

- **React**
- **TypeScript**
- **React Router**
- **TanStack Query** for data fetching and mutations
- **SSE** ([`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source)) for streaming responses and agent events — see [streaming-protocol.md](streaming-protocol.md)
- **Ant Design 5** — the design system for both chat and admin areas

The frontend should remain framework-light and avoid duplicating backend behavior.

---

## 4. High-Level Architecture

```
┌──────────────────────────────────────────────────────┐
│             Frontend React Web App                  │
│                                                      │
│  ┌────────────────────┐  ┌────────────────────────┐  │
│  │   Chat Area        │  │      Admin Area         │  │
│  │ - chat sessions    │  │ - users/tenants        │  │
│  │ - streaming UI     │  │ - models/tools         │  │
│  │ - files/templates  │  │ - templates/skills     │  │
│  │ - prompts/skills   │  │ - analytics/settings   │  │
│  │ - memory manager   │  │ role-aware: admin sees │  │
│  │ - tool activation  │  │ all; manager sees only │  │
│  │ - personal skills  │  │ their tenant scope     │  │
│  └────────────────────┘  └────────────────────────┘  │
│                                                      │
│  Shared: auth, API client, tenant context, routing   │
└───────────────────────────────┬──────────────────────┘
                                │ REST + SSE (text/event-stream)
                                ▼
┌──────────────────────────────────────────────────────┐
│          Backend + Microsoft Agent Framework        │
└──────────────────────────────────────────────────────┘
```

---

## 5. Route Structure

Suggested route domains:

```
/
/login
/chat
/chat/:sessionId
/widget (embed iframe page — no auth required, uses guest token)
/admin
/admin/users
/admin/tenants
/admin/models
/admin/tools
/admin/mcp-servers
/admin/templates
/admin/skills
/admin/memories
/admin/analytics
/admin/settings
```

Admin routes under `/admin/tenants` and `/admin/settings` are visible to `admin` only. All other `/admin` routes are accessible to both `admin` and `manager`, with the backend enforcing tenant scope for managers.

---

## 6. Shared Frontend Modules

Suggested structure:

```
/frontend
  /src
    /app
      App.tsx
      router.tsx
    /providers
      AuthProvider.tsx
      QueryProvider.tsx
      TenantProvider.tsx
    /services
      api.ts
      auth.ts
    /shared
      /components
      /layouts
      /theme
      /utils
    /features
      /chat
      /admin
```

The chat and admin areas should share foundations, but not implementation details that make the code harder to reason about.

---

## 7. Authentication and Access Control

- JWT is stored in memory, not localStorage
- Initial app bootstrap loads authenticated user, role, and tenant context
- Three roles carried in JWT: `admin`, `manager`, `user`
- Route guards:
  - `user` role → chat area only
  - `manager` role → chat area + admin area (tenant-scoped)
  - `admin` role → full access to chat and admin areas
- Backend enforces all authorization rules on every protected endpoint

---

## 8. Embed Widget

The embeddable chat widget uses a separate architecture from the main SPA:

- **`WidgetPage`** (`src/features/chat/routes/WidgetPage.tsx`) — a lightweight React
  page loaded inside an iframe on the host website. Reads `?token=` from URL, fetches
  the embed config, stores the guest JWT via `api.ts`'s `setToken()`, and renders
  `ChatWindow` with `embedded={true}`.
- **`ChatWindow` embedded mode** — the `embedded` prop hides the sidebar, model/template/
  skill selectors, settings drawers, and other chrome. Only the message list, input
  area, and minimal header are shown.
- **`embed.js`** (`public/embed.js`) — a ~3 KB vanilla JS loader script injected by
  the host website's `<script>` tag. Creates a floating bubble + drawer (or inline
  iframe) and communicates with the iframe via `window.postMessage`.
- **No auth provider required** — the widget uses guest JWT tokens instead of the
  `AuthProvider`'s user login flow. The `WidgetPage` is a public route (no `RouteGuard`).

See [`embed-widget.md`](embed-widget.md) for the full widget documentation.

This keeps the frontend simple while preserving a strong security model.

---

## 8. Data Access and Streaming

### **8.1 Standard Resource Access**
- REST API for users, tenants, models, tools, MCP servers, templates, prompts, skills, memories, logs, and settings
- Shared data layer for query caching, loading states, and mutation flows

### **8.2 Agent Interaction**
- Streaming responses are delivered from the backend to the chat area
- The frontend renders tokens, tool activity, progress states, and final results
- Agent sessions remain backend-owned and are rehydrated through backend APIs

---

## 9. Refine Usage

Refine should be used only inside the admin area for screens that are primarily:

- resource lists
- CRUD forms
- filters and tables
- audit and management views

Refine should not define the architecture of the chat area.

---

## 10. Mobile Support and Progressive Web App (PWA)

The frontend is designed to be mobile-friendly and installable as a PWA on Android and iOS devices.

### **10.1 Responsive Design**
- All chat area layouts must be responsive and usable on small screens
- Ant Design Grid (`Row`/`Col` + `useBreakpoint`) is used for adaptive layouts instead of TailwindCSS responsive prefixes
- The admin area should be functional on tablet and above; full mobile optimization is secondary for admin screens

### **10.2 PWA Requirements**
The frontend must ship as a valid PWA. The required artifacts are:

- **`manifest.json`** — declares app name, short name, icons (192×192 and 512×512 PNG), theme color, background color, and `display: "standalone"`
- **Service Worker** — handles asset caching and enables offline shell loading; use `vite-plugin-pwa` (Workbox-based) to generate this automatically
- **HTTPS** — required for PWA installation and already mandatory for JWT authentication

### **10.3 Installation Behavior**

| Platform | Browser | Install mechanism |
|---|---|---|
| Android | Chrome | Automatic install prompt when manifest + service worker are detected |
| iOS | Safari | Manual: user taps Share → "Add to Home Screen" |
| Desktop | Chrome / Edge | Install button appears in the address bar |

On Android, Chrome automatically shows an install prompt. On iOS, installation is manual through Safari's Share menu — this is an OS-level constraint and cannot be changed.

### **10.4 PWA Scope**
The PWA install targets the **Chat Area** as the primary experience. The admin area is accessible once installed but is not the primary mobile use case.

### **10.5 Implementation Notes**
- Use `vite-plugin-pwa` to generate the service worker and manifest during the build
- Cache the app shell (HTML, JS, CSS) for fast load on repeat visits
- Do not cache backend API responses in the service worker; data freshness is handled by TanStack Query
- Push notifications (for agent completion events) are supported on Android and on iOS 16.4+

---

## 11. Goals of the Frontend Architecture

- keep the frontend thin and backend-driven
- avoid maintaining two separate frontend applications
- share authentication, routing, and UI foundations
- preserve strong separation between chat and admin experiences
- support future prompt, skill, and agent UX without re-architecting the app
- deliver a mobile-friendly, installable experience through PWA support