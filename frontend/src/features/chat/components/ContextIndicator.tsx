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
//   critical (≥ 75%)  — darkest blue arc + WarningOutlined icon (right of ring)
//
// The percentage is drawn INSIDE the ring via Progress `format`. antd only
// paints circle children when size > 20 (smaller circles become tooltip-only),
// which is why RING_SIZE must stay above that threshold. The percentage stays
// visible text (WCAG 1.4.11 compliant via text contrast).
// =============================================================================

import React, { useState, useCallback } from "react";
import { Button, Divider, Popover, Progress, Typography, message } from "antd";
import { WarningOutlined, CompressOutlined, ExclamationCircleOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getSessionContext,
  summarizeSession,
} from "../services/chat";
import { formatTokensApprox, formatTokensCompact } from "../utils/formatMetrics";

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

/**
 * Ring diameter in px. Must stay > 20 so antd renders the inner label.
 * Set to 24 to match `controlHeightSM` (32 × 0.75), i.e. the height of the
 * small text buttons sharing the SessionUsageToolbar row.
 */
const RING_SIZE = 24;

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
      <div data-testid="context-indicator-loading" style={{ display: "inline-flex", alignItems: "center", height: RING_SIZE, paddingInline: 2 }}>
        <Progress
          type="circle"
          percent={0}
          size={RING_SIZE}
          strokeColor="#d9d9d9"
          trailColor="#f0f0f0"
          strokeWidth={6}
          format={() => (
            <span data-testid="context-indicator-label" style={{ fontSize: 11, lineHeight: 1, color: "#8c8c8c" }}>
              …
            </span>
          )}
          aria-hidden="true"
        />
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
      <Text style={{ fontSize: 13, fontWeight: 600, display: "block", marginBottom: 2 }}>
        {Math.round(percentage ?? 0)}%
      </Text>
      <Text style={{ fontSize: 11, color: "#8c8c8c", display: "block", marginBottom: 8 }}>
        of context used
      </Text>
      <Text style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
        {formatTokensApprox(tokensUsed)} / {formatTokensCompact(contextLength!)} in context
      </Text>
      <Divider style={{ margin: "8px 0" }} />
      {(data?.system_prompt_tokens ?? data?.tool_definition_tokens ?? data?.messages_tokens) != null && (
        <div data-testid="context-breakdown">
          {data?.system_prompt_tokens != null && (
            <div data-testid="context-breakdown-system" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <Text type="secondary">System prompt</Text>
              <Text>{formatTokensApprox(data.system_prompt_tokens)}</Text>
            </div>
          )}
          {data?.tool_definition_tokens != null && (
            <div data-testid="context-breakdown-tools" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <Text type="secondary">Tool definitions</Text>
              <Text>{formatTokensApprox(data.tool_definition_tokens)}</Text>
            </div>
          )}
          {data?.messages_tokens != null && (
            <div data-testid="context-breakdown-messages" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <Text type="secondary">Messages</Text>
              <Text>{formatTokensApprox(data.messages_tokens)}</Text>
            </div>
          )}
        </div>
      )}
      <Divider style={{ margin: "8px 0" }} />
      <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 8 }}>
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
          height: RING_SIZE,
          paddingInline: 2,
          gap: 4,
          lineHeight: 0,
        }}
      >
        <Progress
          data-testid="context-indicator-ring"
          type="circle"
          percent={rawPct}
          size={RING_SIZE}
          strokeWidth={6}
          strokeColor={arcColor}
          trailColor="#bfbfbf"
          format={() => (
            <span
              data-testid="context-indicator-label"
              style={{
                fontSize: labelPct >= 100 ? 9 : 10,
                lineHeight: 1,
                fontWeight: 600,
                whiteSpace: "nowrap",
                fontVariantNumeric: "tabular-nums",
                color: band === "critical" ? "#d4380d" : "#434343",
              }}
            >
              {labelPct}%
            </span>
          )}
          aria-hidden="true"
        />
        {band === "critical" && (
          <WarningOutlined data-testid="context-indicator-warning" style={{ fontSize: 12, color: "#d4380d" }} />
        )}
      </Button>
    </Popover>
  );
});

export default ContextIndicator;
