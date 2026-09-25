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

PH Agent Hub exposes workflows as skills with `execution_type = workflow_based`. When the agent loop resolves a skill with `execution_type = workflow_based`, it delegates to a MAF Workflow runner instead of a simple `Agent.run()` call.

MAF Workflows are built using the `WorkflowBuilder` graph API. PH Agent Hub connects steps sequentially via `add_chain` — topology is data defined in the workflow module, not code.

#### Workflow Definition

Workflows are defined in `/backend/src/agents/workflows/` as Python modules. Each module must expose `MAF_KEY` (a constant string matching the skill's `maf_target_key`) plus one of:

- **`WORKFLOW_DEFINITION`** — a `dict` or a `WorkflowDefinition` instance (from `definition.py`)
- **`STEPS`** — a list of step dicts, auto-wrapped by `load_workflow_definition()` into a `WorkflowDefinition` keyed by `MAF_KEY`

A workflow consists of **steps** — an ordered list of step dicts. Each step is a plain dict with the following fields:

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Executor identity. Must be stable across rebuilds. |
| `name` | `str` | Display label only. |
| `type` | `Literal["inline", "agent"]` | `"inline"` for LLM-driven steps (requires `instructions`); `"agent"` for pre-built agent steps (requires `agent_ref`). |
| `agent_ref` | `str \| None` | Required when `type == "agent"`. An `@`-prefixed role reference (e.g. `@reasoning`) or a registered agent key. |
| `instructions` | `str \| None` | Required when `type == "inline"`. System prompt for the LLM. |
| `model_ref` | `str \| None` | Model reference. An `@`-prefixed role (e.g. `@reasoning`) or an unprefixed concrete tenant model id. An unbound `@`-prefixed role raises `ValidationError` and never falls back. For an omitted `model_ref` on an `inline` step, `resolve_model` is never called (the step cannot execute without a model). A concrete reference that is not found in the tenant falls back to the skill's `default_model_id`. For `type == "agent"`, an omitted `model_ref` inherits the agent module's `MODEL_ROLE`. |
| `reasoning_effort` | `str \| None` | Optional chain-of-thought effort level override. |
| `temperature` | `float` | Model temperature, clamped to [0.0, 2.0]. Default `0.7`. |
| `input` | `str` | Description of the step's input source (see *input forms* below). Default is empty — inherit from upstream output. |
| `context_mode` | `Literal["full", "last_agent"]` | How prior context is passed. Default `"last_agent"` — only the immediately preceding agent's response messages. `"full"` also includes the original user input. `"custom"` is not supported. |
| `on_error` | `Literal["stop", "continue"]` | Step failure handling. Default `"stop"` (halt the workflow); `"continue"` skips to the next step. |

**Reference convention**: An `@`-prefixed value is a logical role reference drawn from a closed, centrally declared vocabulary in `roles.py` (`MODEL_ROLES`, `TOOL_ROLES`, `AGENT_ROLES`). Each tenant binds these roles to its own concrete resources: model roles resolve through the tenant's model-role bindings (raising `ValidationError` for an unbound role), and tool roles resolve against the run's tenant-scoped tool pool (raising `ValidationError` for an unresolved ref). An unprefixed value (e.g. `"gpt-4o"`) is a concrete tenant resource id, validated on a different path.

**Input forms** for the `input` field:

- Empty string (default): inherit the upstream step's output.
- `"user_message"`: use the original user message for this step.
- `"output_of:<step_id>"`: use the output of the specified step (the referenced `step_id` must exist in the definition and cannot be the step's own id).
- Any other non-empty string: treated as literal text.

#### Definition identity and edit policy

A workflow definition is identified by two immutable tokens:

- **`key`** — the checkpoint namespace and the registry key (see `definition.py`).
- **step `id`** — the MAF executor identity (see `engine.py` and `identity.py`).

The definition `key` is immutable: a rename is a create, not an edit. This is enforced by `load_workflow_definition` which requires a definition's `key` to equal the module's `MAF_KEY` (see `engine.py` § "Workflow definition loader"). A step `id` must be a non-empty, whitespace-free token — it serves as MAF executor identity.

Config-only versus topology changes are derived from MAF's `Workflow.graph_signature` (never from a hand-maintained field list) by rebuilding a probe workflow and comparing signatures (see `graph_signature`, `graph_signature_hash`, and `classify_edit` in `identity.py`). The full edit policy is:

> Workflow definition edit policy
>
> - A definition is identified by its key (the checkpoint namespace and the registry
>   key) and by its step ids (MAF executor identity). Neither may be renamed in place:
>   renaming is a create, not an edit, and is rejected by validation.
> - A config-only edit changes nothing in MAF's graph signature (instructions,
>   model_ref, tool_refs, temperature, reasoning_effort, context_mode, type, agent_ref,
>   input, on_error, display name, description). It is allowed and applies to
>   subsequent runs; paused runs of the previous definition remain resumable.
> - A topology edit changes the graph: a step is added, removed, renumbered or renamed,
>   or an edge changes. It is allowed, but paused runs of the previous topology can no
>   longer be resumed. The edit must report the added and removed step ids, and, once
>   checkpoint storage exists, the number of paused runs affected. Paused runs are
>   never silently stranded and the edit is never silently blocked.

#### Workflow Execution & Streaming

Each workflow step is built into a MAF `Agent` (see `build_agent_for_step`), wrapped with a `StepAgent` that applies step-level input semantics, and then connected sequentially via `WorkflowBuilder.add_chain`. The workflow is executed as a single MAF `Workflow.run()` call.

Steps are executed in order. Per-step failures are handled according to the `on_error` field: `"stop"` halts the workflow, `"continue"` skips to the next step. There is no automatic fallback to single-agent execution.

#### Token Extraction

Workflow token usage (input tokens, cache hits, output tokens) is extracted from the `WorkflowRunResult` after execution by iterating output and intermediate events, accumulating `usage_details` from `AgentResponse` payloads.

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
[3] Route by execution_type
    ├── execution_type = agent_based → assemble MAF Agent → agent.run()
    └── execution_type = workflow_based → assemble MAF Workflow → workflow.run()
        │
        ▼
[4] Apply middleware pipeline
    - DeepSeek stabilizer (if DeepSeek provider)
    - Loop protection
    - OpenTelemetry tracing
        │
        ▼
[5] Execute (Agent.run() or Workflow.run(stream=True))
        │
        ▼
[6] Persist message + branch to MariaDB
        │
        ▼
[7] Stream tokens + agent events → SSE → frontend
    - token/tool/tool_result/step_complete events
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

MAF stream events are mapped to typed SSE events in `runner.py` before being sent to the client. The full event schema, error codes, nginx configuration, and client-side handling pattern are defined in [backend-architecture.md](backend-architecture.md) §11.

MAF streaming integration points:
- Token chunks are forwarded to the SSE response stream as they arrive from the model
- Agent events (tool start, tool result, step complete) are emitted as typed SSE events
- The DeepSeek stabilizer filters `<think>` tokens from the stream before they reach the SSE layer
- The **Stream Bridge** (`agents/stream_bridge.py`) manages the SSE streaming connection between the agent loop and the frontend, handling reconnection, partial response preservation on navigation, and stream lifecycle

---

## 7. Workflow Engine (`agents/workflows/engine.py`)

The workflow engine provides the bridge between PH Agent Hub's skill system and MAF's Workflow API:

| Function | Purpose |
|---|---|
| `resolve_model(db, model_ref)` | Look up a `Model` record from the database by its `id` or `model_id` attribute |
| `load_workflow_definition(mod)` | Extract a validated `WorkflowDefinition` from a registered MAF module (accepts `WORKFLOW_DEFINITION` or `STEPS`) |
| `_build_agent_for_step(step, model, tools, temperature, reasoning_effort, instructions)` | Creates a MAF `Agent` configured with the step's instructions, model, tools, and options (private) |
| `build_workflow(defn, db, extra_tools, base_temperature, base_reasoning_effort, default_model_id)` | Assembles a MAF `Workflow` from a `WorkflowDefinition` by building agents for each step and connecting them sequentially via `WorkflowBuilder.add_chain` |
| `run_workflow(workflow, message)` | Executes a workflow synchronously; returns `(output_text, WorkflowRunResult)` |
| `iter_workflow_sse(workflow, message, session_id, message_id, ...)` | Async generator that iterates the workflow and emits SSE-compatible event dicts for each agent event |
| `_extract_token_counts_from_workflow(result)` | Extracts input/output/cached token counts from a completed `WorkflowRunResult` by iterating output and intermediate events |
| `classify_edit(current, proposed)` | Classifies an edit as `unchanged`, `config_only`, or `topology` from MAF's `graph_signature_hash` (see `identity.py`) |
| `render_edit_report(classification, *, paused_run_count=None)` | Renders the edit-impact report, stating whether paused runs remain resumable (see `identity.py`) |

Definitions are also validated so a key that diverges from `MAF_KEY` is rejected.

Execution flow:
1. `load_workflow_definition` reads the workflow module and validates its structure
2. `build_workflow` resolves model records, builds a MAF `Agent` per step, applies `StepAgent` input semantics, and connects them sequentially
3. `run_workflow` executes the workflow; each step receives the previous step's output (governed by `input` and `context_mode`)
4. Token usage from each step is accumulated via `_extract_token_counts_from_workflow`
5. The final output combines results from all steps

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

## 11. References

- [MAF GitHub](https://github.com/microsoft/agent-framework)
- [MAF Docs — Agents](https://learn.microsoft.com/en-us/agent-framework/agents/index)
- [MAF Docs — Agent Skills](https://learn.microsoft.com/en-us/agent-framework/agents/skills)
- [MAF Docs — Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/index)
- [MAF Docs — Providers](https://learn.microsoft.com/en-us/agent-framework/agents/providers/index)
- [MAF Docs — Tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/index)
- [DeepSeek Stabilizer](deepseek-stabilizer.md)
- [Streaming Protocol](backend-architecture.md#11-streaming-protocol)

### Example Workflow

- **`web_research_report`** (`agents/workflows/web_research_report.py`) — A two-step workflow:
  1. **Step id `research`** — type `inline` with `@reasoning` as its model reference, instructed to search and summarize findings.
  2. **Step id `report`** — type `inline` with `@reasoning` as its model reference and a lower `temperature` (0.3), instructed to synthesize findings into a structured report.
