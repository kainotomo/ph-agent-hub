// =============================================================================
// StreamingProvider — Run-status store tests (Issue #604)
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import React from "react";
import { StreamingProvider, useStreamingContext } from "./StreamingProvider";

const mockUseSessionStatusStream = vi.hoisted(() => vi.fn());

vi.mock("./AuthProvider", () => ({
  useAuth: () => ({ user: { id: "u1" }, loading: false }),
}));

vi.mock("../features/chat/hooks/useSessionStatusStream", () => ({
  useSessionStatusStream: mockUseSessionStatusStream,
}));

function renderStore() {
  return renderHook(() => useStreamingContext(), {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <StreamingProvider>{children}</StreamingProvider>
    ),
  });
}

function latestStreamOptions() {
  const calls = mockUseSessionStatusStream.mock.calls;
  return calls[calls.length - 1][0] as {
    enabled: boolean;
    onSnapshot: (runs: Array<Record<string, unknown>>) => void;
    onEvent: (event: Record<string, unknown>) => void;
  };
}

describe("StreamingProvider", () => {
  beforeEach(() => {
    localStorage.clear();
    mockUseSessionStatusStream.mockClear();
  });

  it("seeds running sessions from the server snapshot", () => {
    const { result } = renderStore();

    act(() => {
      result.current.seedFromSessions([
        { id: "s1", running: true, run_id: "run-1" },
        { id: "s2", running: false },
      ]);
    });

    expect(result.current.streamingSessionIds.has("s1")).toBe(true);
    expect(result.current.streamingSessionIds.has("s2")).toBe(false);
    expect(result.current.getRunId("s1")).toBe("run-1");
  });

  it("applies live status snapshot and run events", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      options.onSnapshot([
        { session_id: "s1", run_id: "run-1", kind: "chat" },
      ]);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(true);

    act(() => {
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "finished",
        outcome: "completed",
      });
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);

    // A completed run for a non-active session raises the unread dot.
    expect(result.current.completedUnreadIds.has("s1")).toBe(true);
    act(() => {
      result.current.markSessionViewed("s1");
    });
    expect(result.current.completedUnreadIds.has("s1")).toBe(false);
  });

  it("does not mark the active session unread while it is visible", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      result.current.setActiveSession("s1");
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "finished",
        outcome: "completed",
      });
    });
    expect(result.current.completedUnreadIds.has("s1")).toBe(false);
  });

  it("does not mark cancelled/failed runs as unread", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    for (const outcome of ["cancelled", "failed", "lost"]) {
      act(() => {
        options.onEvent({
          session_id: `s-${outcome}`,
          run_id: "run-x",
          state: "finished",
          outcome,
        });
      });
    }
    expect(result.current.completedUnreadIds.size).toBe(0);
  });

  it("keeps a locally-started run when a server snapshot omits it", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      result.current.addStreamingSession("s-new", "run-local");
    });
    expect(result.current.streamingSessionIds.has("s-new")).toBe(true);

    act(() => {
      options.onSnapshot([]);
    });
    expect(result.current.streamingSessionIds.has("s-new")).toBe(true);

    // Once the tab's own stream closes, the local entry is removed.
    act(() => {
      result.current.removeStreamingSession("s-new");
    });
    expect(result.current.streamingSessionIds.has("s-new")).toBe(false);
  });

  it("removes server-seeded runs once the list says they stopped", () => {
    const { result } = renderStore();

    act(() => {
      result.current.seedFromSessions([{ id: "s1", running: true, run_id: "r1" }]);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(true);

    act(() => {
      result.current.seedFromSessions([{ id: "s1", running: false }]);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);
  });

  it("keeps a server-known run when a local stream closes, unless forced", () => {
    const { result } = renderStore();

    act(() => {
      result.current.seedFromSessions([{ id: "s1", running: true, run_id: "r1" }]);
    });
    act(() => {
      result.current.removeStreamingSession("s1");
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(true);

    act(() => {
      result.current.removeStreamingSession("s1", true);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);
  });

  it("persists completion-unread ids to localStorage", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "finished",
        outcome: "completed",
      });
    });
    expect(result.current.completedUnreadIds.has("s1")).toBe(true);
    expect(localStorage.getItem("ph.sidebar.completedUnread")).toContain("s1");
  });
});
