// =============================================================================
// useRunState — Issue #609
// =============================================================================
// The hook reconciles from the server record (mount, focus, explicit call)
// instead of trusting transport closure.  A failed read must not fabricate a
// terminal state, and a stale "idle" read must not erase a live turn.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

const { mockGetRunState } = vi.hoisted(() => ({
  mockGetRunState: vi.fn(),
}));

vi.mock("../services/chat", () => ({
  getRunState: mockGetRunState,
}));

import { useRunState } from "./useRunState";

describe("useRunState (Issue #609)", () => {
  beforeEach(() => {
    mockGetRunState.mockReset();
    mockGetRunState.mockResolvedValue({
      run_id: null,
      state: "idle",
    });
  });

  it("adopts a failed server record on mount", async () => {
    mockGetRunState.mockResolvedValue({
      run_id: "run-1",
      state: "failed",
      error_code: "model_timeout",
      error_message: "provider timed out",
      persisted: false,
    });

    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );

    await waitFor(() => expect(result.current.snapshot.phase).toBe("failed"));
    expect(result.current.snapshot.errorCode).toBe("model_timeout");
    expect(result.current.snapshot.reason).toBe("provider timed out");
  });

  it("adopts a completed server record only when an answer was persisted", async () => {
    mockGetRunState.mockResolvedValue({
      run_id: "run-1",
      state: "completed",
      persisted: true,
      message_id: "msg-1",
    });

    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );

    await waitFor(() => expect(result.current.snapshot.phase).toBe("done"));
    expect(result.current.snapshot.messageId).toBe("msg-1");
  });

  it("does not clobber a live local turn with a stale idle read", async () => {
    mockGetRunState.mockResolvedValue({
      run_id: null,
      state: "idle",
    });

    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );

    act(() => {
      result.current.begin();
      result.current.dispatch({ type: "stream_open", runId: "run-1" });
    });

    await waitFor(() => expect(mockGetRunState).toHaveBeenCalled());
    expect(result.current.snapshot.phase).toBe("running");
  });

  it("marks a run lost when the stream closes while running", async () => {
    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );

    act(() => {
      result.current.begin();
      result.current.dispatch({ type: "stream_open" });
      result.current.dispatch({ type: "stream_closed" });
    });

    expect(result.current.snapshot.phase).toBe("lost");
  });

  it("reconciles on window focus", async () => {
    mockGetRunState.mockResolvedValue({
      run_id: "run-1",
      state: "lost",
      error_code: "orphaned",
    });

    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );
    await waitFor(() => expect(mockGetRunState).toHaveBeenCalledTimes(1));

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    await waitFor(() => expect(mockGetRunState).toHaveBeenCalledTimes(2));
    expect(result.current.snapshot.phase).toBe("lost");
  });

  it("reconciles when the tab becomes visible again", async () => {
    mockGetRunState.mockResolvedValue({
      run_id: "run-1",
      state: "completed",
      persisted: true,
      message_id: "msg-1",
    });

    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );
    await waitFor(() => expect(mockGetRunState).toHaveBeenCalledTimes(1));

    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    visibility.mockRestore();

    await waitFor(() => expect(mockGetRunState).toHaveBeenCalledTimes(2));
    expect(result.current.snapshot.phase).toBe("done");
  });

  it("keeps local state when the server read fails", async () => {
    mockGetRunState.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() =>
      useRunState({ sessionId: "session-1" }),
    );

    act(() => {
      result.current.begin();
      result.current.dispatch({ type: "stream_open" });
    });

    await waitFor(() => expect(mockGetRunState).toHaveBeenCalled());
    expect(result.current.snapshot.phase).toBe("running");
  });
});
