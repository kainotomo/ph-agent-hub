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
  CompressOutlined,
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
  summarizeSession,
} from "../services/chat";
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

const { TextArea } = Input;
const { Text } = Typography;
const { useBreakpoint } = Grid;

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
  onSessionUpdate,
}: ChatWindowProps) {
  const [inputValue, setInputValue] = useState("");
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
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const queryClient = useQueryClient();
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const [toolsOpen, setToolsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [showScrollButton, setShowScrollButton] = useState(false);

  // Optimistic user message — shown immediately on send, replaced by
  // the real persisted message when the response completes or the
  // stream is stopped / errors out.
  const [pendingUserMessage, setPendingUserMessage] = useState<{
    id: string;
    content: string;
  } | null>(null);

  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [regeneratingMsgId, setRegeneratingMsgId] = useState<string | null>(null);

  const { streaming, startStream, startRegenerateStream, startEditStream, stopStream } = useStream();

  const { data: messages, isLoading: loadingMessages } = useQuery({
    queryKey: ["messages", sessionId],
    queryFn: () => listMessages(sessionId),
    refetchInterval: false,
  });

  // Fetch models to determine if selected model supports thinking
  interface ModelInfo {
    id: string;
    name: string;
    thinking_enabled: boolean;
    provider: string;
  }
  const { data: modelList } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelInfo[]>("/models"),
  });
  const selectedModel = useMemo(
    () => (modelList || []).find((m) => m.id === selectedModelId),
    [modelList, selectedModelId],
  );
  const modelSupportsThinking = selectedModel?.thinking_enabled === true;

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
  useEffect(() => {
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
  }, [sessionId, temperature, crossSessionMemoryEnabled]);

  // Clear editing state once the edited message is confirmed gone from the list
  useEffect(() => {
    if (editingMsgId && messages) {
      const stillExists = messages.some((m: any) => m.id === editingMsgId);
      if (!stillExists) {
        setEditingMsgId(null);
      }
    }
  }, [messages, editingMsgId]);

  // ---- Fetch follow-up questions after stream closes (Issue #126) -----------
  // The backend now generates follow-up questions in a background task so
  // the SSE stream can close immediately after message_complete.  This
  // helper polls the follow-up endpoint once after a short delay.
  const fetchFollowUpQuestions = useCallback(
    (sid: string, setter: (questions: string[]) => void) => {
      const BASE_URL = import.meta.env.VITE_API_URL || "/api";
      setTimeout(async () => {
        try {
          const token = getToken();
          const res = await fetch(
            `${BASE_URL}/chat/session/${sid}/follow-up-questions`,
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
    [],
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
        onToken(token, msgId) {
          setStreamingMessageId(msgId);
          setStreamingContent((prev) => prev + token);
        },
        onReasoningToken(delta) {
          setStreamingReasoningContent((prev) => prev + delta);
        },
        onToolStart(data) {
          setToolEvents((prev) => [...prev, { type: "function_call", data }]);
        },
        onToolResult(data) {
          setToolEvents((prev) => [...prev, { type: "function_result", data }]);
        },
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
        },
        onFollowUpQuestions(questions) {
          setFollowUpQuestions(questions);
        },
        onSummarized(data) {
          notification.info({
            message: "Conversation Summarized",
            description: `Compressed ${data.summarized_message_count} earlier messages to save context space.`,
            placement: "topRight",
            duration: 4,
          });
        },
        onError(err) {
          setEditingMsgId(null);
          setPendingUserMessage(null);
          setStreamingTokens(null);
          setStreamError(err);
          message.error(err || "Edit failed");
        },
        onClose() {
          setPendingUserMessage(null);
          setStreamingContent("");
          setStreamingReasoningContent("");
          setStreamingMessageId(null);
          setToolEvents([]);
          setStreamingTokens(null);
          queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
          queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
          queryClient.invalidateQueries({ queryKey: ["sessions"] });
          fetchFollowUpQuestions(sessionId, setFollowUpQuestions);
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

    startStream(
      sessionId,
      content,
      fileIds.length > 0 ? fileIds : undefined,
      sessionTemperature ?? undefined,
      {
      onToken(token, msgId) {
        setStreamingMessageId(msgId);
        setStreamingContent((prev) => prev + token);
      },
      onReasoningToken(delta) {
        setStreamingReasoningContent((prev) => prev + delta);
      },
      onToolStart(data) {
        setToolEvents((prev) => [
          ...prev,
          { type: "function_call", data },
        ]);
      },
      onToolResult(data) {
        setToolEvents((prev) => [
          ...prev,
          { type: "function_result", data },
        ]);
      },
      onMessageComplete(data) {
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
      },
      onFollowUpQuestions(questions) {
        setFollowUpQuestions(questions);
      },
      onTagsUpdated(_data) {
        queryClient.invalidateQueries({ queryKey: ["sessions"] });
      },
      onSummarized(data) {
        notification.info({
          message: "Conversation Summarized",
          description: `Compressed ${data.summarized_message_count} earlier messages to save context space.`,
          placement: "topRight",
          duration: 4,
        });
      },
      onError(err) {
        setPendingUserMessage(null);
        setStreamingTokens(null);
        setStreamError(err);
        console.error("Stream error:", err);
      },
      onClose() {
        setPendingUserMessage(null);
        setStreamingContent("");
        setStreamingReasoningContent("");
        setStreamingMessageId(null);
        setToolEvents([]);
        setStreamingTokens(null);
        queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
        queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
        queryClient.invalidateQueries({ queryKey: ["sessions"] });
        fetchFollowUpQuestions(sessionId, setFollowUpQuestions);
      },
    });
  }, [inputValue, streaming, sessionId, startStream, queryClient, pendingFiles, editingMsgId]);

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

    startRegenerateStream(sessionId, messageId, {
      onToken(token, msgId) {
        setStreamingMessageId(msgId);
        setStreamingContent((prev) => prev + token);
      },
      onReasoningToken(delta) {
        setStreamingReasoningContent((prev) => prev + delta);
      },
      onToolStart(data) {
        setToolEvents((prev) => [...prev, { type: "function_call", data }]);
      },
      onToolResult(data) {
        setToolEvents((prev) => [...prev, { type: "function_result", data }]);
      },
      onMessageComplete(data) {
        setRegeneratingMsgId(null);
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
      },
      onFollowUpQuestions(questions) {
        setFollowUpQuestions(questions);
      },
      onTagsUpdated(_data) {
        queryClient.invalidateQueries({ queryKey: ["sessions"] });
      },
      onSummarized(data) {
        notification.info({
          message: "Conversation Summarized",
          description: `Compressed ${data.summarized_message_count} earlier messages to save context space.`,
          placement: "topRight",
          duration: 4,
        });
      },
      onError(err) {
        setRegeneratingMsgId(null);
        setStreamingTokens(null);
        setStreamError(err);
        message.error(err || "Regenerate failed");
      },
      onClose() {
        setRegeneratingMsgId(null);
        setStreamingContent("");
        setStreamingReasoningContent("");
        setStreamingMessageId(null);
        setToolEvents([]);
        setStreamingTokens(null);
        queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
        queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
        queryClient.invalidateQueries({ queryKey: ["sessions"] });
        fetchFollowUpQuestions(sessionId, setFollowUpQuestions);
      },
    });
  }, [streaming, sessionId, startRegenerateStream, queryClient]);

  // File upload handlers
  const handleFileUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      try {
        const formData = new FormData();
        formData.append("file", file);
        const res = await api<{
          file_id: string;
          original_filename: string;
        }>(`/chat/session/${sessionId}/upload`, {
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
    [sessionId],
  );

  const handleRemoveFile = useCallback((fileId: string) => {
    setPendingFiles((prev) => prev.filter((f) => f.file_id !== fileId));
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
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
      {isMobile ? (
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
            <TemporaryChatBadge isTemporary={isTemporary} />
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
            <TemporaryChatBadge isTemporary={isTemporary} />
          )}
          <ModelSelector
            value={selectedModelId}
            onChange={(id) => onSessionUpdate?.({ selected_model_id: id })}
          />
          <TemplateSelector
            value={selectedTemplateId}
            onChange={(id) => onSessionUpdate?.({ selected_template_id: id })}
          />
          <SkillSelector
            value={selectedSkillId}
            onChange={(id) => onSessionUpdate?.({ selected_skill_id: id })}
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
          <Button
            size="small"
            icon={<CompressOutlined />}
            onClick={async () => {
              try {
                const result = await summarizeSession(sessionId);
                notification.success({
                  message: "Conversation Summarized",
                  description: `Compressed ${result.summarized_message_count} messages. Saved ~${result.tokens_saved} tokens.`,
                  placement: "topRight",
                  duration: 5,
                });
                queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
              } catch (err: any) {
                message.error(err?.message || "Summarization failed");
              }
            }}
            title="Summarize conversation"
          >
            Summarize
          </Button>
          <Switch
            size="small"
            checked={localCrossSessionMemory ?? false}
            checkedChildren="🧠 Memory"
            unCheckedChildren="🧠 Memory"
            title="Cross-session memory"
            onChange={(v) => {
              setLocalCrossSessionMemory(v);
              onSessionUpdate?.({ cross_session_retrieval_enabled: v });
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
                onSessionUpdate?.({ thinking_enabled: v });
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
                onSessionUpdate?.({ temperature: val });
              }}
              style={{ width: 80, margin: 0 }}
            />
          </div>
        </div>
      )}
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
            value={selectedModelId}
            onChange={(id) => onSessionUpdate?.({ selected_model_id: id })}
          />
          <TemplateSelector
            value={selectedTemplateId}
            onChange={(id) => onSessionUpdate?.({ selected_template_id: id })}
          />
          <SkillSelector
            value={selectedSkillId}
            onChange={(id) => onSessionUpdate?.({ selected_skill_id: id })}
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
          <Button
            icon={<CompressOutlined />}
            onClick={async () => {
              try {
                const result = await summarizeSession(sessionId);
                notification.success({
                  message: "Conversation Summarized",
                  description: `Compressed ${result.summarized_message_count} messages. Saved ~${result.tokens_saved} tokens.`,
                  placement: "topRight",
                  duration: 5,
                });
                queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
              } catch (err: any) {
                message.error(err?.message || "Summarization failed");
              }
            }}
            title="Summarize conversation"
          >
            Summarize
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
                onSessionUpdate?.({ thinking_enabled: v });
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
              onSessionUpdate?.({ cross_session_retrieval_enabled: v });
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
                  onSessionUpdate?.({ temperature: val });
                }}
                marks={{ 0: "0", 1: "1", 2: "2" }}
              />
            </Space>
          </div>
        </div>
      </Drawer>

      <MemoryManager
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
        sessionId={sessionId}
      />

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
                onEdit={msg.sender === "user" ? handleEdit : undefined}
                onDelete={
                  !isTemporary
                    ? handleDelete
                    : undefined
                }
                onRegenerate={
                  msg.sender === "assistant" && !isTemporary
                    ? handleRegenerate
                    : undefined
                }
                disabled={streaming}
                regenerating={regeneratingMsgId === msg.id}
                streaming={msg.id === streamingMessageId}
              />
            </div>
          )}
          components={{
            Header: () =>
              streamError ? (
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
              ) : null,
            EmptyPlaceholder: () =>
              loadingMessages ? (
                <div style={{ textAlign: "center", padding: 48 }}>
                  <Spin />
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
                  {followUpQuestions.map((q, i) => (
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

      {/* Input area */}
      <div
        style={{
          padding: "12px 16px",
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
          <div style={{ flex: 1, minWidth: 0 }}>
            <TextArea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                editingMsgId
                  ? "Edit message…"
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
              icon={editingMsgId ? <EditOutlined /> : <SendOutlined />}
              onClick={handleSend}
              disabled={!inputValue.trim()}
            >
              {editingMsgId ? "Edit & Send" : "Send"}
            </Button>
          )}
        </div>
      </div>

      {/* Tools drawer */}
      <SessionToolActivation
        sessionId={sessionId}
        open={toolsOpen}
        onClose={() => setToolsOpen(false)}
        selectedSkillId={selectedSkillId}
      />
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
