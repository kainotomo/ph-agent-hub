// =============================================================================
// PH Agent Hub — ChatWindow
// =============================================================================
// Scrollable message list; streaming token accumulation; stop-generation
// button (calls DELETE /chat/session/:id/stream); uses useStream hook;
// renders MessageBubble list.
// =============================================================================

import React, { useRef, useEffect, useState, useCallback, useMemo } from "react";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";
import { Button, Drawer, Grid, Input, Slider, Space, Spin, Empty, Alert, Switch, Tag, Typography, Upload, message, notification } from "antd";
import {
  SendOutlined,
  SettingOutlined,
  StopOutlined,
  DownOutlined,
  PaperClipOutlined,
  RobotOutlined,
  EditOutlined,
  CloseOutlined,
  ToolOutlined,
  DatabaseOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageBubble } from "./MessageBubble";
import { useStream } from "../hooks/useStream";
import {
  listMessages,
  deleteMessage,
  finalizeSession,
  updateAssistantMessage,
  listAlwaysOnTools,
  getStreamStatus,
} from "../services/chat";
import { AutopilotPanel, INITIAL_AUTOPILOT_STATE } from "./AutopilotPanel";
import { getDemoMessages } from "../services/demo";
import { getWidgetMessages } from "../services/widget";
import api, { getToken } from "../../../services/api";
import {
  ModelSelector,
  TemplateSelector,
  SkillSelector,
  PromptLibrary,
  TemporaryChatBadge,
  SessionToolActivation,
  MemoryManager,
} from "./";
import { AUTO_ROUTE_VALUE } from "./ModelSelector";

const { TextArea } = Input;
const { Text } = Typography;
const { useBreakpoint } = Grid;

// ---------------------------------------------------------------------------
// Draft persistence helpers
// ---------------------------------------------------------------------------

const DRAFT_PREFIX = "chat-draft:";
const FILES_PREFIX = "chat-files:";
const DRAFT_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

/** Purge sessionStorage draft entries older than DRAFT_TTL_MS. */
function purgeStaleDrafts(): void {
  try {
    const now = Date.now();
    const toRemove: string[] = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (!key) continue;
      if (key.startsWith(DRAFT_PREFIX) || key.startsWith(FILES_PREFIX)) {
        const value = sessionStorage.getItem(key);
        if (!value) {
          toRemove.push(key);
        }
      }
    }
    // Also check if a companion "timestamp" key exists and is stale
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i);
      if (!key) continue;
      if (key.startsWith(DRAFT_PREFIX) || key.startsWith(FILES_PREFIX)) {
        const tsKey = key + ":ts";
        const ts = sessionStorage.getItem(tsKey);
        if (ts) {
          const age = now - parseInt(ts, 10);
          if (age > DRAFT_TTL_MS) {
            toRemove.push(key);
            toRemove.push(tsKey);
          }
        }
      }
    }
    for (const key of toRemove) {
      sessionStorage.removeItem(key);
    }
  } catch {
    // Ignore
  }
}

/** Write a draft entry with a companion timestamp. */
function writeDraft(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
    sessionStorage.setItem(key + ":ts", String(Date.now()));
  } catch {
    // sessionStorage may be full or unavailable
  }
}

/** Read a draft entry. */
function readDraft(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Remove a draft entry and its companion timestamp. */
function removeDraft(key: string): void {
  try {
    sessionStorage.removeItem(key);
    sessionStorage.removeItem(key + ":ts");
  } catch {
    // Ignore
  }
}

// ---- Run stale-draft cleanup once per session -----------------------------
// Purge on module load to avoid accumulating stale entries across browser
// sessions.  Idempotent — safe to call on every page mount.
purgeStaleDrafts();

// ---------------------------------------------------------------------------
// Pending file info (stored after upload completes)
// ---------------------------------------------------------------------------

interface PendingFile {
  file_id: string;
  original_filename: string;
}

interface ChatWindowProps {
  sessionId: string;
  isTemporary?: boolean;
  selectedModelId?: string;
  selectedTemplateId?: string;
  selectedSkillId?: string;
  temperature?: number | null;
  crossSessionMemoryEnabled?: boolean | null;
  autoRouteEnabled?: boolean;
  autoSelectTools?: boolean;
  embedded?: boolean;
  demo?: boolean;
  widget?: boolean;
  greetingText?: string;
  logoUrl?: string;
  featureFlags?: Record<string, boolean>;
  isPending?: boolean;
  onSessionUpdate?: (data: Record<string, unknown>) => void;
}

export function ChatWindow({
  sessionId,
  isTemporary,
  selectedModelId,
  selectedTemplateId,
  selectedSkillId,
  temperature,
  crossSessionMemoryEnabled = null,
  autoRouteEnabled = false,
  autoSelectTools = true,
  embedded = false,
  demo = false,
  widget = false,
  greetingText = "",
  logoUrl = "",
  featureFlags = {},
  isPending = false,
  onSessionUpdate,
}: ChatWindowProps) {
  // ---- Draft persistence for pending (lazy) sessions --------------------
  // When isPending is true, the session doesn't exist on the backend yet,
  // so draft text and files are stored in sessionStorage to survive
  // accidental navigation away and back.

  const [inputValue, setInputValue] = useState(() => {
    if (isPending) {
      return readDraft(DRAFT_PREFIX + sessionId) || "";
    }
    return "";
  });

  // Persist inputValue to sessionStorage for pending sessions
  useEffect(() => {
    if (isPending) {
      if (inputValue) {
        writeDraft(DRAFT_PREFIX + sessionId, inputValue);
      } else {
        removeDraft(DRAFT_PREFIX + sessionId);
      }
    }
  }, [inputValue, isPending, sessionId]);

  const [streamingContent, setStreamingContent] = useState("");
  const [streamingReasoningContent, setStreamingReasoningContent] = useState("");
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamingTokens, setStreamingTokens] = useState<{ tokens_in: number; tokens_out: number } | null>(null);
  const [thinkingEnabled, setThinkingEnabled] = useState<boolean | null>(null);
  const [localCrossSessionMemory, setLocalCrossSessionMemory] = useState<boolean | null>(crossSessionMemoryEnabled);
  const [sessionTemperature, setSessionTemperature] = useState<number | null>(
    temperature ?? null,
  );
  const [toolEvents, setToolEvents] = useState<Array<{type: string; data: Record<string, unknown>}>>([]);
  const [followUpQuestions, setFollowUpQuestions] = useState<string[]>([]);
  const [finalizing, setFinalizing] = useState(false);

  // ---- Autopilot mode (Issue #446) ----------------------------------------
  const [isAutopilotMode, setIsAutopilotMode] = useState(false);
  const [autopilotState, setAutopilotState] = useState(INITIAL_AUTOPILOT_STATE);

  // ---- Pending session local state (Phase 2 — Issue #329) -------------
  // When the session doesn't exist yet (isPending), we accumulate settings
  // changes locally and submit them as session_data on the first message.
  const [pendModelId, setPendModelId] = useState<string | undefined>(undefined);
  const [pendTemplateId, setPendTemplateId] = useState<string | undefined>(
    undefined,
  );
  const [pendSkillId, setPendSkillId] = useState<string | undefined>(undefined);
  const [pendAutoRoute, setPendAutoRoute] = useState<boolean>(false);
  const [pendAutoSelectTools, setPendAutoSelectTools] =
    useState<boolean>(true);
  const [pendActiveToolIds, setPendActiveToolIds] = useState<string[]>([]);
  const [pendingFlag, setPendingFlag] = useState(isPending);

  // When the session gets created (isPending flips), clear pending state
  // and remove the persisted draft from sessionStorage.
  useEffect(() => {
    if (!isPending) {
      setPendingFlag(false);
      removeDraft(DRAFT_PREFIX + sessionId);
      removeDraft(FILES_PREFIX + sessionId);
    }
  }, [isPending, sessionId]);

  // ---- Initialize pending active tools with always-on tools -------------
  // For pending (lazy) sessions, pre-populate the tool activation list
  // with the user's always-on tools so they appear selected in the UI
  // before the first message is sent.
  const { data: alwaysOnIds } = useQuery({
    queryKey: ["always-on-tools"],
    queryFn: listAlwaysOnTools,
    enabled: pendingFlag,
  });
  const alwaysOnInitialized = useRef(false);
  useEffect(() => {
    if (pendingFlag && alwaysOnIds && alwaysOnIds.length > 0 && !alwaysOnInitialized.current) {
      setPendActiveToolIds(alwaysOnIds);
      alwaysOnInitialized.current = true;
    }
  }, [pendingFlag, alwaysOnIds]);

  // ---- Sync skill tools into pending active tools -----------------------
  // When the user selects a skill in pending mode, fetch the skill's
  // tool IDs and merge them into pendActiveToolIds so they appear
  // activated in the UI and get sent with the first message.
  const prevPendSkillRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!pendingFlag) return;
    const newSkill = pendSkillId;
    const oldSkill = prevPendSkillRef.current;
    if (newSkill && newSkill !== oldSkill) {
      prevPendSkillRef.current = newSkill;
      // Fetch the skill's tool IDs
      api<{ items: { id: string; tool_ids: string[] }[] }>("/skills").then((skills) => {
        const skill = skills.items.find((s) => s.id === newSkill);
        if (skill && skill.tool_ids.length > 0) {
          setPendActiveToolIds((prev) => {
            const merged = new Set([...prev, ...skill.tool_ids]);
            return Array.from(merged);
          });
        }
      }).catch(() => {
        // Silently fail — skill tools are a UX convenience
      });
    }
  }, [pendingFlag, pendSkillId]);

  // Intercept settings changes when pending — store locally instead of
  // calling onSessionUpdate (which would 404 since the session doesn't exist).
  const handleSettingsUpdate = useCallback(
    (data: Record<string, unknown>) => {
      if (pendingFlag) {
        if ("selected_model_id" in data)
          setPendModelId(data.selected_model_id as string | undefined);
        if ("selected_template_id" in data)
          setPendTemplateId(data.selected_template_id as string | undefined);
        if ("selected_skill_id" in data)
          setPendSkillId(data.selected_skill_id as string | undefined);
        if ("auto_route_enabled" in data)
          setPendAutoRoute(data.auto_route_enabled as boolean);
        if ("auto_select_tools" in data)
          setPendAutoSelectTools(data.auto_select_tools as boolean);
        if ("temperature" in data)
          setSessionTemperature(data.temperature as number | null);
        if ("thinking_enabled" in data)
          setThinkingEnabled(data.thinking_enabled as boolean | null);
        if ("cross_session_retrieval_enabled" in data)
          setLocalCrossSessionMemory(
            data.cross_session_retrieval_enabled as boolean | null,
          );
        return;
      }
      onSessionUpdate?.(data);
    },
    [pendingFlag, onSessionUpdate],
  );

  // Model change handler: "Auto" (__auto__) → null model + auto_route_enabled
  const handleModelChange = useCallback(
    (id: string) => {
      if (id === AUTO_ROUTE_VALUE) {
        handleSettingsUpdate({ selected_model_id: null, auto_route_enabled: true });
      } else {
        handleSettingsUpdate({ selected_model_id: id, auto_route_enabled: false });
      }
    },
    [handleSettingsUpdate],
  );
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const prevLoadingRef = useRef(true);
  const queryClient = useQueryClient();
  const screens = useBreakpoint();
  const isMobile = !screens.md;

  // Handle finalizing a temporary session to permanent
  const handleFinalize = useCallback(async () => {
    if (!sessionId) return;
    setFinalizing(true);
    try {
      const permanent = await finalizeSession(sessionId);
      queryClient.setQueryData(["session", sessionId], permanent);
      queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      message.success("Chat saved permanently");
    } catch (err) {
      message.error("Failed to save chat permanently");
    } finally {
      setFinalizing(false);
    }
  }, [sessionId, queryClient]);

  // Invalidate session tools when the selected skill changes (backend syncs
  // active tools on skill change, but the sidebar needs to know to refetch).
  const prevSkillRef = useRef(selectedSkillId);
  useEffect(() => {
    if (prevSkillRef.current !== selectedSkillId) {
      prevSkillRef.current = selectedSkillId;
      queryClient.invalidateQueries({ queryKey: ["session-tools", sessionId] });
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    }
  }, [selectedSkillId, sessionId, queryClient]);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [showScrollButton, setShowScrollButton] = useState(false);

  // Restore pending files from sessionStorage on mount for pending sessions
  useEffect(() => {
    if (isPending) {
      try {
        const stored = readDraft(FILES_PREFIX + sessionId);
        if (stored) {
          const parsed = JSON.parse(stored) as PendingFile[];
          if (Array.isArray(parsed) && parsed.length > 0) {
            setPendingFiles(parsed);
          }
        }
      } catch {
        // Ignore parse errors
      }
    }
  }, [isPending, sessionId]); // mount only

  // Persist pendingFiles to sessionStorage for pending sessions
  useEffect(() => {
    if (isPending) {
      if (pendingFiles.length > 0) {
        writeDraft(FILES_PREFIX + sessionId, JSON.stringify(pendingFiles));
      } else {
        removeDraft(FILES_PREFIX + sessionId);
      }
    }
  }, [pendingFiles, isPending, sessionId]);

  // Optimistic user message — shown immediately on send, replaced by
  // the real persisted message when the response completes or the
  // stream is stopped / errors out.
  const [pendingUserMessage, setPendingUserMessage] = useState<{
    id: string;
    content: string;
  } | null>(null);

  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [regeneratingMsgId, setRegeneratingMsgId] = useState<string | null>(null);
  const [ctaDismissed, setCtaDismissed] = useState(false);

  // Welcome suggestions for first-time demo users
  const DEMO_WELCOME_SUGGESTIONS = [
    "What can you do?",
    "Explain multi-tenancy",
    "Write a Python script",
    "Summarize a document",
  ];

  const { streaming, startStream, startRegenerateStream, startEditStream, stopStream, startReconnect, resetStream } = useStream(
    demo ? "demo" : widget ? "widget" : "chat"
  );

  const { data: messages, isLoading: loadingMessages } = useQuery({
    queryKey: ["messages", sessionId],
    enabled: !pendingFlag,
    queryFn: () =>
      demo
        ? getDemoMessages().then((msgs) =>
            msgs.map((m) => ({
              id: m.id,
              session_id: m.session_id,
              sender: m.role as "user" | "assistant",
              content: [{ type: "text" as const, text: m.content }],
              model_id: null,
              model_name: null,
              model_provider: null,
              tool_calls: null,
              tokens_in: null,
              tokens_out: null,
              is_deleted: false,
              created_at: m.created_at,
              updated_at: m.created_at,
            })),
          )
        : widget
          ? getWidgetMessages().then((msgs) =>
              msgs.map((m) => ({
                id: m.id,
                session_id: m.session_id,
                sender: m.role as "user" | "assistant",
                content: [{ type: "text" as const, text: m.content }],
                model_id: null,
                model_name: null,
                model_provider: null,
                tool_calls: null,
                tokens_in: null,
                tokens_out: null,
                is_deleted: false,
                created_at: m.created_at,
                updated_at: m.created_at,
              })),
            )
          : listMessages(sessionId),
    refetchInterval: false,
  });

  // Count user messages for CTA trigger (demo mode only)
  const userMessageCount = (messages || []).filter((m: any) => m.sender === "user").length;

  // Fetch models to determine if selected model supports thinking
  // (skipped in demo mode to avoid 401 triggering auto-refresh)
  interface ModelInfo {
    id: string;
    name: string;
    thinking_enabled: boolean;
    provider: string;
    context_length?: number | null;
  }
  const { data: modelList } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelInfo[]>("/models"),
    enabled: !demo,
  });
  const selectedModel = useMemo(
    () => (modelList || []).find((m) => m.id === selectedModelId),
    [modelList, selectedModelId],
  );
  const modelSupportsThinking = selectedModel?.thinking_enabled === true;

  // Auto-select the first available model for pending sessions (no backend
  // session to provide a default yet — mirrors backend create_session logic).
  // NOTE: Must NOT fire when the user has explicitly chosen "Auto" mode —
  // that sets pendModelId=null + pendAutoRoute=true.  The check for
  // !pendAutoRoute prevents re-selecting the first model and overriding
  // the user's choice (Issue #400).
  useEffect(() => {
    if (pendingFlag && modelList && modelList.length > 0 && !pendModelId && !pendAutoRoute) {
      setPendModelId(modelList[0].id);
    }
  }, [pendingFlag, modelList, pendModelId, pendAutoRoute]);

  // Reset all streaming state when the session changes (mount with new
  // sessionId or fresh mount). This prevents stale streamingContent,
  // pendingUserMessage, etc. from bleeding into the new session.
  //
  // NOTE: We intentionally do NOT call stopStream(sessionId) in a cleanup
  // effect here.  React StrictMode double-mounts components in dev, so a
  // cleanup-based stopStream would fire DELETE /chat/session/:id/stream on
  // every mount, setting a Redis cancel flag (60 s TTL) that cancels the
  // next message the user sends (Issue #124).  Stream abort on unmount is
  // handled inside useStream.ts, and stale session–switch state is cleared
  // by the state‑reset effect below.
  //
  // Issue #455: Because React reuses the same ChatWindow when navigating
  // between sessions in the same route, the useStream hook's internal
  // streaming state would persist across navigations.  We call
  // resetStream() to clear it without cancelling the background agent.
  useEffect(() => {
    resetStream();
    reconnectAttemptedRef.current = false;
    // Clear stopped-session tracking so a new session can reconnect.
    stoppedSessionsRef.current.clear();
    setStreamingContent("");
    setStreamingReasoningContent("");
    setStreamingMessageId(null);
    setStreamError(null);
    setToolEvents([]);
    setFollowUpQuestions([]);
    setStreamingTokens(null);
    setPendingUserMessage(null);
    setEditingMsgId(null);
    setRegeneratingMsgId(null);
    setSessionTemperature(temperature ?? null);
    setLocalCrossSessionMemory(crossSessionMemoryEnabled ?? null);
    setAutopilotState(INITIAL_AUTOPILOT_STATE);
    setIsAutopilotMode(false);
    queryClient.invalidateQueries({ queryKey: ["memory"] });
  }, [sessionId, temperature, crossSessionMemoryEnabled, queryClient, resetStream]);

  // ---- Issue #455: Reconnect to a running agent on mount -----------------
  // When navigating back to a session where the agent is still running,
  // open a reconnect SSE stream to resume live updates.
  const [reconnecting, setReconnecting] = useState(false);
  const reconnectAttemptedRef = useRef(false);
  const stoppedSessionsRef = useRef(new Set<string>());

  useEffect(() => {
    // Only for normal chat sessions — not demo or widget.
    // Skip reconnect if user explicitly stopped this session (Issue #457).
    if (demo || widget || pendingFlag || !sessionId || reconnectAttemptedRef.current || stoppedSessionsRef.current.has(sessionId)) {
      return;
    }

    let cancelled = false;

    const checkAndReconnect = async () => {
      try {
        const status = await getStreamStatus(sessionId);
        if (cancelled) return;
        if (status.active) {
          setReconnecting(true);
          reconnectAttemptedRef.current = true;
          const reconnectHandlers = buildStreamHandlers('reconnect');
          // If the active stream is an autopilot, pre-set the panel state
          // immediately (before autopilot_turn_start arrives) so the panel
          // shows even if that event already scrolled past the buffer.
          if (status.autopilot) {
            setAutopilotState({
              currentTurn: 1,
              maxTurns: 20,
              status: "executing",
            });
            setIsAutopilotMode(true);
          }
          await startReconnect(sessionId, {
            ...reconnectHandlers,
            onClose: () => {
              if (!cancelled) {
                setReconnecting(false);
                setAutopilotState(INITIAL_AUTOPILOT_STATE);
                setIsAutopilotMode(false);
                reconnectHandlers.onClose();
              }
            },
          });
        }
      } catch {
        // Stream status check failed — session may not exist yet.
        // This is normal for lazy-created sessions.
      } finally {
        if (!cancelled) {
          setReconnecting(false);
        }
      }
    };

    checkAndReconnect();

    return () => {
      cancelled = true;
    };
  }, [sessionId, demo, widget, pendingFlag, startReconnect, queryClient]);

  // Auto-scroll to bottom when messages finish loading (existing session)
  useEffect(() => {
    if (prevLoadingRef.current && !loadingMessages && messages?.length) {
      // Small delay to let Virtuoso layout settle
      setTimeout(() => {
        virtuosoRef.current?.scrollToIndex({ index: "LAST", behavior: "auto" });
      }, 50);
    }
    prevLoadingRef.current = loadingMessages;
  }, [loadingMessages, messages]);

  // Clear editing state once the edited message is confirmed gone from the list
  useEffect(() => {
    if (editingMsgId && messages) {
      const stillExists = messages.some((m: any) => m.id === editingMsgId);
      if (!stillExists) {
        setEditingMsgId(null);
      }
    }
  }, [messages, editingMsgId]);

  // In demo mode, when persisted messages arrive from Redis after a stream
  // completes, clear the streaming bubble so the real messages show instead.
  const prevMessagesLenRef = useRef(0);
  useEffect(() => {
    if (demo && messages && messages.length > 0 && messages.length > prevMessagesLenRef.current && streamingMessageId) {
      setStreamingContent("");
      setStreamingReasoningContent("");
      setStreamingMessageId(null);
    }
    prevMessagesLenRef.current = messages?.length ?? 0;
  }, [demo, messages, streamingMessageId]);

  // ---- Fetch follow-up questions after stream closes (Issue #126) -----------
  // The backend now generates follow-up questions in a background task so
  // the SSE stream can close immediately after message_complete.  This
  // helper polls the follow-up endpoint once after a short delay.
  const fetchFollowUpQuestions = useCallback(
    (sid: string, setter: (questions: string[]) => void) => {
      const BASE_URL = import.meta.env.VITE_API_URL || "/api";
      const endpoint = demo
        ? `${BASE_URL}/demo/session/follow-up-questions`
        : widget
          ? `${BASE_URL}/widget/session/follow-up-questions`
          : `${BASE_URL}/chat/session/${sid}/follow-up-questions`;
      setTimeout(async () => {
        try {
          const token = getToken();
          const res = await fetch(
            endpoint,
            { headers: token ? { Authorization: `Bearer ${token}` } : {} },
          );
          if (res.ok) {
            const data = await res.json();
            if (data.questions && data.questions.length > 0) {
              setter(data.questions);
            }
          }
        } catch {
          // Follow-up questions are optional — silently ignore failures
        }
      }, 1500);
    },
    [demo],
  );

  // ---- Shared stream handler factory (Issue #457) ---------------------------
  // Builds the handler object passed to startStream, startReconnect,
  // startRegenerateStream, or startEditStream.  Keeps 4 nearly-identical
  // handler sets in sync without code duplication.
  const buildStreamHandlers = useCallback(
    (mode: 'send' | 'reconnect' | 'regenerate' | 'edit' | 'autopilot') => {
      const isReconnect = mode === 'reconnect';
      const isRegenerate = mode === 'regenerate';
      const isEdit = mode === 'edit';
      const isSend = mode === 'send';
      const isAutopilot = mode === 'autopilot';

      return {
        onToken: (token: string, msgId: string) => {
          setStreamingMessageId((prev) => prev ?? msgId);
          setStreamingContent((prev) => prev + token);
        },
        onReasoningToken: (delta: string, msgId: string) => {
          setStreamingMessageId((prev) => prev ?? msgId);
          setStreamingReasoningContent((prev) => prev + delta);
        },
        onToolStart: (data: Record<string, unknown>) => {
          setToolEvents((prev) => [...prev, { type: "function_call", data }]);
        },
        onToolResult: (data: Record<string, unknown>) => {
          setToolEvents((prev) => [...prev, { type: "function_result", data }]);
        },
        onStepComplete: () => { /* No UI update needed */ },
        onFollowUpQuestions: (questions: string[]) => {
          setFollowUpQuestions(questions);
        },
        onTagsUpdated: () => {
          queryClient.invalidateQueries({ queryKey: ["sessions"] });
        },
        onMemoryUpdated: isReconnect
          ? () => { /* Sidebar auto-refreshes on session invalidation */ }
          : (data: { action: string }) => {
              queryClient.invalidateQueries({ queryKey: ["memory", sessionId] });
              notification.info({
                message: data.action === "saved" ? "Information saved" : "Memory deleted",
                description: data.action === "saved"
                  ? "I'll remember this for next time."
                  : "Memory entry has been removed.",
                placement: "bottomRight",
                duration: 4,
              });
            },
        // ---- Autopilot event handlers -----------------------------------
        onAutopilotTurnStart: (data: { turn: number; max_turns: number }) => {
          setAutopilotState((prev) => ({
            ...prev,
            currentTurn: data.turn,
            maxTurns: data.max_turns,
            status: "executing" as const,
          }));
          setIsAutopilotMode(true);
        },
        onAutopilotTurnComplete: (_data: { turn: number; max_turns: number }) => {
          // Progress updated on next turn start; nothing extra needed.
        },
        onAutopilotComplete: (data: { summary: string; turn: number }) => {
          setAutopilotState((prev) => ({
            ...prev,
            status: "complete" as const,
            summary: data.summary,
          }));
        },
        onAutopilotMaxTurns: (_data: { max_turns: number }) => {
          setAutopilotState((prev) => ({
            ...prev,
            status: "max_turns" as const,
          }));
        },
        // ------------------------------------------------------------------
        onSummarized: isReconnect
          ? () => { /* No notification during reconnect */ }
          : (data: { summarized_message_count: number }) => {
              notification.info({
                message: "Conversation Summarized",
                description: `Compressed ${data.summarized_message_count} earlier messages to save context space.`,
                placement: "topRight",
                duration: 4,
              });
            },
        onMessageComplete: (data: {
          message_id?: string;
          tokens_in?: number;
          tokens_out?: number;
        }) => {
          if (isRegenerate) setRegeneratingMsgId(null);
          if (isSend || isEdit || isAutopilot) setPendingUserMessage(null);
          if (!isAutopilot) setToolEvents([]);
          if (data.tokens_in || data.tokens_out) {
            setStreamingTokens({
              tokens_in: data.tokens_in || 0,
              tokens_out: data.tokens_out || 0,
            });
          }
          if (isSend && !demo) {
            // Chat mode: clear streaming state and refetch from API.
            setStreamingContent("");
            setStreamingReasoningContent("");
            setStreamingMessageId(null);
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
            queryClient.invalidateQueries({ queryKey: ["sessionContext", sessionId] });
          } else if (isSend && demo) {
            // Demo mode: keep streamingMessageId so the streaming
            // content bubble remains visible as the final message.
            setStreamingMessageId(data.message_id || "demo-response");
          } else if (isReconnect) {
            // Reconnect: update tokens without clearing streaming state;
            // onClose handles the final cleanup.
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
          } else if (isAutopilot) {
            // Autopilot: don't clear streaming state between turns —
            // the autopilot_turn_start/complete events handle the UI.
            // Just invalidate queries so persisted per-turn messages appear.
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
          } else {
            // Regenerate / Edit: clear streaming state and refetch.
            setStreamingContent("");
            setStreamingReasoningContent("");
            setStreamingMessageId(null);
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
            queryClient.invalidateQueries({ queryKey: ["sessionContext", sessionId] });
          }
        },
        onError: (err: string) => {
          if (isRegenerate) setRegeneratingMsgId(null);
          if (isEdit) setEditingMsgId(null);
          if (isSend || isEdit) setPendingUserMessage(null);
          setStreamingTokens(null);
          setStreamError(err);
          if (!isReconnect) {
            message.error(err || (isEdit ? "Edit failed" : isRegenerate ? "Regenerate failed" : "Request failed"));
          } else {
            console.error("Reconnect stream error:", err);
          }
        },
        onClose: () => {
          if (isRegenerate) setRegeneratingMsgId(null);
          if (isEdit) setEditingMsgId(null);
          if (isSend || isEdit) setPendingUserMessage(null);
          setStreamingContent("");
          setStreamingReasoningContent("");
          setStreamingMessageId(null);
          setToolEvents([]);
          setStreamingTokens(null);
          if (isAutopilot) {
            // Autopilot: onClose fires after all turns complete.
            // Reset autopilot state so the panel disappears and messages
            // refetch shows the final result.
            setAutopilotState(INITIAL_AUTOPILOT_STATE);
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
            queryClient.invalidateQueries({ queryKey: ["sessionContext", sessionId] });
          } else if (!demo) {
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
            queryClient.invalidateQueries({ queryKey: ["sessionContext", sessionId] });
          } else {
            queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
          }
          if (!isReconnect) {
            fetchFollowUpQuestions(sessionId, setFollowUpQuestions);
            // Re-fetch sessions after a delay so auto-generated tags appear
            setTimeout(() => {
              queryClient.invalidateQueries({ queryKey: ["sessions"] });
            }, 3000);
          }
        },
      };
    },
    [
      sessionId, queryClient, demo,
      setStreamingContent, setStreamingReasoningContent, setStreamingMessageId,
      setStreamError, setToolEvents, setFollowUpQuestions, setStreamingTokens,
      setPendingUserMessage, setRegeneratingMsgId, setEditingMsgId,
      fetchFollowUpQuestions,
    ],
  );

  const handleSend = useCallback(async () => {
    if (!inputValue.trim() || streaming) return;
    const content = inputValue.trim();
    setInputValue("");
    setStreamingContent("");
    setStreamingReasoningContent("");
    setStreamError(null);
    setToolEvents([]);
    setFollowUpQuestions([]);
    setStreamingTokens(null);

    // ---- Edit mode: streaming, like regenerate but on the user message ----
    if (editingMsgId) {
      const msgId = editingMsgId;

      // Show the new user message + thinking dots, same as regenerate.
      // Keep editingMsgId set so the old message stays hidden during streaming.
      setPendingUserMessage({
        id: `pending-edit-${Date.now()}`,
        content,
      });

      startEditStream(sessionId, msgId, content, sessionTemperature ?? undefined, {
        ...buildStreamHandlers('edit'),
        onMessageComplete(data) {
          // Don't clear editingMsgId here — the refetch hasn't completed yet.
          // It gets cleared by the useEffect below when messages update.
          setPendingUserMessage(null);
          setStreamingContent("");
          setStreamingReasoningContent("");
          setStreamingMessageId(null);
          setToolEvents([]);
          if (data.tokens_in || data.tokens_out) {
            setStreamingTokens({ tokens_in: data.tokens_in || 0, tokens_out: data.tokens_out || 0 });
          }
          queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
          queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
          queryClient.invalidateQueries({ queryKey: ["sessions"] });
          queryClient.invalidateQueries({ queryKey: ["sessionContext", sessionId] });
        },
      });
      return;
    }

    // ---- Normal send mode ----
    // Optimistic: show the user message immediately
    setPendingUserMessage({
      id: `pending-user-${Date.now()}`,
      content,
    });

    const fileIds = pendingFiles.map((f) => f.file_id);
    setPendingFiles([]);

    // Build session_data for lazy session creation (Phase 2)
    const sessionData = pendingFlag
      ? {
          title: "New Chat",
          auto_route_enabled: pendAutoRoute,
          auto_select_tools: pendAutoSelectTools,
          selected_model_id: pendModelId || null,
          selected_template_id: pendTemplateId || null,
          selected_skill_id: pendSkillId || null,
          thinking_enabled: thinkingEnabled,
          temperature: sessionTemperature,
          active_tool_ids: pendActiveToolIds.length > 0 ? pendActiveToolIds : null,
        }
      : undefined;

    if (isAutopilotMode) {
      // ---- Autopilot mode: stream with autopilot flag ----------------------
      setAutopilotState({
        currentTurn: 0,
        maxTurns: 20,
        status: "executing",
      });
      startStream(
        sessionId,
        content,
        fileIds.length > 0 ? fileIds : undefined,
        sessionTemperature ?? undefined,
        { ...(sessionData || {}), autopilot: true },
        buildStreamHandlers('autopilot'),
        true,
      );
    } else {
      startStream(
        sessionId,
        content,
        fileIds.length > 0 ? fileIds : undefined,
        sessionTemperature ?? undefined,
        sessionData,
        buildStreamHandlers('send'),
      );
    }
  }, [inputValue, streaming, sessionId, startStream, queryClient, pendingFiles, editingMsgId, pendingFlag, pendModelId, pendTemplateId, pendSkillId, pendAutoRoute, pendAutoSelectTools, pendActiveToolIds, thinkingEnabled, sessionTemperature, isAutopilotMode, setAutopilotState]);

  const handleStop = async () => {
    // Clear the streaming ghost bubble immediately for instant UX.
    // The backend will persist the partial response (stopStream sends
    // the cancel signal first), and when the stream ends naturally the
    // onClose handler refetches messages → the partial response becomes
    // a permanent message bubble.
    setStreamingContent("");
    setStreamingReasoningContent("");
    setStreamingMessageId(null);
    setStreamingTokens(null);
    setToolEvents([]);
    // Track that this session was explicitly stopped so reconnecting
    // won't attempt to rejoin a cancelled agent (Issue #457).
    if (sessionId) {
      stoppedSessionsRef.current.add(sessionId);
    }
    setAutopilotState(INITIAL_AUTOPILOT_STATE);
    await stopStream(sessionId);
  };

  const handleEdit = useCallback((messageId: string) => {
    const msg = (messages || []).find((m) => m.id === messageId);
    if (msg) {
      const text = parseTextFromContent(msg.content);
      setInputValue(text);
      setEditingMsgId(messageId);
    }
  }, [messages]);

  const handleCancelEdit = useCallback(() => {
    setEditingMsgId(null);
    setInputValue("");
  }, []);

  const handleEditAssistant = useCallback(async (messageId: string, newContent: string) => {
    await updateAssistantMessage(sessionId, messageId, newContent);
    queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
  }, [sessionId, queryClient]);

  const handleDelete = useCallback(async (messageId: string) => {
    await deleteMessage(sessionId, messageId);
    queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
  }, [sessionId, queryClient]);

  const handleRegenerate = useCallback((messageId: string) => {
    if (streaming) return;
    setRegeneratingMsgId(messageId);
    setStreamingContent("");
    setStreamingReasoningContent("");
    setStreamError(null);
    setToolEvents([]);
    setFollowUpQuestions([]);
    setStreamingTokens(null);

    startRegenerateStream(sessionId, messageId, buildStreamHandlers('regenerate'));
  }, [streaming, sessionId, startRegenerateStream, queryClient]);

  // File upload handlers
  const handleFileUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      try {
        const formData = new FormData();
        formData.append("file", file);
        const endpoint = demo
          ? `/demo/session/upload`
          : `/chat/session/${sessionId}/upload`;
        const res = await api<{
          file_id: string;
          original_filename: string;
        }>(endpoint, {
          method: "POST",
          body: formData,
        });
        setPendingFiles((prev) => [
          ...prev,
          {
            file_id: res.file_id,
            original_filename: res.original_filename,
          },
        ]);
        message.success(`${file.name} attached`);
      } catch {
        message.error(`Failed to upload ${file.name}`);
      } finally {
        setUploading(false);
      }
      return false; // Prevent default Upload behavior
    },
    [sessionId, demo],
  );

  const handleRemoveFile = useCallback((fileId: string) => {
    setPendingFiles((prev) => prev.filter((f) => f.file_id !== fileId));
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !isMobile) {
      e.preventDefault();
      handleSend();
    }
  };

  // ---- Flat message list (linear, no branching) ----
  const displayMessages: Array<any> = (messages || []).filter(
    (m) => m.id !== regeneratingMsgId && m.id !== editingMsgId,
  );

  // Show the user's message immediately at the bottom (optimistic UI)
  if (pendingUserMessage) {
    displayMessages.push({
      id: pendingUserMessage.id,
      session_id: sessionId,
      sender: "user" as const,
      content: [{ type: "text", text: pendingUserMessage.content }],
      model_id: null,
      tool_calls: null,
      is_deleted: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }
  if ((streamingContent || streamingReasoningContent) && streamingMessageId) {
    displayMessages.push({
      id: streamingMessageId,
      session_id: sessionId,
      sender: "assistant" as const,
      content: [
        ...(streamingReasoningContent
          ? [{ type: "reasoning", text: streamingReasoningContent }]
          : []),
        ...(streamingContent
          ? [{ type: "text", text: streamingContent }]
          : []),
        ...toolEvents.map((ev) => ({
          type: ev.type,
          name: (ev.data as Record<string, unknown>).tool_name,
          arguments: (ev.data as Record<string, unknown>).arguments,
          output: (ev.data as Record<string, unknown>).result_summary,
          is_error: !(ev.data as Record<string, unknown>).success,
          call_id: (ev.data as Record<string, unknown>).tool_call_id,
          batch_id: (ev.data as Record<string, unknown>).batch_id,
        })),
      ],
      model_id: selectedModelId || null,
      model_name: selectedModel?.name || null,
      model_provider: selectedModel?.provider || null,
      tool_calls: null,
      tokens_in: streamingTokens?.tokens_in ?? null,
      tokens_out: streamingTokens?.tokens_out ?? null,
      is_deleted: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    });
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#fff",
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      onDrop={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const files = Array.from(e.dataTransfer.files);
        for (const file of files) {
          await handleFileUpload(file);
        }
      }}
    >
      <style>{`
        @keyframes thinkingDot {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
          40% { transform: scale(1); opacity: 1; }
        }
      `}</style>
      {/* Top bar */}
      {embedded ? (
        <div
          style={{
            padding: "4px 12px",
            borderBottom: "1px solid #f0f0f0",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}
        >
          <Text strong style={{ fontSize: 14 }}>
            Chat
          </Text>
        </div>
      ) : isMobile ? (
        <div
          style={{
            padding: "8px 16px 8px 56px",
            borderBottom: "1px solid #f0f0f0",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {isTemporary !== undefined && (
            <TemporaryChatBadge
              isTemporary={isTemporary}
              onFinalize={handleFinalize}
              loading={finalizing}
            />
          )}
          <Button
            size="small"
            icon={<SettingOutlined />}
            onClick={() => setSettingsOpen(true)}
          >
            Options
          </Button>
        </div>
      ) : (
        <div
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid #f0f0f0",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          {isTemporary !== undefined && (
            <TemporaryChatBadge
              isTemporary={isTemporary}
              onFinalize={handleFinalize}
              loading={finalizing}
            />
          )}
          <ModelSelector
            value={pendingFlag
              ? (pendAutoRoute && !pendModelId ? AUTO_ROUTE_VALUE : pendModelId)
              : (autoRouteEnabled && !selectedModelId ? AUTO_ROUTE_VALUE : selectedModelId)
            }
            onChange={handleModelChange}
          />
          <TemplateSelector
            value={pendingFlag ? pendTemplateId : selectedTemplateId}
            onChange={(id) => handleSettingsUpdate({ selected_template_id: id })}
          />
          <SkillSelector
            value={pendingFlag ? pendSkillId : selectedSkillId}
            onChange={(id) => handleSettingsUpdate({ selected_skill_id: id })}
          />
          <PromptLibrary
            onUse={(resolvedText) => setInputValue(resolvedText)}
          />
          <Button
            size="small"
            onClick={() => setToolsOpen(true)}
          >
            Tools
          </Button>
          <Switch
            size="small"
            checked={localCrossSessionMemory ?? false}
            checkedChildren="🧠 Memory"
            unCheckedChildren="🧠 Memory"
            title="Cross-session memory"
            onChange={(v) => {
              setLocalCrossSessionMemory(v);
              handleSettingsUpdate({ cross_session_retrieval_enabled: v });
            }}
          />
          {modelSupportsThinking && (
            <Switch
              size="small"
              checked={thinkingEnabled ?? true}
              checkedChildren="🧠"
              unCheckedChildren="🧠"
              title="Thinking Mode"
              onChange={(v) => {
                setThinkingEnabled(v);
                handleSettingsUpdate({ thinking_enabled: v });
              }}
            />
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 120 }}>
            <Text type="secondary" style={{ fontSize: 11, whiteSpace: "nowrap" }}>
              Temperature
            </Text>
            <Slider
              min={0}
              max={2}
              step={0.1}
              value={sessionTemperature ?? 0.7}
              onChange={(v) => {
                const val = v as number;
                setSessionTemperature(val);
                handleSettingsUpdate({ temperature: val });
              }}
              style={{ width: 80, margin: 0 }}
            />
          </div>
        </div>
      )}
      {!embedded && (
        <Drawer
          placement="bottom"
          title="Chat Options"
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          height="auto"
          styles={{ body: { paddingBottom: 32 } }}
        >
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <ModelSelector
            value={pendingFlag
              ? (pendAutoRoute && !pendModelId ? AUTO_ROUTE_VALUE : pendModelId)
              : (autoRouteEnabled && !selectedModelId ? AUTO_ROUTE_VALUE : selectedModelId)
            }
            onChange={handleModelChange}
          />
          <TemplateSelector
            value={pendingFlag ? pendTemplateId : selectedTemplateId}
            onChange={(id) => handleSettingsUpdate({ selected_template_id: id })}
          />
          <SkillSelector
            value={pendingFlag ? pendSkillId : selectedSkillId}
            onChange={(id) => handleSettingsUpdate({ selected_skill_id: id })}
          />
          <PromptLibrary
            onUse={(resolvedText) => setInputValue(resolvedText)}
          />
          <Button
            icon={<DatabaseOutlined />}
            onClick={() => {
              setSettingsOpen(false);
              setMemoryOpen(true);
            }}
          >
            Memories
          </Button>
          <Button
            icon={<ToolOutlined />}
            onClick={() => {
              setSettingsOpen(false);
              setToolsOpen(true);
            }}
          >
            Tools
          </Button>
          {modelSupportsThinking && (
            <Switch
              size="small"
              checked={thinkingEnabled ?? true}
              checkedChildren="🧠"
              unCheckedChildren="🧠"
              title="Thinking Mode"
              onChange={(v) => {
                setThinkingEnabled(v);
                handleSettingsUpdate({ thinking_enabled: v });
              }}
            />
          )}
          <Switch
            size="small"
            checked={localCrossSessionMemory ?? false}
            checkedChildren="🧠 Memory"
            unCheckedChildren="🧠 Memory"
            title="Cross-session memory"
            onChange={(v) => {
              setLocalCrossSessionMemory(v);
              handleSettingsUpdate({ cross_session_retrieval_enabled: v });
            }}
          />
          <div style={{ width: "100%" }}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Temperature
              </Text>
              <Slider
                min={0}
                max={2}
                step={0.1}
                value={sessionTemperature ?? 0.7}
                onChange={(v) => {
                  const val = v as number;
                  setSessionTemperature(val);
                  handleSettingsUpdate({ temperature: val });
                }}
                marks={{ 0: "0", 1: "1", 2: "2" }}
              />
            </Space>
          </div>
        </div>
      </Drawer>
      )}

      {!embedded && (
        <MemoryManager
          open={memoryOpen}
          onClose={() => setMemoryOpen(false)}
          sessionId={sessionId}
        />
      )}

      {/* Messages area */}
      <div style={{ position: "relative", flex: 1 }}>
        <Virtuoso
          ref={virtuosoRef}
          data={displayMessages}
          followOutput="smooth"
          atBottomThreshold={80}
          atBottomStateChange={(atBottom) => setShowScrollButton(!atBottom)}
          style={{ height: "100%" }}
          itemContent={(_index, msg) => (
            <div style={{ padding: "0 16px" }}>
              <MessageBubble
                key={msg.id}
                message={msg}
                sessionId={sessionId}
                showFeedback={!embedded && !demo}
                onEdit={
                  msg.sender === "user" && !isTemporary
                    ? handleEdit
                    : undefined
                }
                onEditAssistant={
                  msg.sender === "assistant" && !isTemporary
                    ? handleEditAssistant
                    : undefined
                }
                hasSubsequentMessages={
                  messages
                    ? messages.some(
                        (m: any) =>
                          m.created_at > msg.created_at &&
                          m.id !== regeneratingMsgId &&
                          m.id !== editingMsgId,
                      )
                    : false
                }
                onDelete={
                  !isTemporary
                    ? handleDelete
                    : undefined
                }
                onRegenerate={
                  msg.sender === "assistant" && !isTemporary && messages
                    ? (() => {
                        const assistants = messages.filter(
                          (m: any) => m.sender === "assistant" && !m.is_deleted,
                        );
                        return assistants[assistants.length - 1]?.id === msg.id
                          ? handleRegenerate
                          : undefined;
                      })()
                    : undefined
                }
                disabled={streaming}
                regenerating={regeneratingMsgId === msg.id}
                streaming={msg.id === streamingMessageId}
              />
            </div>
          )}
          components={{
            Header: () => (
              <>
                {reconnecting && (
                  <div style={{ padding: "0 16px" }}>
                    <Alert
                      message="Reconnecting to live session…"
                      type="info"
                      icon={<Spin size="small" />}
                      showIcon
                      style={{ marginBottom: 8, borderLeft: "4px solid #1677ff" }}
                    />
                  </div>
                )}
                {streamError ? (
                  <div style={{ padding: "0 16px" }}>
                    <Alert
                      message="Error"
                      description={streamError}
                      type="error"
                      closable
                      onClose={() => setStreamError(null)}
                      style={{ marginBottom: 12 }}
                    />
                  </div>
                ) : null}
              </>
            ),
            EmptyPlaceholder: () =>
              loadingMessages ? (
                <div style={{ textAlign: "center", padding: 48 }}>
                  <Spin />
                </div>
              ) : demo || widget ? (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: "48px 32px",
                    textAlign: "center",
                  }}
                >
                  {logoUrl ? (
                    <img
                      src={logoUrl}
                      alt="Logo"
                      style={{
                        maxWidth: 120,
                        maxHeight: 48,
                        marginBottom: 16,
                        objectFit: "contain",
                      }}
                    />
                  ) : (
                    <div style={{ fontSize: 48, marginBottom: 16 }}>
                      👋
                    </div>
                  )}
                  <Text strong style={{ fontSize: 18, marginBottom: 8 }}>
                    {greetingText || (demo ? "I'm an AI assistant. Try asking me:" : "Start a conversation!")}
                  </Text>
                  {demo && (
                    <div
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: 8,
                        marginTop: 12,
                        justifyContent: "center",
                      }}
                    >
                      {DEMO_WELCOME_SUGGESTIONS.map((q) => (
                        <Button
                          key={q}
                          size="small"
                          type="default"
                          style={{
                            borderRadius: 16,
                            maxWidth: "100%",
                            whiteSpace: "normal",
                            height: "auto",
                            padding: "4px 12px",
                            textAlign: "left",
                          }}
                          onClick={() => {
                            setInputValue(q);
                            setTimeout(() => {
                              const textarea = document.querySelector(
                                `[data-session-id="${sessionId}"] textarea, #chat-input-${sessionId}`
                              ) as HTMLTextAreaElement;
                              if (textarea) {
                                textarea.focus();
                              }
                            }, 0);
                          }}
                        >
                          {q}
                        </Button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <Empty
                  description="No messages yet. Start a conversation!"
                  style={{ marginTop: 64 }}
                />
              ),
            Footer: () => (
              <>
                {/* Thinking placeholder — shown while streaming but before any content or reasoning */}
              {streaming && !streamingContent && !streamingReasoningContent && (
                <div style={{ padding: "0 16px", marginBottom: 16 }}>
                  <div style={{ display: "flex", justifyContent: "flex-start", marginBottom: 2 }}>
                    <Space style={{ marginLeft: 4 }} size={2}>
                      <RobotOutlined style={{ color: "#52c41a", fontSize: 11 }} />
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        Assistant
                      </Text>
                    </Space>
                  </div>
                  <div
                    style={{
                      padding: "8px 0",
                    }}
                  >
                    <Space size={4}>
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          style={{
                            display: "inline-block",
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            background: "#bbb",
                            animation: `thinkingDot 1.4s ease-in-out ${i * 0.2}s infinite`,
                          }}
                        />
                      ))}
                      <Text type="secondary" style={{ fontSize: 13, marginLeft: 4 }}>
                        AI is thinking…
                      </Text>
                    </Space>
                  </div>
                </div>
              )}

              {/* Follow-up questions chips */}
              {!streaming && followUpQuestions.length > 0 && (
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 8,
                    padding: "0 16px 12px",
                    justifyContent: "flex-start",
                  }}
                >
                  {(featureFlags.follow_up_questions ?? true) && followUpQuestions.map((q, i) => (
                    <Button
                      key={i}
                      size="small"
                      type="default"
                      style={{
                        borderRadius: 16,
                        maxWidth: "100%",
                        whiteSpace: "normal",
                        height: "auto",
                        padding: "4px 12px",
                        textAlign: "left",
                      }}
                      onClick={() => {
                        setInputValue(q);
                        setFollowUpQuestions([]);
                        setTimeout(() => {
                          const textarea = document.querySelector(
                            `[data-session-id="${sessionId}"] textarea, #chat-input-${sessionId}`
                          ) as HTMLTextAreaElement;
                          if (textarea) {
                            textarea.focus();
                          }
                        }, 0);
                      }}
                    >
                      {q}
                    </Button>
                  ))}
                </div>
              )}
            </>
          ),
        }}
      />
      {/* Scroll-to-bottom floating button — rendered outside Virtuoso's scroll container */}
      {showScrollButton && (
        <Button
          shape="circle"
          size="small"
          icon={<DownOutlined />}
          onClick={() => {
            virtuosoRef.current?.scrollToIndex({
              index: "LAST",
              behavior: "smooth",
            });
          }}
          style={{
            position: "absolute",
            bottom: 16,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 10,
            boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
          }}
          title="Scroll to bottom"
        />
      )}
      </div>

      {/* Autopilot progress panel — outside Virtuoso so it's always visible (Issue #446) */}
      <AutopilotPanel
        state={autopilotState}
        onStop={handleStop}
        sessionId={sessionId}
      />

      {/* Demo CTA banner — shown after 3+ user messages */}
      {demo && userMessageCount >= 3 && !ctaDismissed && (
        <Alert
          message={
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
              }}
            >
              <span>
                🤖 Like what you see?{" "}
                <strong>Deploy your own instance →</strong>
              </span>
              <Button
                type="primary"
                size="small"
                href="https://github.com/kainotomo/ph-agent-hub"
                target="_blank"
                rel="noopener noreferrer"
              >
                Get Started on GitHub
              </Button>
            </div>
          }
          type="info"
          closable
          onClose={() => setCtaDismissed(true)}
          style={{
            margin: "0 16px 0",
            borderLeft: "4px solid #1677ff",
          }}
        />
      )}

      {/* Input area */}
      <div
        style={{
          padding: "12px 16px",
          paddingBottom: "calc(12px + env(safe-area-inset-bottom, 0px))",
          borderTop: "1px solid #f0f0f0",
        }}
      >
        {/* Pending file chips */}
        {pendingFiles.length > 0 && (
          <div style={{ marginBottom: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
            {pendingFiles.map((f) => (
              <Tag
                key={f.file_id}
                closable
                onClose={() => handleRemoveFile(f.file_id)}
                color="blue"
              >
                {f.original_filename}
              </Tag>
            ))}
          </div>
        )}

        {/* Edit mode indicator */}
        {editingMsgId && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 8,
              padding: "4px 12px",
              background: "#fff7e6",
              border: "1px solid #ffd591",
              borderRadius: 6,
            }}
          >
            <EditOutlined style={{ color: "#fa8c16" }} />
            <Text type="secondary" style={{ fontSize: 13, flex: 1 }}>
              Editing message — a new branch will be created
            </Text>
            <Button
              type="text"
              size="small"
              icon={<CloseOutlined />}
              onClick={handleCancelEdit}
            />
          </div>
        )}

        <div style={{ display: "flex", alignItems: "flex-end", gap: 8, width: "100%" }}>
          {/* Autopilot mode toggle (Issue #446) — only for normal chat */}
          {!demo && !widget && !editingMsgId && !isTemporary && (
            <Space direction="vertical" size={0} style={{ alignItems: "center" }}>
              <Button
                size="small"
                type={isAutopilotMode ? "primary" : "default"}
                onClick={() => setIsAutopilotMode(!isAutopilotMode)}
                title={isAutopilotMode ? "Switch to Chat mode" : "Switch to Autopilot mode"}
                style={{
                  fontSize: 11,
                  padding: "0 6px",
                  height: 22,
                  lineHeight: "20px",
                }}
              >
                {isAutopilotMode ? "🤖 AP" : "💬"}
              </Button>
              <Text style={{ fontSize: 9, lineHeight: "12px", color: "#888" }}>
                {isAutopilotMode ? "Auto" : "Chat"}
              </Text>
            </Space>
          )}
          {(featureFlags.file_upload ?? true) && (
            <Upload
              multiple
              showUploadList={false}
              beforeUpload={async (file) => {
                await handleFileUpload(file);
                return false;
              }}
              disabled={streaming || !!editingMsgId}
              accept={
                ".pdf,.csv,.txt,.md,.json,.png,.jpg,.jpeg,.gif,.webp,.xlsx,.docx,.pptx," +
                "application/pdf,text/csv,text/plain,text/markdown," +
                "application/json,image/png,image/jpeg,image/gif,image/webp," +
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet," +
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document," +
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
              }
            >
              <Button
                icon={<PaperClipOutlined />}
                disabled={streaming || !!editingMsgId}
                loading={uploading}
                title="Attach files"
              />
            </Upload>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <TextArea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                editingMsgId
                  ? "Edit message…"
                  : isAutopilotMode
                  ? "Describe your goal… (autopilot)"
                  : isTemporary
                  ? "Type a message…"
                  : isMobile
                  ? "Type a message…"
                  : "Type a message… (Enter to send, Shift+Enter for new line)"
              }
              autoSize={{ minRows: 1, maxRows: 6 }}
              disabled={streaming}
              style={{ resize: "none", width: "100%" }}
            />
          </div>
          {streaming ? (
            <Space size={4}>
              {streaming ? (
                <Button
                  danger
                  icon={<StopOutlined />}
                  onClick={handleStop}
                >
                  Stop
                </Button>
              ) : (
                <Button
                  type="primary"
                  loading
                  disabled
                >
                  Sending…
                </Button>
              )}
              <Spin size="small" />
            </Space>
          ) : (
            <Button
              type="primary"
              icon={editingMsgId ? <EditOutlined /> : isAutopilotMode ? <RobotOutlined /> : <SendOutlined />}
              onClick={handleSend}
              disabled={!inputValue.trim()}
            >
              {editingMsgId ? "Edit & Send" : isAutopilotMode ? "Start Autopilot" : "Send"}
            </Button>
          )}
        </div>
      </div>

      {/* Tools drawer */}
      {!embedded && (
        <SessionToolActivation
          sessionId={sessionId}
          open={toolsOpen}
          onClose={() => setToolsOpen(false)}
          selectedSkillId={selectedSkillId}
          autoSelectTools={pendingFlag ? pendAutoSelectTools : autoSelectTools}
          onAutoSelectToolsChange={(v) => handleSettingsUpdate({ auto_select_tools: v })}
          isPending={pendingFlag}
          pendingActiveToolIds={pendActiveToolIds}
          onPendingToolToggle={(toolId, checked) => {
            setPendActiveToolIds((prev) =>
              checked
                ? [...prev, toolId]
                : prev.filter((id) => id !== toolId)
            );
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function parseTextFromContent(content: unknown): string {
  if (!content) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((c) => c && typeof c === "object" && c.type === "text")
      .map((c) => c.text || "")
      .join("");
  }
  return "";
}

export default ChatWindow;
