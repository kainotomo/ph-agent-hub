// =============================================================================
// PH Agent Hub — ContextIndicator
// =============================================================================
// Circular progress ring in the sidebar that shows how much of the model's
// context window is consumed. Clicking opens a popover with exact token
// counts and a "Compact Conversation" button (Issue #309).
// =============================================================================

import { useState, useCallback } from "react";
import { Button, Popover, Progress, Tooltip, Typography, message } from "antd";
import { CompressOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getSessionContext,
  summarizeSession,
} from "../services/chat";

const { Text } = Typography;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a number to a concise human-readable form: 4400 → "4.4k" */
function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}m`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** Pick a stroke colour based on usage percentage. */
function strokeColorForPercentage(pct: number): string {
  if (pct > 80) return "#ff4d4f"; // red
  if (pct > 50) return "#faad14"; // orange
  return "#52c41a";               // green
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ContextIndicatorProps {
  sessionId?: string;
}

export function ContextIndicator({ sessionId }: ContextIndicatorProps) {
  const queryClient = useQueryClient();
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [compacting, setCompacting] = useState(false);

  // Fetch context data
  const { data, isLoading, isError } = useQuery({
    queryKey: ["sessionContext", sessionId],
    queryFn: () => getSessionContext(sessionId!),
    enabled: !!sessionId,
  });

  const tokensUsed = data?.tokens_used ?? 0;
  const contextLength = data?.context_length ?? null;
  const percentage = data?.percentage ?? null;

  const handleCompact = useCallback(async () => {
    if (!sessionId) return;
    setCompacting(true);
    try {
      const result = await summarizeSession(sessionId);
      message.success(
        `Compressed ${result.summarized_message_count} messages. Saved ~${result.tokens_saved} tokens.`,
      );
      // Refresh both messages and context
      queryClient.invalidateQueries({ queryKey: ["messages", sessionId] });
      queryClient.invalidateQueries({ queryKey: ["sessionContext", sessionId] });
      setPopoverOpen(false);
    } catch (err: any) {
      message.error(err?.message || "Summarization failed");
    } finally {
      setCompacting(false);
    }
  }, [sessionId, queryClient]);

  // Hide if there's no active session
  if (!sessionId) {
    return null;
  }

  // Loading state: show a plain icon
  if (isLoading || isError) {
    return (
      <Tooltip title="Context Window">
        <Button
          type="text"
          icon={<CompressOutlined />}
          size="small"
          disabled
        />
      </Tooltip>
    );
  }

  // If context_length is not known, show icon without progress ring
  const hasContextLength = contextLength !== null && contextLength > 0;
  const rawPct = hasContextLength && percentage !== null
    ? Math.min(percentage, 100)
    : 0;
  // Always show at least a tiny arc so the ring is visible
  const progressPct = rawPct > 0 ? rawPct : 1;
  const strokeColor = hasContextLength ? strokeColorForPercentage(rawPct) : "#8c8c8c";


  // Popover content
  const popoverContent = (
    <div style={{ minWidth: 200 }}>
      <Text strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
        Context Window
      </Text>
      {hasContextLength ? (
        <Text style={{ fontSize: 12, display: "block", marginBottom: 12 }}>
          {formatTokenCount(tokensUsed)} / {formatTokenCount(contextLength!)} tokens
          {" "}
          <Text type="secondary" style={{ fontSize: 11 }}>
            ({progressPct.toFixed(1)}%)
          </Text>
        </Text>
      ) : (
        <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 12 }}>
          Context length not configured for this model
        </Text>
      )}
      <Button
        type="primary"
        size="small"
        icon={<CompressOutlined />}
        onClick={handleCompact}
        loading={compacting}
        block
      >
        Compact Conversation
      </Button>
    </div>
  );

  return (
    <Popover
      content={popoverContent}
      trigger="click"
      open={popoverOpen}
      onOpenChange={setPopoverOpen}
      placement="bottomLeft"
    >
      <Tooltip title={`${progressPct.toFixed(1)}%`}>
        <Button
          type="text"
          size="small"
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 0,
            lineHeight: 0,
            height: 24,
            width: 24,
          }}
        >
          {hasContextLength ? (
            <Progress
              type="circle"
              percent={progressPct}
              size={22}
              strokeColor={strokeColor}
              trailColor="#d9d9d9"
              format={() => ""}
              strokeWidth={5}
            />
          ) : (
            <CompressOutlined style={{ fontSize: 14, color: "#8c8c8c" }} />
          )}
        </Button>
      </Tooltip>
    </Popover>
  );
}

export default ContextIndicator;
