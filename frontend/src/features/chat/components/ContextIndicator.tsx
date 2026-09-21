// =============================================================================
// PH Agent Hub — ContextIndicator
// =============================================================================
// Compact context-window gauge shown in the chat top bar (left of ModelSelector).
// Hidden for unsent new chats. Clicking opens a popover with exact token
// counts, the auto-compact threshold, and a "Compact Conversation" button.
//
// Severity bands (non-hue-only):
//   normal   (< 60%)  — blue arc, no warning
//   elevated (60–74%) — darker blue arc, no warning
//   critical (≥ 75%)  — darkest blue arc + WarningOutlined icon
//
// The percentage text is always visible (WCAG 1.4.11 compliant via text contrast).
// =============================================================================

import React, { useState, useCallback } from "react";
import { Button, Popover, Progress, Typography, message } from "antd";
import { WarningOutlined, CompressOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getSessionContext,
  summarizeSession,
} from "../services/chat";

const { Text } = Typography;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Severity band for a usage percentage. Mirrors SUMMARIZE_THRESHOLD (0.75). */
export function bandForPercentage(pct: number): "normal" | "elevated" | "critical" {
  if (pct >= 75) return "critical";
  if (pct >= 60) return "elevated";
  return "normal";
}

/** Arc color for a severity band — single-hue blue ramp. */
export function arcColorForBand(band: "normal" | "elevated" | "critical"): string {
  switch (band) {
    case "normal":
      return "#1677ff";
    case "elevated":
      return "#0958d9";
    case "critical":
      return "#003eb3";
  }
}

/** Format a number to a concise human-readable form: 4400 → "4.4k" */
export function formatTokenCount(n: number): string {
  if (n >= 1_000_000) {
    const v = n / 1_000_000;
    return Number.isInteger(v) ? `${v}m` : `${v.toFixed(1)}m`;
  }
  if (n >= 1_000) {
    const v = n / 1_000;
    return Number.isInteger(v) ? `${v}k` : `${v.toFixed(1)}k`;
  }
  return String(n);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ContextIndicatorProps {
  sessionId?: string;
}

export const ContextIndicator = React.memo(function ContextIndicator({ sessionId }: ContextIndicatorProps) {
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

  // Loading state
  if (isLoading) {
    return (
      <div data-testid="context-indicator-loading" style={{ display: "inline-flex", alignItems: "center", gap: 4, height: 24, paddingInline: 2 }}>
        <Progress
          type="circle"
          percent={0}
          size={18}
          strokeColor="#d9d9d9"
          trailColor="#f0f0f0"
          strokeWidth={5}
          format={() => ""}
          aria-hidden="true"
        />
        <Text style={{ fontSize: 12, color: "#8c8c8c" }} data-testid="context-indicator-label">...</Text>
      </div>
    );
  }

  // Error state
  if (isError) {
    return (
      <div data-testid="context-indicator-error" style={{ display: "inline-flex", alignItems: "center", gap: 4, height: 24, paddingInline: 2 }}>
        <ExclamationCircleOutlined style={{ fontSize: 12, color: "#d4380d" }} />
        <Text style={{ fontSize: 12, color: "#8c8c8c" }} data-testid="context-indicator-label">n/a</Text>
      </div>
    );
  }

  // Unconfigured state (no context_length)
  const hasContextLength = contextLength !== null && contextLength > 0;
  if (!hasContextLength) {
    return (
      <div data-testid="context-indicator-unconfigured" style={{ display: "inline-flex", alignItems: "center", gap: 4, height: 24, paddingInline: 2 }}>
        <CompressOutlined style={{ fontSize: 12, color: "#8c8c8c" }} />
        <Text style={{ fontSize: 12, color: "#8c8c8c" }} data-testid="context-indicator-label">-</Text>
      </div>
    );
  }

  // Normal state
  const rawPct = Math.min(percentage ?? 0, 100);
  const band = bandForPercentage(rawPct);
  const arcColor = arcColorForBand(band);
  const labelPct = Math.round(rawPct);

  // Build aria-label with exact figures
  const ariaLabel = `Context window: ${labelPct}% used, ${formatTokenCount(tokensUsed)} of ${formatTokenCount(contextLength!)} tokens. Opens context details and compaction.`;

  // Popover content
  const popoverContent = (
    <div style={{ minWidth: 200 }}>
      <Text strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
        Context Window
      </Text>
      <Text style={{ fontSize: 12, display: "block", marginBottom: 12 }}>
        {formatTokenCount(tokensUsed)} / {formatTokenCount(contextLength!)} tokens
        {" "}
        <Text type="secondary" style={{ fontSize: 11 }}>
          ({(percentage ?? 0).toFixed(1)}%)
        </Text>
      </Text>
      <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 12 }}>
        Auto-compact at 75% usage
      </Text>
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
      data-testid="context-indicator"
      content={popoverContent}
      trigger="click"
      open={popoverOpen}
      onOpenChange={setPopoverOpen}
      placement="bottomLeft"
    >
      <Button
        type="text"
        size="small"
        aria-label={ariaLabel}
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 24,
          paddingInline: 2,
          gap: 4,
          lineHeight: 0,
        }}
      >
        <Progress
          data-testid="context-indicator-ring"
          type="circle"
          percent={rawPct}
          size={18}
          strokeWidth={5}
          strokeColor={arcColor}
          trailColor="#bfbfbf"
          format={() => ""}
          aria-hidden="true"
        />
        <Text data-testid="context-indicator-label" style={{ fontSize: 12, color: band === "critical" ? "#d4380d" : "#595959" }}>
          {labelPct}%
        </Text>
        {band === "critical" && (
          <WarningOutlined data-testid="context-indicator-warning" style={{ fontSize: 12, color: "#d4380d" }} />
        )}
      </Button>
    </Popover>
  );
});

export default ContextIndicator;
