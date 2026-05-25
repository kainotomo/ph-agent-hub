# Microsoft Agent Framework Integration — PH Agent Hub

This document defines how PH Agent Hub integrates with the **Microsoft Agent Framework (MAF)**, the open-source framework used to build, orchestrate, and operate agent systems.

- **GitHub:** https://github.com/microsoft/agent-framework
- **Docs:** https://learn.microsoft.com/en-us/agent-framework/
- **PyPI:** `pip install agent-framework`
- **Language:** Python (the backend is Python; the .NET MAF SDK is not used)

---

## 1. What MAF Provides

MAF is a production-grade Python framework for building AI agents and multi-agent workflows. The capabilities used by PH Agent Hub are:

| MAF Capability | Used For |
|---|---|
| `Agent` with tool calling | Core conversational agents in the chat area |
| Agent Skills | Registering named, reusable execution profiles (mapped to PH Agent Hub Skills) |
| Workflows (graph-based) | Multi-step, multi-agent orchestration |
| MCP client (`MCPStreamableHTTPTool`, `MCPStdioTool`, `MCPWebsocketTool`) | Dynamically discovering and invoking tools from external MCP servers |
| Middleware | DeepSeek stabilization patches, request/response processing |
| Streaming | Token and event streaming to the frontend |
| OpenTelemetry integration | Observability (tracing and monitoring) |
| Multiple provider support | DeepSeek, OpenAI, Anthropic, Ollama, local models |

---

## 2. Core Concepts Mapped to PH Agent Hub

### 2.1 MAF `Agent`

An MAF `Agent` is instantiated with:
- a provider client (model adapter)
- a name and instructions (system prompt)
- a list of tools

In PH Agent Hub, an `Agent` is created per request using the configuration resolved from the session's selected skill, template, model, and active tool list. Agents are **not** long-lived singleton objects — they are assembled per request from tenant and session state.

```python
from agent_framework import Agent

agent = Agent(
    client=model_client,        # resolved from session model selection
    name=skill.title,
    instructions=system_prompt, # resolved from template + prompt
    tools=active_tools,         # resolved from session_active_tools
)
result = await agent.run(user_message)
```

### 2.2 MAF Agent Skills

MAF supports domain-specific Agent Skills — reusable knowledge and capability bundles that agents can discover and invoke.

In PH Agent Hub, the `skills` table maps to MAF Agent Skills. Each skill record has a `maf_target_key` that identifies the registered MAF agent or workflow. When a user selects a skill in the chat area, the backend resolves `maf_target_key` and routes the request to the corresponding MAF target.

### 2.3 MAF Workflows

MAF Workflows are graph-based orchestrations supporting sequential, concurrent, handoff, and group-collaboration patterns. They support:
- checkpointing and restartability
- human-in-the-loop steps
- time-travel (step replay)

PH Agent Hub exposes workflows as skills with `execution_type = workflow`. When the agent loop resolves a skill with `execution_type = workflow`, it delegates to a MAF Workflow runner instead of a simple `Agent.run()` call.

### 2.4 MAF Middleware

MAF provides a middleware system for request/response processing. PH Agent Hub uses middleware for:
- **DeepSeek stabilization** — strip reasoning tokens, repair JSON, validate tool calls
- **Loop protection** — enforce max steps per agent run
- **Logging and tracing** — inject OpenTelemetry spans

The DeepSeek stabilizer is implemented as a MAF middleware component and monkey-patches are applied at the model adapter layer. See [deepseek-stabilizer.md](deepseek-stabilizer.md) for detail.

---

## 3. Provider Adapters

MAF supports multiple model providers. PH Agent Hub uses MAF provider clients configured per-tenant from the `models` table:

| Provider | MAF Client |
|---|---|
| DeepSeek | `OpenAIChatClient` with custom `base_url` (DeepSeek exposes an OpenAI-compatible API) |
| OpenAI | `OpenAIChatClient` |
| Anthropic | `AnthropicChatClient` |
| Ollama | `OpenAIChatClient` (Ollama exposes an OpenAI-compatible `/v1/chat/completions` endpoint) |
| Local / custom | Custom provider implementing the MAF `ChatClient` interface |

The backend resolves the correct client at request time from the `models` table, using the tenant- and session-selected model.

---

## 4. Agent Execution Flow

```
HTTP Request (POST /chat/session/:id/message)
        │
        ▼
[1] Auth & tenant resolution (JWT claims)
        │
        ▼
[2] Resolve session config
    - selected_model_id → model client
    - selected_template_id → system prompt
    - selected_skill_id → maf_target_key + execution_type
    - session_active_tools → tool list
        │
        ▼
[3] Assemble MAF Agent (or route to Workflow)
        │
        ▼
[4] Apply middleware pipeline
    - DeepSeek stabilizer (if DeepSeek provider)
    - Loop protection
    - OpenTelemetry tracing
        │
        ▼
[5] agent.run(user_message) → streaming response
        │
        ▼
[6] Persist message + branch to MariaDB
        │
        ▼
[7] Stream tokens + agent events → SSE → frontend
```

---

## 5. Tool Registration

MAF tools are Python functions decorated with `@tool` and passed to the `Agent` at construction time. In PH Agent Hub:

- Tools are defined in `/backend/src/tools/` (one module per tool type)
- Tools are registered by the tool service at startup and stored in an in-memory registry
- At request time, the backend resolves the session's active tool list against the registry and passes the resolved tool callables to MAF
- Tool permission checks (tenant scope, role, session activation) are enforced by the backend before passing tools to MAF — MAF itself is not responsible for authorization

```python
from agent_framework import tool

@tool
async def get_sales_order(order_id: str) -> dict:
    """Retrieve a sales order from ERPNext."""
    return await erpnext_client.get_doc("Sales Order", order_id)
```

---

## 6. Streaming

MAF supports token-level streaming. PH Agent Hub uses **Server-Sent Events (SSE)** delivered via [`sse-starlette`](https://github.com/sysid/sse-starlette) on the backend and consumed by [`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source) on the frontend.

MAF stream events are mapped to typed SSE events in `runner.py` before being sent to the client. The full event schema, error codes, nginx configuration, and client-side handling pattern are defined in [streaming-protocol.md](streaming-protocol.md).

MAF streaming integration points:
- Token chunks are forwarded to the SSE response stream as they arrive from the model
- Agent events (tool start, tool result, step complete) are emitted as typed SSE events
- The DeepSeek stabilizer filters `<think>` tokens from the stream before they reach the SSE layer

---

## 7. Skill Registration and Discovery

Skills in PH Agent Hub are stored in the `skills` table. The `maf_target_key` field is a string identifier that maps to a registered MAF agent or workflow. Registration happens at backend startup:

```
/backend/src/agents/registry.py
```

The registry:
- scans `/backend/src/agents/skills/` for skill modules
- scans `/backend/src/agents/workflows/` for workflow modules
- registers each under a string key matching `maf_target_key` values in the DB
- is validated at startup — if a `maf_target_key` in the DB has no registered target, a startup warning is emitted

---

## 8. Observability

MAF has built-in OpenTelemetry integration. PH Agent Hub configures:
- distributed tracing across agent steps and tool calls
- span attributes including `tenant_id`, `user_id`, `session_id`, `skill_key`
- export to a configured OTLP endpoint (local Jaeger in development, configurable in production)

---

## 9. Folder Structure

```
/backend/src
  /agents
    runner.py          — assembles and runs MAF agents per request
    registry.py        — skill and workflow registration at startup
    stabilizer.py      — DeepSeek stabilizer middleware
    deepseek_patch.py  — MAF monkey-patches for DeepSeek compatibility
    /skills            — named skill modules (one per maf_target_key)
    /workflows         — named workflow modules (one per maf_target_key)
```

---

## 10. References

- [MAF GitHub](https://github.com/microsoft/agent-framework)
- [MAF Docs — Agents](https://learn.microsoft.com/en-us/agent-framework/agents/index)
- [MAF Docs — Agent Skills](https://learn.microsoft.com/en-us/agent-framework/agents/skills)
- [MAF Docs — Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/index)
- [MAF Docs — Providers](https://learn.microsoft.com/en-us/agent-framework/agents/providers/index)
- [MAF Docs — Tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/index)
- [DeepSeek Stabilizer](deepseek-stabilizer.md)
- [Streaming Protocol](streaming-protocol.md)
