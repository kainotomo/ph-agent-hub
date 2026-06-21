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
  const lastCall = mockFetchEventSource.mock.calls.at(-1);
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
