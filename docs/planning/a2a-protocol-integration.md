# A2A (Agent-to-Agent) Protocol Integration

## Overview

This document archives the design decisions and implementation plan for adding
[A2A (Agent-to-Agent) Protocol](https://a2a-protocol.org/) support to ph-agent-hub,
tracked in [Issue #404](https://github.com/kainotomo/ph-agent-hub/issues/404).

A2A is an open standard under the Linux Foundation (contributed by Google) that
enables communication and interoperability between independent AI agent systems.
It complements [MCP (Model Context Protocol)](https://modelcontextprotocol.io/),
which connects agents to *tools* — A2A connects agents to *other agents*.

## Scope

ph-agent-hub implements two sides of the A2A protocol:

### A2A Client
Connect to any standards-compliant A2A agent, discover its capabilities via its
Agent Card (`/.well-known/agent-card.json`), and make each declared skill
available as a `FunctionTool` in ph-agent-hub's existing tool registry. This
follows the same pattern as MCP server integration.

### A2A Server
Expose ph-agent-hub's agents to the A2A ecosystem by publishing an Agent Card at
`/.well-known/agent-card.json` and implementing the HTTP+JSON/REST protocol
binding (Section 11 of the spec) for task execution, streaming, and management.

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Protocol version | A2A v1.0 | Current stable release |
| Protocol binding | HTTP+JSON/REST | Simplest, aligns with existing httpx patterns, no gRPC dependency |
| Python SDK | `a2a-sdk[http-server]` v1.1.0 | Official Linux Foundation SDK; provides `A2ACardResolver`, `ClientFactory`, `RestTransport`, all spec types |
| Tool integration | Each skill → `Tool` record (type="a2a") | Identical to MCP pattern; uses existing tool registry, auto-select, session activation |
| Auth (Client) | Fernet-encrypted tokens in DB | Same security model as MCP servers |
| Auth (Server) | Reuse existing JWT tokens | No new credential system needed |
| Server task store | In-memory dict (MVP) | Sufficient for initial release |
| Agent Card path | Configurable per server, default `/.well-known/agent-card.json` | Spec-compliant default per IANA registration (Section 14.3) |

## Deferred to Follow-up (Issue #406)

1. **Task lifecycle adapter**: Async execution, `input-required`/`auth-required`
   states, persistent task store, cancellation
2. **Tool fidelity**: Structured I/O modes, examples in prompts, Part type support
3. **Resilience**: Retry with backoff, circuit breaker, version negotiation,
   observability

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     ph-agent-hub                                 │
│                                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │ Admin UI │───▶│ Admin API    │───▶│ a2a_service.py       │  │
│  │ (React)  │    │ /api/admin/  │    │ - CRUD a2a_servers   │  │
│  │          │    │ a2a-servers  │    │ - test connection     │  │
│  └──────────┘    └──────────────┘    │ - sync tools          │  │
│                                      └───────┬──────────────┘  │
│                                              │                  │
│  ┌──────────┐    ┌──────────────┐    ┌───────▼──────────────┐  │
│  │ Chat UI  │───▶│ Agent Runner │───▶│ tools/a2a.py         │  │
│  │          │    │ runner.py    │    │ build_a2a_tool_      │  │
│  └──────────┘    │              │    │ callables()           │  │
│                  │ _build_tool_ │    │ - a2a.Client per      │  │
│                  │ callables()  │    │   server              │  │
│                  └──────────────┘    │ - MAF FunctionTool    │  │
│                                      │   per skill           │  │
│                                      └───────┬──────────────┘  │
│                                              │                  │
│  ┌──────────────────────────────────────────┐│                  │
│  │ A2A Server (a2a_server.py)               ││                  │
│  │ GET  /.well-known/agent-card.json        ││                  │
│  │ POST /message:send                       ││                  │
│  │ POST /message:stream (SSE)               ││                  │
│  │ GET  /tasks/{id}                         ││                  │
│  │ POST /tasks/{id}:cancel                  ││                  │
│  └──────────────────────────────────────────┘│                  │
│                                              │                  │
└──────────────────────────────────────────────┼──────────────────┘
                                               │
                    A2A Protocol (HTTP+JSON/REST)
                                               │
                    ┌──────────────────────────▼──────────────────┐
                    │  External A2A Agents (any spec-compliant)    │
                    │  - Agent Card at /.well-known/agent-card.json│
                    │  - Skills exposed as discoverable tools      │
                    └─────────────────────────────────────────────┘
```

## Files Created/Modified

### Backend
- `backend/requirements.txt` — added `a2a-sdk[http-server]`
- `backend/src/db/migrations/versions/b1a2c3d4e5f6_*.py` — new migration
- `backend/src/db/orm/a2a_servers.py` — new ORM model
- `backend/src/db/orm/__init__.py` — registered A2aServer
- `backend/src/db/orm/tools.py` — added 'a2a' to tool type enum
- `backend/src/services/a2a_service.py` — CRUD + test + sync
- `backend/src/tools/a2a.py` — tool callable builder
- `backend/src/api/admin.py` — A2A admin CRUD endpoints
- `backend/src/api/a2a_server.py` — A2A server protocol endpoints
- `backend/src/main.py` — conditional A2A server router mount
- `backend/src/core/config.py` — A2A settings
- `backend/src/services/tool_service.py` — added 'a2a' type
- `backend/src/agents/runner.py` — added A2A tool dispatch

### Frontend
- `frontend/src/features/admin/resources/a2a/A2aServerList.tsx` — new
- `frontend/src/features/admin/resources/a2a/A2aServerForm.tsx` — new
- `frontend/src/features/admin/services/admin.ts` — types + API functions
- `frontend/src/features/admin/routes/AdminApp.tsx` — added route
- `frontend/src/features/admin/layouts/AdminLayout.tsx` — sidebar item

### Documentation
- `docs/planning/a2a-protocol-integration.md` — this file
