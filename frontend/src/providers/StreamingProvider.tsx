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

  const runsRef = useRef(runs);
  runsRef.current = runs;
  const activeSessionRef = useRef<string | null>(null);

  const streamingSessionIds = useMemo(
    () => deriveStreamingSessionIds(runs),
    [runs],
  );

  const addStreamingSession = useCallback(
    (id: string, runId: string | null = null) => {
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
    [],
  );

  const removeStreamingSession = useCallback(
    (id: string, force: boolean = false) => {
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
    [],
  );

  const clearStreamingSessions = useCallback(() => {
    setRuns({});
  }, []);

  const seedFromSessions = useCallback((sessions: SessionSeed[]) => {
    setRuns((prev) => seedRunningFromSessions(prev, sessions));
  }, []);

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

  const handleStatusSnapshot = useCallback((snapshot: RunSnapshotEntry[]) => {
    setRuns((prev) => applyStatusSnapshot(prev, snapshot));
  }, []);

  const handleStatusEvent = useCallback((event: RunStatusEvent) => {
    setRuns((prev) => applyRunStatusEvent(prev, event));
    setCompletedUnreadIds((prev) =>
      applyCompletionUnread(prev, event, {
        activeSessionId: activeSessionRef.current,
        documentVisible:
          typeof document === "undefined"
            ? true
            : document.visibilityState === "visible",
      }),
    );
  }, []);

  useSessionStatusStream({
    enabled: !!user,
    onSnapshot: handleStatusSnapshot,
    onEvent: handleStatusEvent,
  });

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
