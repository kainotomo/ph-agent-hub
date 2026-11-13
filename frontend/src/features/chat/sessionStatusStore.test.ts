import { describe, it, expect } from "vitest";
import {
  applyStatusSnapshot,
  applyRunStatusEvent,
  seedRunningFromSessions,
  applyCompletionUnread,
  clearUnread,
  deriveStreamingSessionIds,
  type SessionRunMap,
} from "./sessionStatusStore";

describe("applyStatusSnapshot", () => {
  it("seeds server entries and drops stale server entries", () => {
    const prev: SessionRunMap = {
      a: { runId: null, kind: null, startedAt: null, origin: "server", revision: 10 },
    };
    const next = applyStatusSnapshot(prev, [
      { session_id: "a", run_id: "r1", kind: "local", started_at: "t1", revision: 20 },
      { session_id: "b", run_id: null, kind: null, started_at: null, revision: 0 },
    ]);
    expect(next).toEqual({
      a: { runId: "r1", kind: "local", startedAt: "t1", origin: "server", revision: 20 },
      b: { runId: null, kind: null, startedAt: null, origin: "server", revision: 0 },
    });
    expect(next).not.toHaveProperty("c");
  });

  it("keeps a local entry missing from the snapshot and replaces a local entry present in it", () => {
    const prev: SessionRunMap = {
      a: { runId: null, kind: null, startedAt: null, origin: "local", revision: 0 },
      b: { runId: null, kind: null, startedAt: null, origin: "local", revision: 0 },
    };
    const next = applyStatusSnapshot(prev, [
      { session_id: "b", run_id: "r2", kind: "local", started_at: "t2", revision: 5 },
    ]);
    expect(next).toEqual({
      a: { runId: null, kind: null, startedAt: null, origin: "local", revision: 0 },
      b: { runId: "r2", kind: "local", startedAt: "t2", origin: "server", revision: 5 },
    });
  });

  it("does not let an older snapshot overwrite a newer entry", () => {
    const prev: SessionRunMap = {
      a: { runId: "r1", kind: null, startedAt: null, origin: "server", revision: 20 },
    };
    const next = applyStatusSnapshot(prev, [
      { session_id: "a", run_id: "r1", kind: null, started_at: null, revision: 10 },
    ]);
    expect(next.a.revision).toBe(20);
  });
});

describe("applyRunStatusEvent", () => {
  it("adds a running entry and preserves a local origin", () => {
    const prev: SessionRunMap = {
      a: { runId: null, kind: null, startedAt: null, origin: "local", revision: 0 },
    };
    const next = applyRunStatusEvent(prev, {
      session_id: "a",
      run_id: "r1",
      state: "running",
      kind: "local",
      started_at: "t1",
      revision: 30,
    });
    expect(next).toEqual({
      a: { runId: "r1", kind: "local", startedAt: "t1", origin: "local", revision: 30 },
    });
  });

  it("removes the entry on a finished event", () => {
    const prev: SessionRunMap = {
      a: { runId: "r1", kind: null, startedAt: null, origin: "server", revision: 30 },
    };
    const next = applyRunStatusEvent(prev, {
      session_id: "a",
      run_id: "r1",
      state: "finished",
      revision: 31,
    });
    expect(next).toEqual({});
  });

  it("ignores a stale finish for an older run", () => {
    const prev: SessionRunMap = {
      a: { runId: "r2", kind: null, startedAt: null, origin: "server", revision: 40 },
    };
    const next = applyRunStatusEvent(prev, {
      session_id: "a",
      run_id: "r1",
      state: "finished",
      revision: 31,
    });
    expect(next).toBe(prev);
  });

  it("ignores a running event older than the entry it would replace", () => {
    const prev: SessionRunMap = {
      a: { runId: "r2", kind: null, startedAt: null, origin: "server", revision: 40 },
    };
    const next = applyRunStatusEvent(prev, {
      session_id: "a",
      run_id: "r1",
      state: "running",
      revision: 30,
    });
    expect(next).toBe(prev);
  });
});

describe("seedRunningFromSessions", () => {
  it("sets running sessions, removes stale server entries, and keeps local entries", () => {
    const prev: SessionRunMap = {
      a: { runId: null, kind: null, startedAt: null, origin: "server", revision: 0 },
      b: { runId: null, kind: null, startedAt: null, origin: "local", revision: 0 },
    };
    const next = seedRunningFromSessions(prev, [
      { id: "a", running: true, run_id: "r1", runtime_revision: 10 },
      { id: "b", running: false },
      { id: "c", running: true, runtime_revision: 10 },
    ]);
    expect(next).toEqual({
      a: { runId: "r1", kind: null, startedAt: null, origin: "server", revision: 10 },
      b: { runId: null, kind: null, startedAt: null, origin: "local", revision: 0 },
      c: { runId: null, kind: null, startedAt: null, origin: "server", revision: 10 },
    });
  });

  it("does not resurrect a finished run from an older list", () => {
    const prev: SessionRunMap = {
      a: { runId: "r2", kind: null, startedAt: null, origin: "server", revision: 40 },
    };
    const next = seedRunningFromSessions(prev, [
      { id: "a", running: true, run_id: "r1", runtime_revision: 30 },
    ]);
    expect(next).toBe(prev);
  });

  it("does not remove a live run from an older list", () => {
    const prev: SessionRunMap = {
      a: { runId: "r1", kind: null, startedAt: null, origin: "server", revision: 40 },
    };
    const next = seedRunningFromSessions(prev, [
      { id: "a", running: false, runtime_revision: 30 },
    ]);
    expect(next).toBe(prev);
  });

  it("removes a run once the list is newer", () => {
    const prev: SessionRunMap = {
      a: { runId: "r1", kind: null, startedAt: null, origin: "server", revision: 40 },
    };
    const next = seedRunningFromSessions(prev, [
      { id: "a", running: false, runtime_revision: 41 },
    ]);
    expect(next).toEqual({});
  });
});

describe("applyCompletionUnread", () => {
  const event = {
    session_id: "a",
    run_id: null,
    state: "finished" as const,
    outcome: "completed" as const,
  };
  const context = { activeSessionId: null, documentVisible: true };

  it("adds the session id only when it is not active and the document is visible", () => {
    const next = applyCompletionUnread(new Set(["x"]), event, context);
    expect([...next]).toEqual(["x", "a"]);
  });

  it("adds the session id when the document is hidden", () => {
    const next = applyCompletionUnread(new Set(["x"]), event, {
      activeSessionId: null,
      documentVisible: false,
    });
    expect([...next]).toEqual(["x", "a"]);
  });

  it("does not add the session id when the session is active", () => {
    const next = applyCompletionUnread(new Set(["x"]), event, {
      activeSessionId: "a",
      documentVisible: true,
    });
    expect([...next]).toEqual(["x"]);
  });

  it("never adds unread for cancelled, failed, or lost outcomes", () => {
    for (const outcome of ["cancelled", "failed", "lost"] as const) {
      const next = applyCompletionUnread(new Set(["x"]), {
        ...event,
        outcome,
      }, context);
      expect([...next]).toEqual(["x"]);
    }
  });
});

describe("clearUnread", () => {
  it("removes only the given id", () => {
    const next = clearUnread(new Set(["a", "b"]), "a");
    expect([...next]).toEqual(["b"]);
  });
});

describe("deriveStreamingSessionIds", () => {
  it("returns the keys of the map", () => {
    const next = deriveStreamingSessionIds({
      a: { runId: null, kind: null, startedAt: null, origin: "server", revision: 0 },
      b: { runId: null, kind: null, startedAt: null, origin: "server", revision: 0 },
    });
    expect([...next]).toEqual(["a", "b"]);
  });
});
