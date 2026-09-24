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

PH Agent Hub exposes workflows as skills with `skill_type = workflow_based`. When the agent loop resolves a skill with `skill_type = workflow_based`, it delegates to a MAF Workflow runner instead of a simple `Agent.run()` call.

#### Workflow Definition

Workflows are defined in `/backend/src/agents/workflows/` as Python modules. Each module exposes:
- **`WorkflowDefinition`** — a Pydantic v2 model declaring the workflow metadata and step graph
- **`MAF_KEY`** — a constant string identifier matching the skill's `maf_target_key` in the DB

A workflow consists of **steps** — each step declares its own model/provider, tools, system prompt, and input/output handling. Steps can be sequential or parallel (fan-out → fan-in).

```python
from agent_framework import Workflow, WorkflowStep, ToolSpec, ModelSpec

class WebResearchReportWorkflow(Workflow):
    MAF_KEY = "web_research_report"

    steps = [
        WorkflowStep(
            id="research",
            model=ModelSpec(provider="openai", model="gpt-4"),
            tools=[ToolSpec(name="web_search"), ToolSpec(name="web_scrape")],
            instructions="Research the topic and compile findings.",
        ),
        WorkflowStep(
            id="report",
            model=ModelSpec(provider="openai", model="gpt-4"),
            tools=[ToolSpec(name="document_generation")],
            instructions="Generate a report from the research findings.",
        ),
    ]
```

#### Workflow Execution & Streaming

Workflows stream progress as SSE `workflow_step` events. Each event carries:
```json
{
  "type": "workflow_step",
  "data": {
    "workflow_key": "web_research_report",
    "step_id": "research",
    "step_index": 0,
    "total_steps": 2,
    "status": "completed"  // or "started", "failed", "bypassed"
  }
}
```

The frontend consumes these events to render a progress indicator showing per-step status (started/completed/failed). Per-step failures are reported directly — there is no automatic fallback to single-agent execution.

#### Token Extraction

Workflow token usage (input tokens, cache hits, output tokens) is extracted from the MAF event stream during execution and associated with the final workflow result for billing/usage tracking.

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
    - selected_skill_id → maf_target_key + skill_type
    - session_active_tools → tool list
        │
        ▼
[3] Route by skill_type
    ├── skill_type = agent_based → assemble MAF Agent → agent.run()
    └── skill_type = workflow_based → assemble MAF Workflow → workflow.run()
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
    - Agent-based: token/tool/tool_result/step_complete events
    - Workflow-based: workflow_step events (per-step progress)
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
| `resolve_model(step)` | Maps a workflow step's model spec to the appropriate MAF ChatClient provider |
| `load_workflow_definition(key)` | Loads and validates a `WorkflowDefinition` from the workflow module identified by key |
| `build_agent_for_step(step, model_client, tools)` | Constructs a MAF Agent for a single workflow step with its specific config |
| `build_workflow(definition, tools)` | Assembles a MAF Workflow from the definition, building agents for each step |
| `run_workflow(workflow, user_input)` | Executes the workflow, yielding `WorkflowEvent` objects as they arrive |
| `iter_workflow_sse(workflow, user_input)` | Async generator that iterates the workflow and emits SSE-compatible event dicts |
| `_extract_token_counts_from_workflow(event)` | Extracts input/output/cached token counts from workflow events for billing |

Execution flow:
1. `run_workflow` is called with the workflow and user input
2. Each step is executed in order (or parallel where declared)
3. Step events (`workflow_step`) are emitted as SSE events with status: `started` → `completed` (or `failed`)
4. Token usage from each step is accumulated for billing
5. The final workflow result includes output from all steps

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
  1. **Research** — uses `web_search` and `web_scrape` tools to gather information on a topic
  2. **Report** — uses `document_generation` to create a structured report from the findings
