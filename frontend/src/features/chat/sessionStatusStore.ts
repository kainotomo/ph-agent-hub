export interface RunSnapshotEntry {
  session_id: string;
  run_id: string | null;
  kind?: string | null;
  started_at?: string | null;
}

export interface RunStatusEvent {
  session_id: string;
  run_id: string | null;
  state: "running" | "finished";
  kind?: string | null;
  started_at?: string | null;
  outcome?: "completed" | "cancelled" | "failed" | "lost";
}

export interface SessionRunEntry {
  runId: string | null;
  kind: string | null;
  startedAt: string | null;
  origin: "server" | "local";
}

export type SessionRunMap = Record<string, SessionRunEntry>;

export function applyStatusSnapshot(
  prev: SessionRunMap,
  runs: RunSnapshotEntry[],
): SessionRunMap {
  const next: SessionRunMap = {};
  for (const run of runs) {
    next[run.session_id] = {
      runId: run.run_id,
      kind: run.kind ?? null,
      startedAt: run.started_at ?? null,
      origin: "server",
    };
  }
  for (const [sessionId, entry] of Object.entries(prev)) {
    if (entry.origin === "local" && !next[sessionId]) {
      next[sessionId] = entry;
    }
  }
  return next;
}

export function applyRunStatusEvent(
  prev: SessionRunMap,
  event: RunStatusEvent,
): SessionRunMap {
  if (event.state === "running") {
    const next: SessionRunMap = { ...prev };
    const existing = next[event.session_id];
    if (existing) {
      next[event.session_id] = {
        ...existing,
        runId: event.run_id,
        kind: event.kind ?? null,
        startedAt: event.started_at ?? null,
      };
    } else {
      next[event.session_id] = {
        runId: event.run_id,
        kind: event.kind ?? null,
        startedAt: event.started_at ?? null,
        origin: "server",
      };
    }
    return next;
  }
  if (event.state === "finished") {
    const next: SessionRunMap = { ...prev };
    if (next[event.session_id]) {
      delete next[event.session_id];
    }
    return next;
  }
  return { ...prev };
}

export function seedRunningFromSessions(
  prev: SessionRunMap,
  sessions: Array<{
    id: string;
    running?: boolean;
    run_id?: string | null;
  }>,
): SessionRunMap {
  let changed = false;
  const next: SessionRunMap = { ...prev };
  for (const session of sessions) {
    if (session.running === true) {
      const runId = session.run_id ?? null;
      const existing = next[session.id];
      if (
        existing &&
        existing.origin === "server" &&
        existing.runId === runId
      ) {
        continue;
      }
      next[session.id] = {
        runId,
        kind: null,
        startedAt: null,
        origin: "server",
      };
      changed = true;
    } else if (next[session.id] && next[session.id].origin === "server") {
      delete next[session.id];
      changed = true;
    }
  }
  // Returning the same object when nothing changed keeps callers (e.g. a
  // React effect seeding from a list response) from looping on setState.
  return changed ? next : prev;
}

export function applyCompletionUnread(
  unread: Set<string>,
  event: RunStatusEvent,
  context: { activeSessionId: string | null; documentVisible: boolean },
): Set<string> {
  if (
    event.state === "finished" &&
    event.outcome === "completed" &&
    (event.session_id !== context.activeSessionId || context.documentVisible === false)
  ) {
    const next = new Set(unread);
    next.add(event.session_id);
    return next;
  }
  return new Set(unread);
}

export function clearUnread(unread: Set<string>, sessionId: string): Set<string> {
  const next = new Set(unread);
  next.delete(sessionId);
  return next;
}

export function deriveStreamingSessionIds(map: SessionRunMap): Set<string> {
  return new Set(Object.keys(map));
}
