// =============================================================================
// PH Agent Hub — SessionStatsButton
// =============================================================================
// Pure presentational component showing session usage stats.
// No data fetching, no query hooks, no service imports (except SessionUsageData type).
// =============================================================================

import React from "react";
import { Button, Popover, Spin, Typography } from "antd";
import type { SessionUsageData } from "../services/chat";
import { formatDuration, formatTps } from "../utils/formatMetrics";

const { Text } = Typography;

export interface SessionStatsButtonProps {
  usage?: SessionUsageData;
  isLoading: boolean;
  isError: boolean;
}

export const SessionStatsButton = React.memo(function SessionStatsButton({
  usage,
  isLoading,
  isError,
}: SessionStatsButtonProps) {
  // Loading state
  if (isLoading) {
    return (
      <div data-testid="session-stats-loading">
        <Spin size="small" />
      </div>
    );
  }

  // Error state
  if (isError) {
    return (
      <div data-testid="session-stats-error">
        <Text style={{ fontSize: 12, color: "#8c8c8c" }}>n/a</Text>
      </div>
    );
  }

  // Normal state — text button with popover
  const buttonLabel = `${usage!.turns} turns · ${usage!.steps} steps · ${formatTps(usage!.tps)}`;

  const popoverContent = (
    <div data-testid="session-stats-popover" style={{ minWidth: 220 }}>
      <Text strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
        Session statistics
      </Text>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>LLM time</Text>
          <Text style={{ fontSize: 12 }}>
            {usage!.has_timing_data
              ? formatDuration(usage!.llm_time_ms)
              : "\u2014"}
          </Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Tool time</Text>
          <Text style={{ fontSize: 12 }}>
            {usage!.has_timing_data
              ? formatDuration(usage!.tool_time_ms)
              : "\u2014"}
          </Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Avg time to first token (TTFT)</Text>
          <Text style={{ fontSize: 12 }}>
            {usage!.has_timing_data
              ? formatDuration(usage!.avg_ttft_ms)
              : "\u2014"}
          </Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Tokens per second (TPS)</Text>
          <Text style={{ fontSize: 12 }}>
            {usage!.has_timing_data
              ? formatTps(usage!.tps)
              : "\u2014"}
          </Text>
        </div>
      </div>
    </div>
  );

  return (
    <Popover
      content={popoverContent}
      trigger="click"
      placement="topLeft"
    >
      <Button
        data-testid="session-stats-button"
        type="text"
        size="small"
        style={{ fontSize: 12 }}
      >
        {buttonLabel}
      </Button>
    </Popover>
  );
});

export default SessionStatsButton;
