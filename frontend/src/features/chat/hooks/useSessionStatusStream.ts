// =============================================================================
// PH Agent Hub — useSessionStatusStream (Issue #604)
// =============================================================================
// Keeps the tab's run-status view in sync with the backend's authoritative
// run registry.
//
// It used to hold a long-lived SSE connection (`GET /chat/streams`) for the
// lifetime of every tab.  That cost one of the browser's six HTTP/1.1
// connections per origin per tab, so three tabs streaming at once exhausted
// the budget and every further request queued forever (the "third session
// hangs, refresh fixes it" symptom).  Polling a lightweight snapshot costs
// one short request every few seconds instead, and a dropped poll is just a
// retry rather than a silently dead socket.
//
// The endpoint returns the active runs plus the outcome of any run the tab
// already knew about that has since finished, so the spinner clears and the
// cached messages are invalidated in the same round trip.
// =============================================================================

import { useEffect, useRef } from "react";
import { getToken, refreshAccessToken } from "../../../services/api";
import type {
  RunSnapshotEntry,
  RunStatusEvent,
} from "../sessionStatusStore";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

/** Poll interval while the tab is visible. */
const VISIBLE_INTERVAL_MS = 3_000;
/** Slower cadence in a background tab — the run is still tracked exactly. */
const HIDDEN_INTERVAL_MS = 15_000;
/** Backoff cap after repeated failures (server down, offline). */
const MAX_BACKOFF_MS = 30_000;

export interface SessionStatusStreamOptions {
  /** Poll only while the user is authenticated. */
  enabled: boolean;
  /** Called with the authoritative snapshot on every successful poll. */
  onSnapshot: (runs: RunSnapshotEntry[]) => void;
  /** Called for every run start/finish transition since the last poll. */
  onEvent: (event: RunStatusEvent) => void;
}

interface KnownRun {
  sessionId: string;
  runId: string | null;
}

interface RunsResponse {
  runs?: RunSnapshotEntry[];
  finished?: Array<{
    session_id: string;
    run_id: string | null;
    outcome?: RunStatusEvent["outcome"];
    revision?: number;
  }>;
}

function intervalForVisibility(): number {
  if (typeof document === "undefined") return VISIBLE_INTERVAL_MS;
  return document.visibilityState === "hidden"
    ? HIDDEN_INTERVAL_MS
    : VISIBLE_INTERVAL_MS;
}

/** Build the `?sessions=` hint so the server can report finishes we know. */
function knownRunsQuery(known: Map<string, KnownRun>): string {
  if (known.size === 0) return "";
  const pairs = [...known.values()].map(
    (run) => `${run.sessionId}:${run.runId ?? ""}`,
  );
  return `?sessions=${encodeURIComponent(pairs.join(","))}`;
}

export function useSessionStatusStream({
  enabled,
  onSnapshot,
  onEvent,
}: SessionStatusStreamOptions): void {
  // Keep the latest callbacks without restarting the poll loop on render.
  const onSnapshotRef = useRef(onSnapshot);
  const onEventRef = useRef(onEvent);
  onSnapshotRef.current = onSnapshot;
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;

    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;
    let inFlight = false;
    let backoffMs = 0;
    // Runs this tab has already reported as active.  A run that disappears
    // from the snapshot while still listed here has finished (or been lost).
    const known = new Map<string, KnownRun>();

    const clearTimer = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = (delayMs: number) => {
      if (disposed) return;
      clearTimer();
      timer = setTimeout(poll, delayMs);
    };

    const applyResponse = (body: RunsResponse) => {
      const runs = body.runs ?? [];
      const activeIds = new Set(runs.map((run) => run.session_id));

      // Finishes the server confirmed for runs we asked about.
      for (const finished of body.finished ?? []) {
        known.delete(finished.session_id);
        onEventRef.current({
          session_id: finished.session_id,
          run_id: finished.run_id,
          state: "finished",
          outcome: finished.outcome,
          revision: finished.revision,
        });
      }

      // Starts (including runs begun in another tab or device).
      for (const run of runs) {
        const previous = known.get(run.session_id);
        if (!previous || previous.runId !== run.run_id) {
          known.set(run.session_id, {
            sessionId: run.session_id,
            runId: run.run_id,
          });
          onEventRef.current({
            session_id: run.session_id,
            run_id: run.run_id,
            state: "running",
            kind: run.kind ?? null,
            started_at: run.started_at ?? null,
            revision: run.revision,
          });
        }
      }

      // Anything we believed was running but the registry no longer lists,
      // and the server could not report an outcome for (marker expired).
      for (const [sessionId, run] of known) {
        if (!activeIds.has(sessionId)) {
          known.delete(sessionId);
          onEventRef.current({
            session_id: sessionId,
            run_id: run.runId,
            state: "finished",
            outcome: "lost",
          });
        }
      }

      onSnapshotRef.current(runs);
    };

    const poll = async () => {
      if (disposed || inFlight) return;
      inFlight = true;

      controller = new AbortController();
      const token = getToken();
      let nextDelay = intervalForVisibility();

      try {
        const response = await fetch(
          `${BASE_URL}/chat/runs${knownRunsQuery(known)}`,
          {
            method: "GET",
            headers: {
              Accept: "application/json",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            signal: controller.signal,
            cache: "no-store",
          },
        );

        if (response.status === 401) {
          // Match the SSE behaviour: refresh once, then retry immediately.
          const refreshed = await refreshAccessToken();
          if (refreshed && !disposed) {
            nextDelay = 0;
          }
          return;
        }

        if (!response.ok) {
          throw new Error(`run status failed with status ${response.status}`);
        }

        const body = (await response.json()) as RunsResponse;
        if (disposed) return;
        backoffMs = 0;
        applyResponse(body);
      } catch (error) {
        if (disposed || controller.signal.aborted) return;
        backoffMs = Math.min(
          backoffMs === 0 ? VISIBLE_INTERVAL_MS * 2 : backoffMs * 2,
          MAX_BACKOFF_MS,
        );
        nextDelay = Math.max(nextDelay, backoffMs);
      } finally {
        inFlight = false;
        if (!disposed) schedule(nextDelay);
      }
    };

    // A tab that becomes visible again should not wait out the hidden
    // cadence; users expect the spinner to be right the moment they look.
    const handleVisibility = () => {
      if (disposed) return;
      if (document.visibilityState === "visible") {
        schedule(0);
      }
    };

    document.addEventListener("visibilitychange", handleVisibility);
    poll();

    return () => {
      disposed = true;
      clearTimer();
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [enabled]);
}
