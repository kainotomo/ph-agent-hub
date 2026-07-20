// =============================================================================
// PH Agent Hub — useStream Hook
// =============================================================================
// fetchEventSource wrapper; handles token/tool_start/tool_result/
// step_complete/message_complete/error/heartbeat events per
// streaming-protocol.md §5.
// POST to /chat/session/:id/message with Accept: text/event-stream.
// =============================================================================

import { useState, useCallback, useEffect, useRef } from "react";
import {
  fetchEventSource,
  EventStreamContentType,
} from "@microsoft/fetch-event-source";
import { getToken } from "../../../services/api";
import { useStreamingContext } from "../../../providers/StreamingProvider";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TokenEvent {
  event: "token";
  data: {
    session_id: string;
    message_id: string;
    delta: string;
    step_name?: string;
  };
}

export interface ToolStartEvent {
  event: "tool_start";
  data: {
    session_id: string;
    message_id: string;
    tool_name: string;
    tool_call_id: string;
    arguments: Record<string, unknown>;
    /** Issue #447 — present when multiple tools execute in parallel */
    batch_id?: string;
  };
}

export interface ToolResultEvent {
  event: "tool_result";
  data: {
    session_id: string;
    message_id: string;
    tool_call_id: string;
    tool_name: string;
    success: boolean;
    result_summary: unknown;
    /** Issue #447 — present when multiple tools execute in parallel */
    batch_id?: string;
  };
}

export interface MemoryUpdatedEvent {
  event: "memory_updated";
  data: {
    session_id: string;
    message_id: string;
    tool_name: string;
    action: "saved" | "deleted";
    key: string | null;
    success: boolean;
  };
}

export interface StepCompleteEvent {
  event: "step_complete";
  data: {
    session_id: string;
    message_id: string;
    step_name: string;
    /** Issue #447 — present when the step contained a parallel batch */
    batch_id?: string;
    /** Issue #447 — number of tools in the parallel batch */
    batch_size?: number;
  };
}

export interface MessageCompleteEvent {
  event: "message_complete";
  data: {
    session_id: string;
    message_id: string;
    content: string;
    model_id: string;
    model_name?: string;
    model_provider?: string;
    tokens_in?: number;
    tokens_out?: number;
  };
}

export interface ReasoningTokenEvent {
  event: "reasoning_token";
  data: {
    session_id: string;
    message_id: string;
    delta: string;
  };
}

export interface ErrorEvent {
  event: "error";
  data: {
    session_id: string;
    message_id: string;
    error: string;
  };
}

export interface FollowUpQuestionsEvent {
  event: "follow_up_questions";
  data: {
    session_id: string;
    message_id: string;
    questions: string[];
  };
}

export interface SummarizedEvent {
  event: "summarized";
  data: {
    session_id: string;
    message_id: string;
    summary: string;
    summarized_message_count: number;
    tokens_saved: number;
  };
}

export interface TagsUpdatedEvent {
  event: "tags_updated";
  data: {
    session_id: string;
    tags: string[];
  };
}

export interface HeartbeatEvent {
  event: "heartbeat";
  data: Record<string, never>;
}

export type StreamEvent =
  | TokenEvent
  | ToolStartEvent
  | ToolResultEvent
  | MemoryUpdatedEvent
  | StepCompleteEvent
  | MessageCompleteEvent
  | ReasoningTokenEvent
  | FollowUpQuestionsEvent
  | SummarizedEvent
  | TagsUpdatedEvent
  | ErrorEvent
  | HeartbeatEvent;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useStream(apiPrefix: string = "chat") {
  const [streaming, setStreaming] = useState(false);
  const [streamingSessionId, setStreamingSessionId] = useState<string | null>(
    null,
  );
  const abortRef = useRef<AbortController | null>(null);

  // Issue #455 — share streaming state with sidebar via context.
  // addStreamingSession: called when a stream starts (regardless of session).
  // removeStreamingSession: called when a stream ends for this session.
  const { addStreamingSession, removeStreamingSession } = useStreamingContext();

  // Abort any in-flight SSE stream when the hook unmounts. Without this,
  // @microsoft/fetch-event-source reconnects on document visibility change
  // after navigation, re-POSTing the message (Issue #124).
  //
  // Issue #455: We intentionally do NOT call stopStream here — the agent
  // should keep running in the background.  We only abort the local SSE
  // subscription so the component doesn't leak memory.
  //
  // IMPORTANT: Do NOT clear the streaming context here.  When the user
  // navigates from /chat/:sessionId to /chat (no session), ChatWindow
  // unmounts and this cleanup runs.  If we cleared context here, the
  // sidebar spinner would disappear even though the agent is still
  // running in the background.  Context is only cleared on actual
  // stream end (message_complete event, onerror, stopStream).
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const startStream = useCallback(
    async (
      sessionId: string,
      content: string,
      fileIds: string[] | undefined,
      temperature: number | undefined,
      sessionData: Record<string, unknown> | undefined,
      handlers: {
        onToken?: (token: string, messageId: string) => void;
        onToolStart?: (data: ToolStartEvent["data"]) => void;
        onToolResult?: (data: ToolResultEvent["data"]) => void;
        onMemoryUpdated?: (data: MemoryUpdatedEvent["data"]) => void;
        onStepComplete?: (data: StepCompleteEvent["data"]) => void;
        onMessageComplete?: (data: MessageCompleteEvent["data"]) => void;
        onReasoningToken?: (delta: string, messageId: string) => void;
        onFollowUpQuestions?: (questions: string[]) => void;
        onSummarized?: (data: SummarizedEvent["data"]) => void;
        onTagsUpdated?: (data: TagsUpdatedEvent["data"]) => void;
        onError?: (error: string, messageId: string) => void;
        onClose?: () => void;
      },
    ) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      setStreamingSessionId(sessionId);
      addStreamingSession(sessionId);

      const token = getToken();

      const isSessionInPath = apiPrefix === "chat";
      const messageUrl = isSessionInPath
        ? `${BASE_URL}/chat/session/${sessionId}/message`
        : `${BASE_URL}/${apiPrefix}/session/message`;

      // For non-chat modes (demo, widget), use regular fetch instead of
      // fetchEventSource, which has issues with SSE POST requests.
      if (!isSessionInPath) {
        try {
          // Don't use AbortController for demo — it conflicts with
          // React StrictMode's lifecycle and causes signal aborts.
          const res = await fetch(messageUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({
              content,
              file_ids: fileIds || [],
              ...(temperature !== undefined ? { temperature } : {}),
              ...(sessionData ? { session_data: sessionData } : {}),
            }),
          });
          if (!res.ok) {
            throw new Error(`Request failed with status ${res.status}`);
          }
          const data = await res.json();
          // Call onMessageComplete first so it finalizes the message
          // without clearing content (the demo mode onMessageComplete
          // handler sets the final content from the data).
          handlers.onMessageComplete?.({
            session_id: sessionId,
            message_id: data.message_id || "",
            content: data.content || "",
            model_id: "",
            model_name: "",
          });
          // Deliver tokens so the streaming UI shows the response
          handlers.onToken?.(data.content || "", data.message_id || "");
        } catch (err) {
          handlers.onError?.(String(err), "");
        } finally {
          setStreaming(false);
          setStreamingSessionId(null);
          removeStreamingSession(sessionId);
          handlers.onClose?.();
        }
        return;
      }

      try {
        await fetchEventSource(
          messageUrl,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({
              content,
              file_ids: fileIds || [],
              ...(temperature !== undefined ? { temperature } : {}),
              ...(sessionData ? { session_data: sessionData } : {}),
            }),
            openWhenHidden: true,
            signal: controller.signal,
            async onopen(response) {
              if (
                response.ok &&
                response.headers
                  .get("content-type")
                  ?.includes(EventStreamContentType)
              ) {
                return;
              }
              throw new Error(
                `Stream failed with status ${response.status}`,
              );
            },
            onmessage(ev) {
              try {
                const parsed = JSON.parse(ev.data);
                switch (ev.event) {
                  case "token":
                    handlers.onToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "tool_start":
                    handlers.onToolStart?.(parsed);
                    break;
                  case "tool_result":
                    handlers.onToolResult?.(parsed);
                    break;
                  case "memory_updated":
                    handlers.onMemoryUpdated?.(parsed);
                    break;
                  case "step_complete":
                    handlers.onStepComplete?.(parsed);
                    break;
                  case "message_complete":
                    handlers.onMessageComplete?.(parsed);
                    break;
                  case "reasoning_token":
                    handlers.onReasoningToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "follow_up_questions":
                    handlers.onFollowUpQuestions?.(parsed.questions || []);
                    break;
                  case "summarized":
                    handlers.onSummarized?.(parsed);
                    break;
                  case "tags_updated":
                    handlers.onTagsUpdated?.(parsed);
                    break;
                  case "error":
                    handlers.onError?.(parsed.message || parsed.error || "Unknown error", parsed.message_id);
                    break;
                  case "heartbeat":
                    // Ignore heartbeats
                    break;
                }
              } catch {
                // Ignore parse errors on individual events
              }
            },
            onclose() {
              // Only remove from active set on NATURAL stream end.
              // When the controller is aborted (resetStream/navigation),
              // the agent is still running — keep the session active.
              if (!controller.signal.aborted) {
                removeStreamingSession(sessionId);
              }
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
            },
            onerror(err) {
              if (controller.signal.aborted) {
                setStreaming(false);
                setStreamingSessionId(null);
                handlers.onClose?.();
                return;
              }
              removeStreamingSession(sessionId);
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
              throw err;
            },
          },
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          handlers.onError?.(String(err), "");
          removeStreamingSession(sessionId);
        }
        setStreaming(false);
        setStreamingSessionId(null);
      }
    },
    [],
  );

  const stopStream = useCallback(
    async (sessionId: string) => {
      // 1. Tell the backend to cancel via Redis flag — the agent runner
      //    checks this on every token yield, breaks, persists the partial
      //    response, and ends the stream normally.
      try {
        const token = getToken();
        const streamUrl = apiPrefix === "chat"
          ? `${BASE_URL}/chat/session/${sessionId}/stream`
          : `${BASE_URL}/${apiPrefix}/session/stream`;
        await fetch(streamUrl, {
          method: "DELETE",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
      } catch {
        // Best effort
      }
      // Remove from active set immediately — user explicitly stopped this agent.
      removeStreamingSession(sessionId);
      // 2. Schedule a safety-net abort in case the backend doesn't finish
      //    within 2 s (e.g. stuck in a long tool call).  When the backend
      //    shuts down cleanly the onclose handler fires and does the final
      //    setStreaming(false) + setStreamingSessionId(null).
      const controller = abortRef.current;
      if (controller) {
        setTimeout(() => {
          if (!controller.signal.aborted) {
            controller.abort();
            setStreaming(false);
            setStreamingSessionId(null);
          }
        }, 2000);
      } else {
        setStreaming(false);
        setStreamingSessionId(null);
      }
    },
    [],
  );

  const startReconnect = useCallback(
    async (
      sessionId: string,
      handlers: {
        onToken?: (token: string, messageId: string) => void;
        onToolStart?: (data: ToolStartEvent["data"]) => void;
        onToolResult?: (data: ToolResultEvent["data"]) => void;
        onMemoryUpdated?: (data: MemoryUpdatedEvent["data"]) => void;
        onStepComplete?: (data: StepCompleteEvent["data"]) => void;
        onMessageComplete?: (data: MessageCompleteEvent["data"]) => void;
        onReasoningToken?: (delta: string, messageId: string) => void;
        onFollowUpQuestions?: (questions: string[]) => void;
        onSummarized?: (data: SummarizedEvent["data"]) => void;
        onTagsUpdated?: (data: TagsUpdatedEvent["data"]) => void;
        onError?: (error: string, messageId: string) => void;
        onClose?: () => void;
      },
    ): Promise<void> => {
      const controller = new AbortController();
      abortRef.current = controller;

      // IMPORTANT: Do NOT set streaming=true yet — wait until onopen
      // confirms the SSE connection is live.  This prevents a race where
      // startReconnect sets streaming=true, then immediately an onerror/
      // onclose fires (because the agent finished between getStreamStatus
      // and the reconnect request), leaving streaming=true with no actual
      // SSE stream.
      //
      // streamingSessionId and context are set inside onopen.
      const token = getToken();

      try {
        await fetchEventSource(
          `${BASE_URL}/chat/session/${sessionId}/stream`,
          {
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
                // SSE connection confirmed — now signal that streaming is live.
                setStreaming(true);
                setStreamingSessionId(sessionId);
                addStreamingSession(sessionId);
                return;
              }
              // Agent finished between status check and reconnect request.
              // Backend returns 200 OK with {"active": false} as JSON.
              if (response.ok) {
                // Silently close — no error shown to user.
                handlers.onClose?.();
                return;
              }
              // If the stream is not available (agent already finished),
              // treat it as a normal close.
              if (response.status === 404 || response.status === 400) {
                handlers.onClose?.();
                return;
              }
              throw new Error(
                `Reconnect stream failed with status ${response.status}`,
              );
            },
            onmessage(ev) {
              try {
                const parsed = JSON.parse(ev.data);
                switch (ev.event) {
                  case "token":
                    handlers.onToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "tool_start":
                    handlers.onToolStart?.(parsed);
                    break;
                  case "tool_result":
                    handlers.onToolResult?.(parsed);
                    break;
                  case "memory_updated":
                    handlers.onMemoryUpdated?.(parsed);
                    break;
                  case "step_complete":
                    handlers.onStepComplete?.(parsed);
                    break;
                  case "message_complete":
                    handlers.onMessageComplete?.(parsed);
                    break;
                  case "reasoning_token":
                    handlers.onReasoningToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "follow_up_questions":
                    handlers.onFollowUpQuestions?.(parsed.questions || []);
                    break;
                  case "summarized":
                    handlers.onSummarized?.(parsed);
                    break;
                  case "tags_updated":
                    handlers.onTagsUpdated?.(parsed);
                    break;
                  case "error":
                    handlers.onError?.(parsed.message || parsed.error || "Unknown error", parsed.message_id);
                    break;
                  case "heartbeat":
                    break;
                }
              } catch {
                // Ignore parse errors on individual events
              }
            },
            onclose() {
              if (!controller.signal.aborted) {
                removeStreamingSession(sessionId);
              }
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
            },
            onerror(err) {
              if (controller.signal.aborted) {
                setStreaming(false);
                setStreamingSessionId(null);
                handlers.onClose?.();
                return;
              }
              removeStreamingSession(sessionId);
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
              throw err;
            },
          },
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          handlers.onError?.(String(err), "");
          removeStreamingSession(sessionId);
        }
        setStreaming(false);
        setStreamingSessionId(null);
      }
    },
    [],
  );

  const startRegenerateStream = useCallback(
    async (
      sessionId: string,
      messageId: string,
      handlers: {
        onToken?: (token: string, messageId: string) => void;
        onToolStart?: (data: ToolStartEvent["data"]) => void;
        onToolResult?: (data: ToolResultEvent["data"]) => void;
        onMemoryUpdated?: (data: MemoryUpdatedEvent["data"]) => void;
        onStepComplete?: (data: StepCompleteEvent["data"]) => void;
        onMessageComplete?: (data: MessageCompleteEvent["data"]) => void;
        onReasoningToken?: (delta: string, messageId: string) => void;
        onFollowUpQuestions?: (questions: string[]) => void;
        onSummarized?: (data: SummarizedEvent["data"]) => void;
        onTagsUpdated?: (data: TagsUpdatedEvent["data"]) => void;
        onError?: (error: string, messageId: string) => void;
        onClose?: () => void;
      },
    ) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      setStreamingSessionId(sessionId);
      addStreamingSession(sessionId);

      const token = getToken();

      try {
        await fetchEventSource(
          `${BASE_URL}/chat/session/${sessionId}/message/${messageId}/regenerate`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
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
                return;
              }
              throw new Error(
                `Stream failed with status ${response.status}`,
              );
            },
            onmessage(ev) {
              try {
                const parsed = JSON.parse(ev.data);
                switch (ev.event) {
                  case "token":
                    handlers.onToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "tool_start":
                    handlers.onToolStart?.(parsed);
                    break;
                  case "tool_result":
                    handlers.onToolResult?.(parsed);
                    break;
                  case "memory_updated":
                    handlers.onMemoryUpdated?.(parsed);
                    break;
                  case "step_complete":
                    handlers.onStepComplete?.(parsed);
                    break;
                  case "message_complete":
                    handlers.onMessageComplete?.(parsed);
                    break;
                  case "reasoning_token":
                    handlers.onReasoningToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "follow_up_questions":
                    handlers.onFollowUpQuestions?.(parsed.questions || []);
                    break;
                  case "summarized":
                    handlers.onSummarized?.(parsed);
                    break;
                  case "tags_updated":
                    handlers.onTagsUpdated?.(parsed);
                    break;
                  case "error":
                    handlers.onError?.(parsed.message || parsed.error || "Unknown error", parsed.message_id);
                    break;
                  case "heartbeat":
                    break;
                }
              } catch {
                // Ignore parse errors on individual events
              }
            },
            onclose() {
              if (!controller.signal.aborted) {
                removeStreamingSession(sessionId);
              }
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
            },
            onerror(err) {
              if (controller.signal.aborted) {
                setStreaming(false);
                setStreamingSessionId(null);
                handlers.onClose?.();
                return;
              }
              removeStreamingSession(sessionId);
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
              throw err;
            },
          },
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          handlers.onError?.(String(err), "");
          removeStreamingSession(sessionId);
        }
        setStreaming(false);
        setStreamingSessionId(null);
      }
    },
    [],
  );

  const startEditStream = useCallback(
    async (
      sessionId: string,
      messageId: string,
      content: string,
      temperature: number | undefined,
      handlers: {
        onToken?: (token: string, messageId: string) => void;
        onToolStart?: (data: ToolStartEvent["data"]) => void;
        onToolResult?: (data: ToolResultEvent["data"]) => void;
        onMemoryUpdated?: (data: MemoryUpdatedEvent["data"]) => void;
        onStepComplete?: (data: StepCompleteEvent["data"]) => void;
        onMessageComplete?: (data: MessageCompleteEvent["data"]) => void;
        onReasoningToken?: (delta: string, messageId: string) => void;
        onFollowUpQuestions?: (questions: string[]) => void;
        onSummarized?: (data: SummarizedEvent["data"]) => void;
        onError?: (error: string, messageId: string) => void;
        onClose?: () => void;
      },
    ) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      setStreamingSessionId(sessionId);
      addStreamingSession(sessionId);

      const token = getToken();

      try {
        await fetchEventSource(
          `${BASE_URL}/chat/session/${sessionId}/message/${messageId}`,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({
              content,
              ...(temperature !== undefined ? { temperature } : {}),
            }),
            openWhenHidden: true,
            signal: controller.signal,
            async onopen(response) {
              if (
                response.ok &&
                response.headers
                  .get("content-type")
                  ?.includes(EventStreamContentType)
              ) {
                return;
              }
              throw new Error(
                `Stream failed with status ${response.status}`,
              );
            },
            onmessage(ev) {
              try {
                const parsed = JSON.parse(ev.data);
                switch (ev.event) {
                  case "token":
                    handlers.onToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "tool_start":
                    handlers.onToolStart?.(parsed);
                    break;
                  case "tool_result":
                    handlers.onToolResult?.(parsed);
                    break;
                  case "memory_updated":
                    handlers.onMemoryUpdated?.(parsed);
                    break;
                  case "step_complete":
                    handlers.onStepComplete?.(parsed);
                    break;
                  case "message_complete":
                    handlers.onMessageComplete?.(parsed);
                    break;
                  case "reasoning_token":
                    handlers.onReasoningToken?.(parsed.delta, parsed.message_id);
                    break;
                  case "follow_up_questions":
                    handlers.onFollowUpQuestions?.(parsed.questions || []);
                    break;
                  case "summarized":
                    handlers.onSummarized?.(parsed);
                    break;
                  case "error":
                    handlers.onError?.(parsed.message || parsed.error || "Unknown error", parsed.message_id);
                    break;
                  case "heartbeat":
                    break;
                }
              } catch {
                // Ignore parse errors on individual events
              }
            },
            onclose() {
              if (!controller.signal.aborted) {
                removeStreamingSession(sessionId);
              }
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
            },
            onerror(err) {
              if (controller.signal.aborted) {
                setStreaming(false);
                setStreamingSessionId(null);
                handlers.onClose?.();
                return;
              }
              removeStreamingSession(sessionId);
              setStreaming(false);
              setStreamingSessionId(null);
              handlers.onClose?.();
              throw err;
            },
          },
        );
      } catch (err) {
        if (!controller.signal.aborted) {
          handlers.onError?.(String(err), "");
          removeStreamingSession(sessionId);
        }
        setStreaming(false);
        setStreamingSessionId(null);
      }
    },
    [],
  );

  // Issue #455: when the parent component reuses this hook across session
  // navigations (same route, different param), the old fetchEventSource is
  // still alive (the cleanup effect only fires on unmount, not on session
  // change).  This creates TWO connections to the same bridge and stale
  // onclose/onerror callbacks that override the new streaming state.
  //
  // We therefore abort the old SSE connection here.  This is safe: it only
  // closes the LOCAL fetchEventSource — the background agent task on the
  // backend continues running independently.
  //
  // IMPORTANT: Do NOT touch the streaming context here — the sidebar
  // spinner must persist when navigating away from a running session.
  const resetStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setStreaming(false);
  }, []);

  return {
    streaming,
    streamingSessionId,
    resetStream,
    startStream,
    startRegenerateStream,
    startEditStream,
    stopStream,
    startReconnect,
  };
}

export default useStream;
