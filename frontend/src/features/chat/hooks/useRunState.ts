// =============================================================================
// PH Agent Hub — useRunState (Issue #609)
// =============================================================================
// Owns the client state machine for the visible conversation and reconciles
// it from `GET /chat/session/:id/run-state`:
//
//   * on mount / session change,
//   * on tab focus and visibility,
//   * on demand (reconnect, `stream_resync`, after a close).
//
// The transport callbacks call `dispatch(...)`; the reducer guarantees that
// no path ends in `running` after the stream closed.
// =============================================================================

import { useCallback, useEffect, useRef, useState } from "react";
import {
  INITIAL_RUN_STATE,
  reduceRunState,
  type RunInput,
  type RunSnapshot,
} from "../runStateMachine";
import { getRunState } from "../services/chat";

export interface UseRunStateOptions {
  /** The visible conversation; `null` when no session is on screen. */
  sessionId: string | null;
  /** Disable server reconciliation (demo/widget modes have no run rows). */
  enabled?: boolean;
}

export interface UseRunStateResult {
  snapshot: RunSnapshot;
  dispatch: (input: RunInput) => void;
  /** Start a new turn (optimistic submission). */
  begin: () => void;
  /** Read the authoritative record and adopt its terminal state. */
  reconcile: () => Promise<void>;
  /** Clear a terminal banner / start over. */
  reset: () => void;
}

export function useRunState({
  sessionId,
  enabled = true,
}: UseRunStateOptions): UseRunStateResult {
  const [snapshot, setSnapshot] = useState<RunSnapshot>({
    ...INITIAL_RUN_STATE,
  });
  // Tracks the session the state belongs to so a late response for a
  // previous conversation cannot overwrite the current one.
  const sessionRef = useRef<string | null>(sessionId);

  const dispatch = useCallback((input: RunInput) => {
    setSnapshot((prev) => reduceRunState(prev, input));
  }, []);

  const begin = useCallback(() => {
    dispatch({ type: "submit" });
  }, [dispatch]);

  const reset = useCallback(() => {
    dispatch({ type: "reset" });
  }, [dispatch]);

  const reconcile = useCallback(async () => {
    if (!sessionId || !enabled) return;
    const target = sessionId;
    try {
      const state = await getRunState(target);
      if (sessionRef.current !== target) return; // stale response
      dispatch({
        type: "server_state",
        state: state.state,
        runId: state.run_id,
        errorCode: state.error_code,
        message: state.error_message,
        messageId: state.message_id,
        persisted: state.persisted,
      });
    } catch {
      // A failed read must not fabricate a terminal state: keep whatever
      // the local machine knows and retry on the next focus/reconnect.
    }
  }, [sessionId, enabled, dispatch]);

  useEffect(() => {
    sessionRef.current = sessionId;
    dispatch({ type: "reset" });
    void reconcile();
  }, [sessionId, dispatch, reconcile]);

  useEffect(() => {
    if (!enabled) return;
    const onFocus = () => {
      void reconcile();
    };
    // `focus` does not always fire when a tab becomes visible again (e.g.
    // iframe/embedded contexts, or a page that never lost keyboard focus).
    // Reconcile on visibility too so a run that finished while hidden cannot
    // leave the view "thinking" (Issue #616).
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        void reconcile();
      }
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, reconcile]);

  return { snapshot, dispatch, begin, reconcile, reset };
}
