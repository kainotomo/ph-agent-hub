// =============================================================================
// PH Agent Hub — MessageBubble
// =============================================================================
// Renders user/assistant message; markdown via react-markdown+remark-gfm;
// code blocks via react-syntax-highlighter; includes MessageFeedback;
// tool activity display for tool_start/tool_result events.
// =============================================================================

import React from "react";
import { Typography, Space, Collapse, Tag, Button, Popconfirm, App, Spin, Tooltip } from "antd";
import {
  UserOutlined,
  RobotOutlined,
  ToolOutlined,
  EditOutlined,
  DeleteOutlined,
  RedoOutlined,
  BulbOutlined,
  FileOutlined,
  CopyOutlined,
  CompressOutlined,
  DollarOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { MessageFeedback } from "./MessageFeedback";
import type { MessageData } from "../services/chat";
import { listMessageUploads } from "../services/chat";
import { getToken } from "../../../services/api";

const { Text, Paragraph } = Typography;

// ---------------------------------------------------------------------------
// Internal: parse content into displayable items
// ---------------------------------------------------------------------------

interface ContentItem {
  type: string;
  text?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  output?: string;
  is_error?: boolean;
  id?: string;
}

function parseContent(content: unknown): ContentItem[] {
  if (!content) return [];
  if (Array.isArray(content)) {
    return content as ContentItem[];
  }
  if (typeof content === "string") {
    return [{ type: "text", text: content }];
  }
  if (typeof content === "object" && content !== null) {
    return [content as ContentItem];
  }
  return [];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface MessageBubbleProps {
  message: MessageData;
  sessionId: string;
  onEdit?: (messageId: string) => void;
  onDelete?: (messageId: string) => void;
  onRegenerate?: (messageId: string) => void;
  disabled?: boolean;
  regenerating?: boolean;
  streaming?: boolean;
}

function MessageBubbleInner({
  message,
  sessionId,
  onEdit,
  onDelete,
  onRegenerate,
  disabled,
  regenerating,
  streaming,
}: MessageBubbleProps) {
  const isUser = message.sender === "user";
  const isSystem = message.sender === "system";
  const contentItems = parseContent(message.content);

  // Separate text, reasoning, and tool events
  const textItems = contentItems.filter((c) => c.type === "text");
  const reasoningItems = contentItems.filter((c) => c.type === "reasoning");
  const toolItems = contentItems.filter(
    (c) => c.type === "function_call" || c.type === "function_result",
  );

  const bubbleStyle: React.CSSProperties = {
    padding: "12px 16px",
    borderRadius: 12,
    marginBottom: 8,
    ...(isSystem
      ? {
          maxWidth: "80%",
          background: "#fffbe6",
          border: "1px solid #ffe58f",
          marginLeft: "auto",
          marginRight: "auto",
          textAlign: "center",
        }
      : isUser
      ? {
          maxWidth: "80%",
          background: "#1677ff",
          color: "#fff",
          marginLeft: "auto",
          borderBottomRightRadius: 4,
        }
      : {
          background: "transparent",
          padding: 0,
          borderRadius: 0,
        }),
  };

  const { message: messageApi } = App.useApp();

  // Fetch attached files for user messages
  const { data: attachedFiles } = useQuery({
    queryKey: ["message-uploads", message.id],
    queryFn: () => listMessageUploads(sessionId, message.id),
    enabled: isUser,
    staleTime: Infinity,
  });

  return (
    <div style={{ marginBottom: 16 }}>
      {/* Sender indicator */}
      <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", marginBottom: 2 }}>
        <Space style={{ marginLeft: isUser ? undefined : 4 }} size={2}>
          {isUser ? (
            <UserOutlined style={{ color: "#1677ff", fontSize: 11 }} />
          ) : isSystem ? (
            <CompressOutlined style={{ color: "#faad14", fontSize: 11 }} />
          ) : (
            <RobotOutlined style={{ color: "#52c41a", fontSize: 11 }} />
          )}
          <Text type="secondary" style={{ fontSize: 11 }}>
            {isUser ? "You" : isSystem ? "Summary" : "Assistant"}
          </Text>
          {!isUser && (message.model_name || message.model_id) && (
            <Text type="secondary" style={{ fontSize: 10, color: "#bbb" }}>
              · {message.model_name || message.model_id?.slice(0, 8)}
            </Text>
          )}
        </Space>
      </div>

      {/* Bubble */}
      <div style={bubbleStyle}>
        {/* Reasoning panel — auto-expanded during streaming so user can see
            the model think in real-time, collapsed otherwise. */}
        {reasoningItems.length > 0 && (
          <Collapse
            ghost
            size="small"
            defaultActiveKey={streaming ? ["reasoning"] : []}
            items={[
              {
                key: "reasoning",
                label: (
                  <Space>
                    <BulbOutlined style={{ color: "#722ed1" }} />
                    <Text style={{ fontSize: 12 }}>
                      Reasoning ({reasoningItems.map((r) => r.text || "").join("").length} chars)
                    </Text>
                  </Space>
                ),
                children: (
                  <div
                    style={{
                      maxHeight: 300,
                      overflow: "auto",
                      borderLeft: "3px solid #d3adf7",
                      padding: "4px 12px",
                    }}
                  >
                    <Typography.Paragraph
                      style={{
                        fontSize: 12,
                        whiteSpace: "pre-wrap",
                        margin: 0,
                        color: "#531dab",
                      }}
                    >
                      {reasoningItems.map((r) => r.text || "").join("")}
                    </Typography.Paragraph>
                  </div>
                ),
                style: {
                  marginBottom: textItems.length > 0 ? 8 : 0,
                },
              },
            ]}
          />
        )}

        {textItems.map((item, i) => (
          <div key={i} style={isUser ? undefined : { maxWidth: 750 }}>
            {isUser ? (
              <Text style={{ color: "#fff", whiteSpace: "pre-wrap" }}>
                {item.text}
              </Text>
            ) : (
              <div className="markdown-body" style={{ fontSize: 14 }}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    code({ className, children, ...props }) {
                      const match = /language-(\w+)/.exec(
                        className || "",
                      );
                      const codeStr = String(children).replace(
                        /\n$/,
                        "",
                      );
                      if (match) {
                        return (
                          <SyntaxHighlighter
                            style={oneDark}
                            language={match[1]}
                            PreTag="div"
                          >
                            {codeStr}
                          </SyntaxHighlighter>
                        );
                      }
                      return (
                        <code
                          className={className}
                          {...(props as Record<string, unknown>)}
                        >
                          {children}
                        </code>
                      );
                    },
                  }}
                >
                  {item.text || ""}
                </ReactMarkdown>
              </div>
            )}
          </div>
        ))}

        {/* Tool calls / results */}
        {toolItems.length > 0 && (
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: "tools",
                label: (
                  <Space>
                    <ToolOutlined />
                    <Text style={{ fontSize: 12, color: isUser ? "#fff" : undefined }}>
                      Tool Activity ({toolItems.length})
                    </Text>
                  </Space>
                ),
                children: (
                  <div style={{ maxHeight: 200, overflow: "auto" }}>
                    {toolItems.map((item, i) => (
                      <div key={i} style={{ marginBottom: 8 }}>
                        {item.type === "function_call" ? (
                          <Tag color="blue">
                            🔧 {item.name}
                          </Tag>
                        ) : (
                          <div>
                            <Tag
                              color={
                                item.is_error
                                  ? "red"
                                  : "green"
                              }
                            >
                              ✓ {item.name || "result"}
                            </Tag>
                            {item.output && (
                              <Paragraph
                                ellipsis={{ rows: 2 }}
                                style={{
                                  fontSize: 12,
                                  margin: "4px 0 0 0",
                                  color: isUser ? "#fff" : "#666",
                                }}
                              >
                                {typeof item.output === "string"
                                  ? item.output
                                  : JSON.stringify(item.output)}
                              </Paragraph>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ),
                style: {
                  marginTop: 8,
                },
              },
            ]}
          />
        )}
        {/* Attached files (user messages only) */}
        {isUser && attachedFiles && attachedFiles.length > 0 && (
          <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
            {attachedFiles.map((f) => (
              <Tag
                key={f.file_id}
                icon={<FileOutlined />}
                color="default"
                style={{ cursor: "pointer", margin: 0 }}
                onClick={async (e) => {
                  e.stopPropagation();
                  try {
                    const BASE_URL = import.meta.env.VITE_API_URL || "/api";
                    const token = getToken();
                    const res = await fetch(
                      `${BASE_URL}/chat/session/${sessionId}/upload/${f.file_id}/download`,
                      {
                        headers: token
                          ? { Authorization: `Bearer ${token}` }
                          : {},
                      },
                    );
                    if (!res.ok) return;
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = f.original_filename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                  } catch {
                    // Silently fail
                  }
                }}
              >
                {f.original_filename}
              </Tag>
            ))}
          </div>
        )}
      </div>

      {/* Actions row (assistant messages only) */}
      {!isUser && (
        <Space
          style={{ marginLeft: 4, marginTop: 2 }}
          size="small"
        >
          <MessageFeedback
            sessionId={sessionId}
            messageId={message.id}
          />
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
            onClick={() => {
              const text = textItems.map((t) => t.text || "").join("\n");
              navigator.clipboard.writeText(text).then(() => {
                messageApi.success("Copied to clipboard");
              }).catch(() => {
                messageApi.error("Failed to copy");
              });
            }}
            disabled={disabled}
          />
          {(message.tokens_in != null || message.tokens_out != null) && (
            <Tooltip
              title={
                <span>
                  Input: ~{message.tokens_in ?? "?"} tokens<br />
                  Output: ~{message.tokens_out ?? "?"} tokens
                  {(message.tokens_in != null && message.tokens_out != null) && (
                    <>
                      <br />Total: ~{(message.tokens_in ?? 0) + (message.tokens_out ?? 0)} tokens
                    </>
                  )}
                </span>
              }
            >
              <Button
                type="text"
                size="small"
                icon={<DollarOutlined />}
                style={{ color: "#8c8c8c" }}
                disabled={disabled}
              >
                <Text style={{ fontSize: 11, color: "#8c8c8c" }}>
                  {message.tokens_out ?? message.tokens_in ?? 0}
                </Text>
              </Button>
            </Tooltip>
          )}
          {onRegenerate && (
            <Button
              type="text"
              size="small"
              icon={regenerating ? <Spin size="small" /> : <RedoOutlined />}
              onClick={() => onRegenerate(message.id)}
              disabled={disabled || regenerating}
            />
          )}
          {onDelete && (
            <Popconfirm
              title="Delete this message?"
              onConfirm={() => onDelete(message.id)}
            >
              <Button
                type="text"
                size="small"
                icon={<DeleteOutlined />}
                danger
                disabled={disabled}
              />
            </Popconfirm>
          )}
        </Space>
      )}

      {/* Actions row (user messages only) */}
      {isUser && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 2 }}>
        <Space size="small">
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
            onClick={() => {
              const text = textItems.map((t) => t.text || "").join("\n");
              navigator.clipboard.writeText(text).then(() => {
                messageApi.success("Copied to clipboard");
              }).catch(() => {
                messageApi.error("Failed to copy");
              });
            }}
            disabled={disabled}
          />
          {onEdit && (
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => onEdit(message.id)}
              disabled={disabled}
            />
          )}
          {onDelete && (
            <Popconfirm
              title="Delete this message?"
              onConfirm={() => onDelete(message.id)}
            >
              <Button
                type="text"
                size="small"
                icon={<DeleteOutlined />}
                danger
                disabled={disabled}
              />
            </Popconfirm>
          )}
        </Space>
        </div>
      )}
    </div>
  );
}

export const MessageBubble = React.memo(MessageBubbleInner, (prev, next) =>
  prev.message.id === next.message.id &&
  prev.message.content === next.message.content &&
  prev.message.tokens_in === next.message.tokens_in &&
  prev.message.tokens_out === next.message.tokens_out &&
  prev.message.model_name === next.message.model_name &&
  prev.streaming === next.streaming &&
  prev.regenerating === next.regenerating &&
  prev.disabled === next.disabled &&
  prev.onEdit === next.onEdit &&
  prev.onDelete === next.onDelete &&
  prev.onRegenerate === next.onRegenerate &&
  prev.sessionId === next.sessionId
);

export default MessageBubble;
