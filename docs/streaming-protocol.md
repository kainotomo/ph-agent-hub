# Streaming Protocol — PH Agent Hub

This document defines the streaming transport, event schema, and error handling contract between the backend and the chat area frontend.

---

## 1. Transport: Server-Sent Events (SSE)

PH Agent Hub uses **Server-Sent Events (SSE)** over HTTP as the streaming transport.

### Rationale

- Chat streaming is **unidirectional** — the server pushes tokens and events; the client only renders them. SSE is designed for this pattern.
- Works transparently through the nginx reverse proxy without special configuration.
- Plain HTTP/1.1 — easy to debug, log, and test.
- Stop-generation is handled by a separate `DELETE` HTTP request, not a bidirectional channel.
- WebSocket is not used — it adds complexity (proxy configuration, connection lifecycle management) with no benefit for a unidirectional push workload.

---

## 2. Libraries

### Backend
**[`sse-starlette`](https://github.com/sysid/sse-starlette)** — SSE support for FastAPI/Starlette.

```
pip install sse-starlette
```

MAF stream events are consumed and forwarded to the SSE response using `sse_starlette.sse.EventSourceResponse`.

### Frontend
**[`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source)** — SSE over POST requests.

```
npm install @microsoft/fetch-event-source
```

The native browser `EventSource` API only supports GET requests. Because the message is sent as a POST body, `@microsoft/fetch-event-source` is required. It also provides retry logic, abort signal support, and typed event handling.

---

## 3. Streaming Endpoint

```
POST /chat/session/:id/message
Content-Type: application/json
Accept: text/event-stream
```

The message is sent and the stream opens in a single request. The response is a `text/event-stream` content type and the connection stays open until the agent finishes or the client aborts.

### Stop Generation

```
DELETE /chat/session/:id/stream
```

Aborts the active stream for the session. The backend cancels the MAF agent run and closes the SSE connection. The partially generated message is saved as-is.

---

## 4. SSE Event Format

Each SSE event follows the standard format:

```
event: <event_type>
data: <json_payload>

```

All `data` payloads are JSON objects. Every event includes a `session_id` and `message_id` field for client-side correlation.

---

## 5. Event Types and Schemas

### 5.1 `token`
A single streamed token chunk from the model. Emitted continuously during generation.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "delta": "token text"
}
```

- `delta` — the raw token string to append to the current assistant message bubble.
- `<think>` tokens are stripped by the DeepSeek stabilizer before this event is emitted; they never reach the client.

---

### 5.2 `tool_start`
Emitted when the agent begins executing a tool call.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "tool_call_id": "uuid",
  "tool_name": "erpnext.get_sales_order",
  "arguments": { "order_id": "SO-00042" }
}
```

- The frontend uses this to render a "calling tool…" progress indicator.

---

### 5.3 `tool_result`
Emitted when a tool call completes.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "tool_call_id": "uuid",
  "tool_name": "erpnext.get_sales_order",
  "success": true,
  "result_summary": "Retrieved Sales Order SO-00042"
}
```

- `result_summary` is a short human-readable string, not the raw tool output. Full tool output is stored server-side and available in the persisted message's `tool_calls` JSON field.
- `success: false` indicates the tool call failed; the agent will handle the error internally.

---

### 5.4 `step_complete`
Emitted at the end of each MAF agent step (one reasoning + tool-calling cycle).

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "step_index": 2,
  "total_steps_so_far": 3
}
```

- Used by the frontend to show step progress for multi-step agent runs.

---

### 5.5 `message_complete`
Emitted when the agent finishes and the full message has been persisted to the database.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "branch_index": 0,
  "total_tokens": 512,
  "model_id": "uuid"
}
```

- `model_id` — the UUID of the model that generated this response. The frontend uses this to display "Model: XYZ" on the message bubble (see Issue #233).
- The frontend uses this to finalize the message bubble, enable feedback controls, and update branch navigation.
- After this event the SSE connection is closed by the server.

---

### 5.6 `follow_up_questions`
Emitted after `message_complete` when the model has `follow_up_questions_enabled` set to `true`. Contains suggested follow-up questions the user can click to continue the conversation.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "questions": [
    "Can you explain that in more detail?",
    "What are the next steps?",
    "How does this compare to other approaches?"
  ]
}
```

- `questions` — an array of up to 3 strings, each a concise follow-up question.
- The frontend renders these as clickable chips below the assistant message bubble.
- Clicking a chip auto-fills the chat input and sends the message.
- If generation fails, no event is emitted (graceful degradation).

---

### 5.7 `summarized`
Emitted at the start of a streaming response when the conversation history was automatically summarized to fit within the model's context window. This event is emitted before any `token` events.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "summary": "The conversation covered project planning for a new feature...",
  "summarized_message_count": 12,
  "tokens_saved": 3400
}
```

- `summary` — the generated summary text that replaces older messages in context.
- `summarized_message_count` — how many messages were compressed into the summary.
- `tokens_saved` — estimated tokens freed up by summarization.
- The frontend can show a brief notification that summarization occurred.
- Not emitted if no summarization was needed.

---

### 5.8 `error`
Emitted when a non-recoverable error occurs during agent execution.

```json
{
  "session_id": "uuid",
  "message_id": "uuid",
  "code": "model_timeout",
  "message": "The model did not respond within the allowed time."
}
```

**Error codes:**

| Code | Meaning |
|---|---|
| `model_timeout` | Model provider did not respond in time |
| `model_error` | Model provider returned an error |
| `tool_error` | All tool call attempts failed |
| `max_steps_exceeded` | Agent exceeded maximum allowed steps |
| `invalid_output` | Output could not be repaired after max retries |
| `auth_error` | JWT expired or invalid mid-stream |
| `internal_error` | Unexpected backend error |

- After an `error` event the SSE connection is closed by the server.
- The partially generated message (if any) is persisted as a soft-deleted message; the branch remains intact.

---

### 5.9 `heartbeat`
Emitted every 15 seconds if no other event has been sent, to keep the connection alive through proxies.

```
event: heartbeat
data: {}
```

- The frontend ignores this event (no UI action needed).
- Required because nginx and some load balancers close idle connections.

---

## 6. Client-Side Handling (Frontend)

Using `@microsoft/fetch-event-source`:

```typescript
import { fetchEventSource } from '@microsoft/fetch-event-source';

await fetchEventSource(`/api/chat/session/${sessionId}/message`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${jwt}`,
  },
  body: JSON.stringify({ content: userMessage }),
  signal: abortController.signal,

  onmessage(event) {
    const payload = JSON.parse(event.data);
    switch (event.event) {
      case 'token':         appendToken(payload.delta); break;
      case 'tool_start':    showToolProgress(payload); break;
      case 'tool_result':   updateToolProgress(payload); break;
      case 'step_complete': updateStepCount(payload); break;
      case 'message_complete': finalizeMessage(payload); break;
      case 'error':         showError(payload); break;
      case 'heartbeat':     break;
    }
  },

  onerror(err) {
    // Network-level error — show reconnect UI
    throw err; // stop retrying
  },
});
```

The `abortController.signal` is used for the stop-generation button:

```typescript
abortController.abort(); // triggers DELETE /chat/session/:id/stream
```

---

## 7. Backend SSE Response (FastAPI)

```python
from sse_starlette.sse import EventSourceResponse
import json

async def stream_agent_response(session_id, message_id, agent_stream):
    async def event_generator():
        async for maf_event in agent_stream:
            if isinstance(maf_event, TokenEvent):
                yield {
                    "event": "token",
                    "data": json.dumps({
                        "session_id": session_id,
                        "message_id": message_id,
                        "delta": maf_event.delta,
                    })
                }
            elif isinstance(maf_event, ToolStartEvent):
                yield {
                    "event": "tool_start",
                    "data": json.dumps({ ... })
                }
            # ... other event types
        yield {
            "event": "message_complete",
            "data": json.dumps({ ... })
        }

    return EventSourceResponse(event_generator())
```

---

## 8. Nginx Configuration for SSE

SSE requires disabling response buffering in nginx:

```nginx
location /api/ {
  proxy_pass http://backend:8000/;
  proxy_http_version 1.1;

  # Required for SSE — disable buffering so tokens are flushed immediately
  proxy_buffering off;
  proxy_cache off;
  proxy_read_timeout 300s;

  # Keep-alive headers
  proxy_set_header Connection '';
  chunked_transfer_encoding on;
}
```

Without `proxy_buffering off`, nginx will buffer the entire response before sending it, which defeats streaming entirely.

---

## 9. References

- [sse-starlette (GitHub)](https://github.com/sysid/sse-starlette)
- [@microsoft/fetch-event-source (GitHub)](https://github.com/Azure/fetch-event-source)
- [MAF Streaming docs](https://learn.microsoft.com/en-us/agent-framework/agents/index)
- [Agent Framework Integration](agent-framework-integration.md)
- [Backend Architecture](backend-architecture.md)
- [Deployment Guide](deployment.md)
