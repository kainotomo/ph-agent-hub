// =============================================================================
// PH Agent Hub — useSessionStatusStream (Issue #604)
// =============================================================================
// Subscribes once per tab to the backend's multiplexed run-status SSE
// stream (``GET /chat/streams``).  The first event is a full snapshot;
// subsequent events are start/finish deltas.  Reconnects with exponential
// backoff, stops permanently on 401/403, and never creates a second
// connection per tab.
// =============================================================================

import { useEffect, useRef } from "react";
import {
  fetchEventSource,
  EventStreamContentType,
} from "@microsoft/fetch-event-source";
import { getToken, refreshAccessToken } from "../../../services/api";
import type {
  RunSnapshotEntry,
  RunStatusEvent,
} from "../sessionStatusStore";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";
const INITIAL_RETRY_MS = 1_000;
const MAX_RETRY_MS = 30_000;

/** Thrown when the server rejects the stream and retrying cannot help. */
class StopStatusStream extends Error {}

/** Thrown after a successful token refresh: reconnect immediately. */
class RefreshAndRetry extends Error {}

export interface SessionStatusStreamOptions {
  /** Connect only while the user is authenticated. */
  enabled: boolean;
  /** Called with the seed snapshot after a (re)connect. */
  onSnapshot: (runs: RunSnapshotEntry[]) => void;
  /** Called for every run start/finish transition. */
  onEvent: (event: RunStatusEvent) => void;
}

export function useSessionStatusStream({
  enabled,
  onSnapshot,
  onEvent,
}: SessionStatusStreamOptions): void {
  // Keep the latest callbacks without re-opening the connection on every
  // render.
  const onSnapshotRef = useRef(onSnapshot);
  const onEventRef = useRef(onEvent);
  onSnapshotRef.current = onSnapshot;
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;

    let disposed = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let retryMs = INITIAL_RETRY_MS;

    const connect = async () => {
      if (disposed) return;

      const token = getToken();
      controller = new AbortController();
      let fatal = false;

      try {
        await fetchEventSource(`${BASE_URL}/chat/streams`, {
          method: "GET",
          headers: {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          openWhenHidden: true,
          signal: controller.signal,
          async onopen(response) {
            if (
              response.ok &&
              response.headers
                .get("content-type")
                ?.includes(EventStreamContentType)
            ) {
              retryMs = INITIAL_RETRY_MS;
              return;
            }
            if (response.status === 401) {
              // Long-lived SSE connections bypass api()'s 401 refresh.  Try
              // once, then retry immediately if a new token was issued.
              const refreshed = await refreshAccessToken();
              if (refreshed) {
                throw new RefreshAndRetry("Token refreshed");
              }
              throw new StopStatusStream(
                "Status stream unauthorized (401)",
              );
            }
            if (response.status === 403) {
              throw new StopStatusStream(
                "Status stream forbidden (403)",
              );
            }
            throw new Error(
              `Status stream failed with status ${response.status}`,
            );
          },
          onmessage(ev) {
            try {
              const parsed = JSON.parse(ev.data);
              if (ev.event === "status_snapshot") {
                onSnapshotRef.current(parsed.runs ?? []);
              } else if (ev.event === "run_status") {
                onEventRef.current(parsed as RunStatusEvent);
              }
            } catch {
              // Ignore malformed individual events; the next snapshot heals.
            }
          },
          onclose() {
            // Server closed (restart / idle timeout) — reconnect below.
          },
          onerror(err) {
            // Throw so the library does not schedule its own retry; this
            // hook owns the backoff schedule.
            throw err;
          },
        });
      } catch (err) {
        if (err instanceof StopStatusStream) fatal = true;
        if (err instanceof RefreshAndRetry) retryMs = 0;
      }

      if (disposed || fatal) return;

      retryTimer = setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, MAX_RETRY_MS);
    };

    void connect();

    return () => {
      disposed = true;
      controller?.abort();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [enabled]);
}
