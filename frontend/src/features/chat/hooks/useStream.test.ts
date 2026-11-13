// =============================================================================
// useStream — Unit Tests
// =============================================================================
// Tests cover: SSE connection lifecycle (startStream, startRegenerateStream,
// startEditStream), token accumulation callbacks, stop/cancel via DELETE,
// error handling, demo/widget fallback, and abort cleanup on unmount.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useStream } from "./useStream";

// ---------------------------------------------------------------------------
// Mock external dependencies
// ---------------------------------------------------------------------------

const { mockFetchEventSource, mockFetch, mockGetToken } = vi.hoisted(() => ({
  mockFetchEventSource: vi.fn(),
  mockFetch: vi.fn(),
  mockGetToken: vi.fn(),
}));

vi.mock("@microsoft/fetch-event-source", () => ({
  fetchEventSource: mockFetchEventSource,
  EventStreamContentType: "text/event-stream",
}));

vi.mock("../../../services/api", () => ({
  getToken: mockGetToken,
}));

// Mock global fetch for DELETE /stream and demo-mode fallback
vi.stubGlobal("fetch", mockFetch);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Simulate an SSE event on the latest fetchEventSource config. */
function sseEvent(
  event: string,
  data: Record<string, unknown>,
) {
  const lastCall = mockFetchEventSource.mock.calls[mockFetchEventSource.mock.calls.length - 1];
  if (!lastCall) throw new Error("fetchEventSource was never called");
  const config = lastCall[1] as {
    onmessage?: (ev: { event: string; data: string }) => void;
  };
  config.onmessage?.({ event, data: JSON.stringify(data) });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useStream", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetToken.mockReturnValue("test-token");
    mockFetch.mockReset();
    // Default: fetchEventSource calls onclose after a brief delay
    mockFetchEventSource.mockImplementation(
      async (_url: string, config: { onclose?: () => void }) => {
        setTimeout(() => config.onclose?.(), 50);
      },
    );
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // ── SSE connection lifecycle ──────────────────────────────────────────

  it("startStream POSTs to the correct URL with headers and body", async () => {
    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startStream(
        "session-1",
        "Hello",
        ["file-1"],
        0.7,
        { custom: "data" },
        {},
      );
    });

    expect(mockFetchEventSource).toHaveBeenCalledWith(
      "/api/chat/session/session-1/message",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          Authorization: "Bearer test-token",
        }),
        openWhenHidden: true,
      }),
    );

    const bodyArg = mockFetchEventSource.mock.calls[0][1] as { body: string };
    expect(JSON.parse(bodyArg.body)).toEqual({
      content: "Hello",
      file_ids: ["file-1"],
      temperature: 0.7,
      session_data: { custom: "data" },
    });
  });

  // ── Token accumulation ────────────────────────────────────────────────

  it("calls onToken callback when token events arrive", async () => {
    const { result } = renderHook(() => useStream());
    const onToken = vi.fn();

    act(() => {
      result.current.startStream("session-1", "Hello", [], undefined, {}, { onToken });
    });

    await waitFor(() => {
      expect(mockFetchEventSource).toHaveBeenCalled();
    });

    act(() => {
      sseEvent("token", { delta: "Hello", message_id: "msg-1" });
      sseEvent("token", { delta: " world", message_id: "msg-1" });
    });

    expect(onToken).toHaveBeenCalledTimes(2);
    expect(onToken).toHaveBeenNthCalledWith(1, "Hello", "msg-1");
    // Hook passes through the delta as-is (includes leading space)
    expect(onToken).toHaveBeenNthCalledWith(2, " world", "msg-1");
  });

  // ── onMessageComplete callback ────────────────────────────────────────

  it("calls onMessageComplete when message_complete event arrives", async () => {
    const { result } = renderHook(() => useStream());
    const onMessageComplete = vi.fn();

    act(() => {
      result.current.startStream("session-1", "Hello", [], undefined, {}, { onMessageComplete });
    });

    await waitFor(() => expect(mockFetchEventSource).toHaveBeenCalled());

    act(() => {
      sseEvent("message_complete", {
        session_id: "session-1",
        message_id: "msg-1",
        content: "Hello world",
        model_id: "model-abc",
        model_name: "DeepSeek V4",
        tokens_in: 10,
        tokens_out: 20,
      });
    });

    expect(onMessageComplete).toHaveBeenCalledWith(
      expect.objectContaining({
        session_id: "session-1",
        message_id: "msg-1",
        content: "Hello world",
        model_id: "model-abc",
      }),
    );
  });

  // ── Workflow approval required ─────────────────────────────────────────

  it("calls onWorkflowApprovalRequired when workflow_approval_required event arrives", async () => {
    const { result } = renderHook(() => useStream());
    const onWorkflowApprovalRequired = vi.fn();

    act(() => {
      result.current.startStream("session-1", "Hello", [], undefined, {}, { onWorkflowApprovalRequired });
    });

    await waitFor(() => expect(mockFetchEventSource).toHaveBeenCalled());

    act(() => {
      sseEvent("workflow_approval_required", {
        type: "function_approval_request",
        request_id: "req-abc",
        step_id: "step-123",
        tool_name: "ask_user",
        arguments: { question: "Continue?" },
      });
    });

    expect(onWorkflowApprovalRequired).toHaveBeenCalledTimes(1);
    expect(onWorkflowApprovalRequired).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "function_approval_request",
        request_id: "req-abc",
        step_id: "step-123",
        tool_name: "ask_user",
        arguments: { question: "Continue?" },
      }),
    );
  });

  // ── Delegated sub-agent events (Issue #574) ───────────────────────────

  it.each([
    "subagent_start",
    "subagent_token",
    "subagent_reasoning_token",
    "subagent_tool_start",
    "subagent_tool_result",
    "subagent_complete",
    "subagent_error",
  ])("dispatches %s to onSubagentEvent", async (eventName) => {
    const { result } = renderHook(() => useStream());
    const onSubagentEvent = vi.fn();

    act(() => {
      result.current.startStream(
        "session-1",
        "Hello",
        [],
        undefined,
        {},
        { onSubagentEvent },
      );
    });

    await waitFor(() => expect(mockFetchEventSource).toHaveBeenCalled());

    act(() => {
      sseEvent(eventName, { parent_call_id: "call-1", delta: "x" });
    });

    expect(onSubagentEvent).toHaveBeenCalledTimes(1);
    expect(onSubagentEvent).toHaveBeenCalledWith(
      eventName,
      expect.objectContaining({ parent_call_id: "call-1" }),
    );
  });

  // ── Stop / cancel stream ──────────────────────────────────────────────

  it("stopStream sends DELETE and sets streaming to false", async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValue({ ok: true });

    const { result } = renderHook(() => useStream());

    // Override fetchEventSource to never close (simulate in-flight stream)
    mockFetchEventSource.mockImplementation(
      () => new Promise(() => {}),
    );

    act(() => {
      result.current.startStream("session-1", "Hello", [], undefined, {}, {});
    });

    // streaming flips to true synchronously inside startStream
    expect(result.current.streaming).toBe(true);

    await act(async () => {
      result.current.stopStream("session-1");
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/chat/session/session-1/stream",
      expect.objectContaining({ method: "DELETE" }),
    );

    // stopStream sets streaming=false via a 2s safety-net timeout
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(result.current.streaming).toBe(false);

    vi.useRealTimers();
  });

  // ── Error handling ────────────────────────────────────────────────────

  it("calls onError when error event arrives", async () => {
    const { result } = renderHook(() => useStream());
    const onError = vi.fn();

    act(() => {
      result.current.startStream("session-1", "Hello", [], undefined, {}, { onError });
    });

    await waitFor(() => expect(mockFetchEventSource).toHaveBeenCalled());

    act(() => {
      sseEvent("error", {
        session_id: "session-1",
        message_id: "msg-1",
        error: "Something went wrong",
      });
    });

    expect(onError).toHaveBeenCalledWith("Something went wrong", "msg-1");
  });

  // ── startRegenerateStream ─────────────────────────────────────────────

  it("startRegenerateStream POSTs to the regenerate endpoint", async () => {
    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startRegenerateStream("session-1", "msg-1", {});
    });

    expect(mockFetchEventSource).toHaveBeenCalledWith(
      "/api/chat/session/session-1/message/msg-1/regenerate",
      expect.any(Object),
    );
  });

  // ── startEditStream ───────────────────────────────────────────────────

  it("startEditStream PUTs to the edit endpoint with content", async () => {
    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startEditStream("session-1", "msg-1", "Edited content", 0.5, {});
    });

    expect(mockFetchEventSource).toHaveBeenCalledWith(
      "/api/chat/session/session-1/message/msg-1",
      expect.objectContaining({ method: "PUT" }),
    );

    const bodyArg = mockFetchEventSource.mock.calls[0][1] as { body: string };
    expect(JSON.parse(bodyArg.body)).toEqual({
      content: "Edited content",
      temperature: 0.5,
    });
  });

  // ── Demo/widget fallback mode ─────────────────────────────────────────

  it("uses regular fetch instead of SSE when apiPrefix is not 'chat'", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({
        content: "Demo response",
        message_id: "demo-msg-1",
      }),
    });

    const { result } = renderHook(() => useStream("demo"));
    const onToken = vi.fn();
    const onMessageComplete = vi.fn();

    await act(async () => {
      await result.current.startStream("demo-session", "Hello", [], undefined, {}, {
        onToken,
        onMessageComplete,
      });
    });

    expect(mockFetchEventSource).not.toHaveBeenCalled();
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/demo/session/message",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("Hello"),
      }),
    );

    expect(onMessageComplete).toHaveBeenCalled();
    expect(onToken).toHaveBeenCalled();
  });

  // ── Session busy (Issue #604) ─────────────────────────────────────────

  it("reports session_busy on HTTP 409 without calling onError", async () => {
    mockFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "already running",
          code: "session_busy",
          run_id: "run-1",
        }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    mockFetchEventSource.mockImplementation(
      async (_url: string, config: any) => {
        const response = await config.fetch(
          "/api/chat/session/session-1/message",
          {},
        );
        expect(response.status).toBe(409);
        const err = Object.assign(
          new Error("Request failed with status 409"),
          { status: 409 },
        );
        try {
          config.onerror(err);
        } catch {
          // onerror throws deliberately to prevent a library retry.
        }
        throw err;
      },
    );

    const onSessionBusy = vi.fn();
    const onError = vi.fn();
    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startStream(
        "session-1",
        "Hello",
        [],
        undefined,
        {},
        { onSessionBusy, onError },
      );
    });

    expect(onSessionBusy).toHaveBeenCalledWith({
      sessionId: "session-1",
      runId: "run-1",
    });
    expect(onError).not.toHaveBeenCalled();
  });

  // ── Cleanup on unmount ────────────────────────────────────────────────

  it("aborts the stream controller on unmount when stream is active", async () => {
    const { result, unmount } = renderHook(() => useStream());

    mockFetchEventSource.mockImplementation(
      () => new Promise(() => {}),
    );

    act(() => {
      result.current.startStream("session-1", "Hello", [], undefined, {}, {});
    });

    expect(result.current.streaming).toBe(true);

    const abortSpy = vi.spyOn(AbortController.prototype, "abort");

    unmount();

    expect(abortSpy).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Stream resume + liveness (Issue #604 follow-up)
// ---------------------------------------------------------------------------
// The backend stamps each SSE event with `<run_id>:<seq>`.  A reconnect
// replays the server buffer, so the hook must drop events it already
// applied — otherwise the visible answer is duplicated — and it must notice
// a silently dead link instead of spinning until a manual refresh.

function latestConfig() {
  const lastCall =
    mockFetchEventSource.mock.calls[mockFetchEventSource.mock.calls.length - 1];
  if (!lastCall) throw new Error("fetchEventSource was never called");
  return lastCall[1] as {
    onmessage?: (ev: { event: string; data: string; id?: string }) => void;
    onopen?: (response: Response) => Promise<void>;
    onclose?: () => void;
    signal?: AbortSignal;
  };
}

function sseEventWithId(
  event: string,
  data: Record<string, unknown>,
  id: string,
) {
  latestConfig().onmessage?.({ event, data: JSON.stringify(data), id });
}

async function openStream() {
  const config = latestConfig();
  await config.onopen?.({
    ok: true,
    headers: { get: () => "text/event-stream" },
  } as unknown as Response);
}

describe("useStream stream resume and liveness", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    mockGetToken.mockReturnValue("test-token");
    mockFetchEventSource.mockImplementation(() => new Promise(() => {}));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("drops replayed events at or below the cursor", () => {
    const { result } = renderHook(() => useStream());
    const onToken = vi.fn();

    act(() => {
      result.current.startStream("s1", "hi", [], undefined, {}, { onToken });
    });

    act(() => {
      sseEventWithId("token", { delta: "Hello", message_id: "m1" }, "run-1:1");
      sseEventWithId("token", { delta: " world", message_id: "m1" }, "run-1:2");
      // A reconnect replays both — neither may be applied twice.
      sseEventWithId("token", { delta: "Hello", message_id: "m1" }, "run-1:1");
      sseEventWithId("token", { delta: " world", message_id: "m1" }, "run-1:2");
      sseEventWithId("token", { delta: "!", message_id: "m1" }, "run-1:3");
    });

    expect(onToken.mock.calls.map((call) => call[0])).toEqual([
      "Hello",
      " world",
      "!",
    ]);
  });

  it("accepts a new run when the run id changes", () => {
    const { result } = renderHook(() => useStream());
    const onToken = vi.fn();

    act(() => {
      result.current.startStream("s1", "hi", [], undefined, {}, { onToken });
    });

    act(() => {
      sseEventWithId("token", { delta: "first", message_id: "m1" }, "run-1:9");
      // A different run restarts the sequence at 1.
      sseEventWithId("token", { delta: "second", message_id: "m2" }, "run-2:1");
    });

    expect(onToken.mock.calls.map((call) => call[0])).toEqual([
      "first",
      "second",
    ]);
  });

  it("reports a stall when a live stream goes silent", async () => {
    const { result } = renderHook(() => useStream());
    const onStall = vi.fn();

    mockFetchEventSource.mockImplementation(() => new Promise(() => {}));
    act(() => {
      result.current.startStream("s1", "hi", [], undefined, {}, { onStall });
    });
    await act(async () => {
      await openStream();
    });

    // Healthy traffic keeps the watchdog quiet.
    act(() => {
      sseEventWithId("token", { delta: "a", message_id: "m1" }, "run-1:1");
      vi.advanceTimersByTime(20_000);
    });
    expect(onStall).not.toHaveBeenCalled();

    // 25 s of silence (no tokens, no heartbeat) means the link is dead, and
    // the abort is silent — the hook must settle the state itself (#616).
    act(() => {
      vi.advanceTimersByTime(26_000);
    });

    expect(onStall).toHaveBeenCalledWith("s1");
    expect(result.current.streaming).toBe(false);
    expect(result.current.streamingSessionId).toBeNull();
  });

  it("treats stream_resync as terminal and notifies the caller", async () => {
    const { result } = renderHook(() => useStream());
    const onResync = vi.fn();

    act(() => {
      result.current.startStream("s1", "hi", [], undefined, {}, { onResync });
    });
    await act(async () => {
      await openStream();
    });
    expect(result.current.streaming).toBe(true);

    act(() => {
      latestConfig().onmessage?.({
        event: "stream_resync",
        data: JSON.stringify({ reason: "buffer_gap", oldest_seq: 900 }),
      });
    });

    expect(onResync).toHaveBeenCalledTimes(1);
    // The hook aborted the link: a silent abort must not leave the view
    // "thinking" forever (Issue #616).
    expect(result.current.streaming).toBe(false);
  });

  it("delivers run_terminal and settles the transport even if the socket stays open", async () => {
    const { result } = renderHook(() => useStream());
    const onRunTerminal = vi.fn();

    act(() => {
      result.current.startStream("s1", "hi", [], undefined, {}, {
        onRunTerminal,
      });
    });
    await act(async () => {
      await openStream();
    });
    expect(result.current.streaming).toBe(true);

    act(() => {
      sseEventWithId(
        "run_terminal",
        {
          session_id: "s1",
          outcome: "completed",
          persisted: true,
          message_id: "m1",
        },
        "run-1:9",
      );
    });

    expect(onRunTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ outcome: "completed", persisted: true }),
    );
    // The transport is still attached, but the turn is over.
    expect(result.current.streaming).toBe(false);
    expect(result.current.streamingSessionId).toBeNull();
  });

  it("resetStream disarms the previous stream's watchdog", async () => {
    const { result } = renderHook(() => useStream());
    const onStall = vi.fn();

    act(() => {
      result.current.startStream("s1", "hi", [], undefined, {}, { onStall });
    });
    await act(async () => {
      await openStream();
    });

    act(() => {
      result.current.resetStream();
      vi.advanceTimersByTime(60_000);
    });

    expect(onStall).not.toHaveBeenCalled();
    expect(result.current.streaming).toBe(false);
  });

  it("ignores a superseded stream's close", async () => {
    const { result } = renderHook(() => useStream());

    act(() => {
      result.current.startStream("s1", "first", [], undefined, {}, {});
    });
    const firstConfig = latestConfig();
    await act(async () => {
      await openStream();
    });

    // A new stream supersedes the first while it is still draining.
    act(() => {
      result.current.startStream("s1", "second", [], undefined, {}, {});
    });
    await act(async () => {
      await openStream();
    });
    expect(result.current.streaming).toBe(true);

    act(() => {
      firstConfig.onclose?.();
    });

    // The stale close must not clear the live turn's state.
    expect(result.current.streaming).toBe(true);
  });
});


// =============================================================================
// Issue #609 — transport failures are not producer failures
// =============================================================================

describe("useStream — transport failure routing (Issue #609)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetToken.mockReturnValue("test-token");
  });

  it("routes an SSE transport failure to onTransportError, not onError", async () => {
    mockFetchEventSource.mockImplementation(
      async (
        _url: string,
        config: { onerror?: (err: Error) => void },
      ) => {
        // The real library throws from onerror to reject its promise.
        config.onerror?.(new Error("network drop"));
      },
    );
    const onError = vi.fn();
    const onTransportError = vi.fn();

    const { result } = renderHook(() => useStream());
    await act(async () => {
      await result.current.startStream(
        "session-1",
        "Hello",
        [],
        undefined,
        undefined,
        { onError, onTransportError },
      );
    });

    expect(onTransportError).toHaveBeenCalledTimes(1);
    expect(onTransportError.mock.calls[0][0]).toMatch(/network drop/);
    expect(onError).not.toHaveBeenCalled();
  });
});
