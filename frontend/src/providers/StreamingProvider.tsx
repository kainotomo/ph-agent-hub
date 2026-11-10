// =============================================================================
// PH Agent Hub — StreamingProvider / session run-status store (Issue #604)
// =============================================================================
// Replaces the old browser-local ``Set<sessionId>`` with a store whose
// truth comes from the backend run registry:
//
//   • seeded from ``running``/``run_id`` fields on session list responses
//   • kept live by the multiplexed ``GET /chat/streams`` SSE stream
//   • updated locally while this tab owns an SSE send/reconnect
//   • completion-unread is per device (localStorage), cleared when the
//     session is opened
//
// The public hook (``useStreamingContext``) stays the same shape for
// existing consumers: ``streamingSessionIds`` continues to drive the
// sidebar spinner.
// =============================================================================

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAuth } from "./AuthProvider";
import { useSessionStatusStream } from "../features/chat/hooks/useSessionStatusStream";
import {
  applyCompletionUnread,
  applyRunStatusEvent,
  applyStatusSnapshot,
  clearUnread,
  deriveStreamingSessionIds,
  seedRunningFromSessions,
  type RunSnapshotEntry,
  type RunStatusEvent,
  type SessionRunMap,
} from "../features/chat/sessionStatusStore";

/** Per-device completion-unread session ids. */
const UNREAD_STORAGE_KEY = "ph.sidebar.completedUnread";
const MAX_UNREAD_IDS = 100;

function loadUnreadIds(): Set<string> {
  try {
    const raw = localStorage.getItem(UNREAD_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(
      parsed.filter((id): id is string => typeof id === "string"),
    );
  } catch {
    return new Set();
  }
}

interface SessionSeed {
  id: string;
  running?: boolean;
  run_id?: string | null;
}

export interface StreamingContextValue {
  /** Session IDs with a live run (spinner source). */
  streamingSessionIds: Set<string>;
  /** Session IDs whose last run completed while the user was away. */
  completedUnreadIds: Set<string>;
  /** Increments on every start/finish event; lets list consumers refetch. */
  statusRevision: number;
  /** Per-session finish counter. ChatWindow refetches its data when this
   * changes, even if the chat SSE stream never delivered `message_complete`
   * (run finished in another tab / while navigated away). */
  completionRevisions: Record<string, number>;
  /** Register a run this tab just started/reconnected to. */
  addStreamingSession: (id: string, runId?: string | null) => void;
  /** Unregister a run after this tab's SSE stream ends. */
  removeStreamingSession: (id: string, force?: boolean) => void;
  /** Clear every locally-known run (rarely needed). */
  clearStreamingSessions: () => void;
  /** Seed/refresh running state from a session list response. */
  seedFromSessions: (sessions: SessionSeed[]) => void;
  /** Active run id for *id*, if known. */
  getRunId: (id: string) => string | null;
  /** Mark a session as opened — clears its completion-unread marker. */
  markSessionViewed: (id: string) => void;
  /** Track which session the user is currently viewing. */
  setActiveSession: (id: string | null) => void;
}

const StreamingContext = createContext<StreamingContextValue>({
  streamingSessionIds: new Set(),
  completedUnreadIds: new Set(),
  statusRevision: 0,
  completionRevisions: {},
  addStreamingSession: () => {},
  removeStreamingSession: () => {},
  clearStreamingSessions: () => {},
  seedFromSessions: () => {},
  getRunId: () => null,
  markSessionViewed: () => {},
  setActiveSession: () => {},
});

export function StreamingProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user } = useAuth();
  const [runs, setRuns] = useState<SessionRunMap>({});
  const [completedUnreadIds, setCompletedUnreadIds] = useState<Set<string>>(
    () => loadUnreadIds(),
  );
  const [statusRevision, setStatusRevision] = useState(0);
  const [completionRevisions, setCompletionRevisions] = useState<
    Record<string, number>
  >({});

  const runsRef = useRef(runs);
  runsRef.current = runs;
  const activeSessionRef = useRef<string | null>(null);

  // Once the live status stream has delivered a snapshot it is the single
  // source of truth.  Session-list seeds are only a first-paint fallback
  // before that point — otherwise a stale `running: true` from a list
  // fetched during a run would resurrect a finished spinner forever.
  const statusStreamReadyRef = useRef(false);

  // Runs that this client has already observed finish.  A stale list seed
  // must not re-add them (keyed by `sessionId:runId`; `sessionId:*` is a
  // wildcard tombstone for the poll fallback when no run id is known).
  const finishedRunsRef = useRef<Set<string>>(new Set());

  const rememberFinished = useCallback((id: string, runId: string | null) => {
    const key = `${id}:${runId ?? "*"}`;
    finishedRunsRef.current.add(key);
    if (finishedRunsRef.current.size > 500) {
      const oldest = finishedRunsRef.current.values().next().value;
      if (oldest) finishedRunsRef.current.delete(oldest);
    }
  }, []);

  const forgetFinished = useCallback((id: string, runId: string | null) => {
    finishedRunsRef.current.delete(`${id}:${runId ?? "*"}`);
    finishedRunsRef.current.delete(`${id}:*`);
  }, []);

  const isFinished = useCallback((id: string, runId: string | null) => {
    const set = finishedRunsRef.current;
    return set.has(`${id}:${runId ?? "*"}`) || set.has(`${id}:*`);
  }, []);

  const bumpCompletionRevision = useCallback((id: string) => {
    setCompletionRevisions((prev) => ({
      ...prev,
      [id]: (prev[id] ?? 0) + 1,
    }));
  }, []);

  const streamingSessionIds = useMemo(
    () => deriveStreamingSessionIds(runs),
    [runs],
  );

  const addStreamingSession = useCallback(
    (id: string, runId: string | null = null) => {
      if (runId) forgetFinished(id, runId);
      forgetFinished(id, null);
      setRuns((prev) => {
        const existing = prev[id];
        return {
          ...prev,
          [id]: {
            runId: runId ?? existing?.runId ?? null,
            kind: existing?.kind ?? null,
            startedAt: existing?.startedAt ?? null,
            // A run already known from the server stays server-owned: a
            // local stream closing must not hide a run that is still live.
            origin: existing?.origin ?? "local",
          },
        };
      });
    },
    [forgetFinished],
  );

  const removeStreamingSession = useCallback(
    (id: string, force: boolean = false) => {
      if (force) {
        const entry = runsRef.current[id];
        if (entry) {
          rememberFinished(id, entry.runId);
          bumpCompletionRevision(id);
        }
      }
      setRuns((prev) => {
        const entry = prev[id];
        if (!entry) return prev;
        // Local entries end when this tab's stream ends.  Server-known
        // runs are cleared by the status snapshot/event (or a forced
        // removal after the status poll confirms inactivity).
        if (entry.origin === "server" && !force) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
    },
    [rememberFinished, bumpCompletionRevision],
  );

  const clearStreamingSessions = useCallback(() => {
    setRuns({});
  }, []);

  const seedFromSessions = useCallback(
    (sessions: SessionSeed[]) => {
      const liveAuthoritative = statusStreamReadyRef.current;
      // After the live stream's first snapshot it owns removals: a list
      // response that was already in flight when a run started must not
      // erase it.  Additions are still welcome — they are how a run
      // started in another tab is discovered while the SSE is temporarily
      // down — but a finished-run tombstone blocks resurrection.
      const usable = sessions
        .filter((session) => !liveAuthoritative || session.running)
        .map((session) =>
          session.running && isFinished(session.id, session.run_id ?? null)
            ? { ...session, running: false }
            : session,
        );
      setRuns((prev) => seedRunningFromSessions(prev, usable));
    },
    [isFinished],
  );

  const getRunId = useCallback(
    (id: string) => runsRef.current[id]?.runId ?? null,
    [],
  );

  const markSessionViewed = useCallback((id: string) => {
    setCompletedUnreadIds((prev) => clearUnread(prev, id));
  }, []);

  const setActiveSession = useCallback((id: string | null) => {
    activeSessionRef.current = id;
    if (id) {
      setCompletedUnreadIds((prev) => clearUnread(prev, id));
    }
  }, []);

  const handleStatusSnapshot = useCallback(
    (snapshot: RunSnapshotEntry[]) => {
      statusStreamReadyRef.current = true;
      for (const run of snapshot) {
        // A run present in the authoritative snapshot cannot be finished.
        forgetFinished(run.session_id, run.run_id);
      }

      // A periodic/reconnect snapshot is also how a finish can be
      // recovered when the individual `run_status` event was missed.
      const previous = runsRef.current;
      const next = applyStatusSnapshot(previous, snapshot);
      const removed = Object.keys(previous).filter((id) => !(id in next));
      if (removed.length > 0) {
        setCompletionRevisions((prev) => {
          const updated = { ...prev };
          for (const id of removed) updated[id] = (updated[id] ?? 0) + 1;
          return updated;
        });
      }
      setRuns(next);
    },
    [forgetFinished],
  );

  const handleStatusEvent = useCallback(
    (event: RunStatusEvent) => {
      if (event.state === "finished") {
        rememberFinished(event.session_id, event.run_id);
        bumpCompletionRevision(event.session_id);
      } else {
        forgetFinished(event.session_id, event.run_id);
      }
      setRuns((prev) => applyRunStatusEvent(prev, event));
      setStatusRevision((revision) => revision + 1);
      setCompletedUnreadIds((prev) =>
        applyCompletionUnread(prev, event, {
          activeSessionId: activeSessionRef.current,
          documentVisible:
            typeof document === "undefined"
              ? true
              : document.visibilityState === "visible",
        }),
      );
    },
    [forgetFinished, rememberFinished, bumpCompletionRevision],
  );

  useSessionStatusStream({
    enabled: !!user,
    onSnapshot: handleStatusSnapshot,
    onEvent: handleStatusEvent,
  });

  // A different user must not inherit another account's run state.
  const userId = user?.id ?? null;
  useEffect(() => {
    statusStreamReadyRef.current = false;
    finishedRunsRef.current.clear();
    setRuns({});
    setCompletionRevisions({});
  }, [userId]);

  // Persist per-device unread markers so a reload keeps the dot.
  useEffect(() => {
    try {
      localStorage.setItem(
        UNREAD_STORAGE_KEY,
        JSON.stringify([...completedUnreadIds].slice(-MAX_UNREAD_IDS)),
      );
    } catch {
      // Private mode / quota — unread still works for this page life.
    }
  }, [completedUnreadIds]);

  // Returning to a visible tab counts as viewing the active session.
  useEffect(() => {
    const handleVisibility = () => {
      const active = activeSessionRef.current;
      if (document.visibilityState === "visible" && active) {
        setCompletedUnreadIds((prev) => clearUnread(prev, active));
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  const value = useMemo<StreamingContextValue>(
    () => ({
      streamingSessionIds,
      completedUnreadIds,
      statusRevision,
      completionRevisions,
      addStreamingSession,
      removeStreamingSession,
      clearStreamingSessions,
      seedFromSessions,
      getRunId,
      markSessionViewed,
      setActiveSession,
    }),
    [
      streamingSessionIds,
      completedUnreadIds,
      statusRevision,
      completionRevisions,
      addStreamingSession,
      removeStreamingSession,
      clearStreamingSessions,
      seedFromSessions,
      getRunId,
      markSessionViewed,
      setActiveSession,
    ],
  );

  return (
    <StreamingContext.Provider value={value}>
      {children}
    </StreamingContext.Provider>
  );
}

export function useStreamingContext(): StreamingContextValue {
  return useContext(StreamingContext);
}
