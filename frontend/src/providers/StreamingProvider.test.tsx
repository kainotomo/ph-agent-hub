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

  it("does not resurrect a finished run from a stale list seed", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      result.current.seedFromSessions([
        { id: "s1", running: true, run_id: "run-1" },
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

    // A list response fetched while the run was still active is stale.
    act(() => {
      result.current.seedFromSessions([
        { id: "s1", running: true, run_id: "run-1" },
      ]);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);
  });

  it("does not let a list seed remove a live run after the status snapshot", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      options.onSnapshot([
        { session_id: "s1", run_id: "run-1", kind: "chat" },
      ]);
    });
    act(() => {
      result.current.seedFromSessions([{ id: "s1", running: false }]);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(true);

    // A different running session discovered from a list response (for
    // example while the SSE is temporarily down) is still added.
    act(() => {
      result.current.seedFromSessions([
        { id: "s2", running: true, run_id: "run-2" },
      ]);
    });
    expect(result.current.streamingSessionIds.has("s2")).toBe(true);
  });

  it("does not resurrect a run force-removed by the status poll", () => {
    const { result } = renderStore();

    act(() => {
      result.current.seedFromSessions([
        { id: "s1", running: true, run_id: "run-1" },
      ]);
    });
    act(() => {
      result.current.removeStreamingSession("s1", true);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);

    act(() => {
      result.current.seedFromSessions([
        { id: "s1", running: true, run_id: "run-1" },
      ]);
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);
  });

  it("bumps statusRevision on live transitions", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();
    const before = result.current.statusRevision;

    act(() => {
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "running",
        kind: "chat",
      });
    });

    expect(result.current.statusRevision).toBe(before + 1);
  });

  it("bumps a per-session completion revision on finish", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      options.onSnapshot([{ session_id: "s1", run_id: "run-1" }]);
    });
    expect(result.current.completionRevisions.s1 ?? 0).toBe(0);

    act(() => {
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "finished",
        outcome: "completed",
      });
    });
    expect(result.current.completionRevisions.s1).toBe(1);

    act(() => {
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "finished",
        outcome: "completed",
      });
    });
    expect(result.current.completionRevisions.s1).toBe(2);
  });

  it("bumps completion revision for forced removals and snapshot recovery", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    // Force-removed by the sidebar status poll (finish event was missed).
    act(() => {
      result.current.seedFromSessions([
        { id: "s1", running: true, run_id: "run-1" },
      ]);
    });
    act(() => {
      result.current.removeStreamingSession("s1", true);
    });
    expect(result.current.completionRevisions.s1).toBe(1);

    // A reconnect/periodic snapshot that no longer contains a known run.
    act(() => {
      options.onSnapshot([{ session_id: "s2", run_id: "run-2" }]);
    });
    act(() => {
      options.onSnapshot([]);
    });
    expect(result.current.completionRevisions.s2).toBe(1);
  });

  it("tracks several concurrently running sessions independently", () => {
    const { result } = renderStore();
    const options = latestStreamOptions();

    act(() => {
      options.onSnapshot([
        { session_id: "s1", run_id: "run-1", kind: "chat" },
        { session_id: "s2", run_id: "run-2", kind: "chat" },
      ]);
    });
    expect(result.current.streamingSessionIds.size).toBe(2);

    act(() => {
      options.onEvent({
        session_id: "s1",
        run_id: "run-1",
        state: "finished",
        outcome: "completed",
      });
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(false);
    expect(result.current.streamingSessionIds.has("s2")).toBe(true);
    expect(result.current.getRunId("s2")).toBe("run-2");
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
