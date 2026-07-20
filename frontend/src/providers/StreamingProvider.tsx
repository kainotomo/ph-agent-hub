// =============================================================================
// PH Agent Hub — StreamingProvider
// =============================================================================
// React context that shares the currently-streaming session ID between
// ChatWindow and SessionSidebar.  When ChatWindow starts/resumes a stream,
// it sets streamingSessionId; when the stream ends, it clears it.
// The sidebar reads this to show a spinner on the running session.
//
// This context survives navigation — if the user navigates away while
// an agent is running, the sidebar can still show the spinner.
// =============================================================================

import React, { createContext, useContext, useState, useCallback } from "react";

interface StreamingContextValue {
  /** The session ID currently being streamed, or null if idle. */
  streamingSessionId: string | null;
  /** Set the currently-streaming session ID. */
  setStreamingSessionId: (id: string | null) => void;
  /** Clear the streaming state. */
  clearStreamingSession: () => void;
}

const StreamingContext = createContext<StreamingContextValue>({
  streamingSessionId: null,
  setStreamingSessionId: () => {},
  clearStreamingSession: () => {},
});

export function StreamingProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(
    null,
  );

  const clearStreamingSession = useCallback(() => {
    setStreamingSessionId(null);
  }, []);

  return (
    <StreamingContext.Provider
      value={{
        streamingSessionId,
        setStreamingSessionId,
        clearStreamingSession,
      }}
    >
      {children}
    </StreamingContext.Provider>
  );
}

export function useStreamingContext(): StreamingContextValue {
  return useContext(StreamingContext);
}
