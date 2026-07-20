// =============================================================================
// PH Agent Hub — StreamingProvider
// =============================================================================
// React context that shares the set of currently-streaming session IDs
// between ChatWindow and SessionSidebar.  Multiple agents can run
// concurrently (different sessions), and each shows its own spinner
// in the sidebar.
//
// addStreamingSession(sessionId)  — called when a stream starts
// removeStreamingSession(sessionId) — called when a stream ends
// clearStreamingSessions()  — called to clear all (e.g. on unmount)
// =============================================================================

import React, { createContext, useContext, useState, useCallback } from "react";

interface StreamingContextValue {
  /** Set of session IDs currently being streamed. */
  streamingSessionIds: Set<string>;
  /** Register a session as actively streaming. */
  addStreamingSession: (id: string) => void;
  /** Unregister a session (stream ended or detected inactive). */
  removeStreamingSession: (id: string) => void;
  /** Clear all streaming sessions (e.g. on context unmount). */
  clearStreamingSessions: () => void;
}

const StreamingContext = createContext<StreamingContextValue>({
  streamingSessionIds: new Set(),
  addStreamingSession: () => {},
  removeStreamingSession: () => {},
  clearStreamingSessions: () => {},
});

export function StreamingProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [streamingSessionIds, setStreamingSessionIds] = useState<Set<string>>(
    () => new Set(),
  );

  const addStreamingSession = useCallback((id: string) => {
    setStreamingSessionIds((prev) => {
      if (prev.has(id)) return prev; // Already present — no update needed
      return new Set(prev).add(id);
    });
  }, []);

  const removeStreamingSession = useCallback((id: string) => {
    setStreamingSessionIds((prev) => {
      if (!prev.has(id)) return prev; // Not present — no update needed
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const clearStreamingSessions = useCallback(() => {
    setStreamingSessionIds(new Set());
  }, []);

  return (
    <StreamingContext.Provider
      value={{
        streamingSessionIds,
        addStreamingSession,
        removeStreamingSession,
        clearStreamingSessions,
      }}
    >
      {children}
    </StreamingContext.Provider>
  );
}

export function useStreamingContext(): StreamingContextValue {
  return useContext(StreamingContext);
}
