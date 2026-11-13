export interface RunSnapshotEntry {
  session_id: string;
  run_id: string | null;
  kind?: string | null;
  started_at?: string | null;
  /** Monotonic per-session runtime revision from the backend. */
  revision?: number;
}

export interface RunStatusEvent {
  session_id: string;
  run_id: string | null;
  state: "running" | "finished";
  kind?: string | null;
  started_at?: string | null;
  outcome?: "completed" | "cancelled" | "failed" | "lost";
  /** Monotonic per-session runtime revision from the backend. */
  revision?: number;
}

export interface SessionRunEntry {
  runId: string | null;
  kind: string | null;
  startedAt: string | null;
  origin: "server" | "local";
  /**
   * Backend runtime revision (see `session_runtime.revision_of`).  The
   * store never lets an older answer overwrite a newer one, which replaces
   * the old "first snapshot wins" flags and finished-run tombstones.
   */
  revision: number;
}

export type SessionRunMap = Record<string, SessionRunEntry>;

/** An event/seed without a revision sorts before everything real. */
function revisionOf(
  value: { revision?: number; runtime_revision?: number } | undefined,
): number {
  const revision = value?.revision ?? value?.runtime_revision;
  return typeof revision === "number" && Number.isFinite(revision) ? revision : 0;
}

export function applyStatusSnapshot(
  prev: SessionRunMap,
  runs: RunSnapshotEntry[],
): SessionRunMap {
  const next: SessionRunMap = {};
  for (const run of runs) {
    const revision = revisionOf(run);
    const existing = prev[run.session_id];
    if (existing && existing.revision > revision) {
      // A slower, older poll finished after a newer one.
      next[run.session_id] = existing;
      continue;
    }
    next[run.session_id] = {
      runId: run.run_id,
      kind: run.kind ?? null,
      startedAt: run.started_at ?? null,
      origin: "server",
      revision,
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
  const revision = revisionOf(event);
  const existing = prev[event.session_id];

  if (event.state === "running") {
    if (existing && existing.revision > revision) return prev;
    const next: SessionRunMap = { ...prev };
    next[event.session_id] = {
      runId: event.run_id,
      kind: event.kind ?? null,
      startedAt: event.started_at ?? null,
      origin: existing?.origin ?? "server",
      revision,
    };
    return next;
  }

  if (event.state === "finished") {
    if (!existing) return prev;
    // A late finish for an older run must not clear a newer one.
    if (existing.revision > revision) return prev;
    const next: SessionRunMap = { ...prev };
    delete next[event.session_id];
    return next;
  }

  return prev;
}

export function seedRunningFromSessions(
  prev: SessionRunMap,
  sessions: Array<{
    id: string;
    running?: boolean;
    run_id?: string | null;
    runtime_revision?: number;
  }>,
  /**
   * Highest revision this tab has already applied per session.  Kept even
   * after an entry is removed (the equivalent of a tombstone): without it a
   * list fetched while a run was active could resurrect a run that has
   * since finished.
   */
  knownRevisions: Record<string, number> = {},
): SessionRunMap {
  let changed = false;
  const next: SessionRunMap = { ...prev };
  for (const session of sessions) {
    const revision = revisionOf(session);
    const existing = next[session.id];
    const floor = Math.max(
      existing?.revision ?? 0,
      knownRevisions[session.id] ?? 0,
    );

    if (session.running === true) {
      // An older list must not resurrect a run that has since finished.
      if (floor > revision) continue;
      const runId = session.run_id ?? null;
      if (
        existing &&
        existing.origin === "server" &&
        existing.runId === runId &&
        existing.revision === revision
      ) {
        continue;
      }
      next[session.id] = {
        runId,
        kind: null,
        startedAt: null,
        origin: "server",
        revision,
      };
      changed = true;
    } else if (
      existing &&
      existing.origin === "server" &&
      floor <= revision
    ) {
      // A list fetched after the finish clears the spinner; one fetched
      // before it (older revision) cannot.
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
