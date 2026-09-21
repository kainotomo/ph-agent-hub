// =============================================================================
// PH Agent Hub — TokenUsageButton
// =============================================================================
// Pure presentational component showing token usage stats.
// No data fetching, no query hooks, no service imports (except SessionUsageData type).
// =============================================================================

import React from "react";
import { Button, Popover, Spin, Typography } from "antd";
import type { SessionUsageData } from "../services/chat";
import { formatTokensCompact, formatTokensExact, formatPercent } from "../utils/formatMetrics";

const { Text } = Typography;

export interface TokenUsageButtonProps {
  usage?: SessionUsageData;
  isLoading: boolean;
  isError: boolean;
}

export const TokenUsageButton = React.memo(function TokenUsageButton({
  usage,
  isLoading,
  isError,
}: TokenUsageButtonProps) {
  // Loading state
  if (isLoading) {
    return (
      <div data-testid="token-usage-loading">
        <Spin size="small" />
      </div>
    );
  }

  // Error state
  if (isError) {
    return (
      <div data-testid="token-usage-error">
        <Text style={{ fontSize: 12, color: "#8c8c8c" }}>n/a</Text>
      </div>
    );
  }

  // Normal state — text button with popover
  const buttonLabel = `${formatTokensCompact(usage!.tokens_total)} tok · Cache hit ${formatPercent(usage!.cache_hit_percent)}`;

  const popoverContent = (
    <div data-testid="token-usage-popover" style={{ minWidth: 220 }}>
      <Text strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
        Token usage
      </Text>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>{formatTokensExact(usage!.tokens_total)} tok</Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Cache hit</Text>
          <Text style={{ fontSize: 12 }}>{formatPercent(usage!.cache_hit_percent)}</Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Uncached input</Text>
          <Text style={{ fontSize: 12 }}>{formatTokensExact(usage!.uncached_input_tokens)} tok</Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Cached input</Text>
          <Text style={{ fontSize: 12 }}>{formatTokensExact(usage!.cached_input_tokens)} tok</Text>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <Text style={{ fontSize: 12 }}>Output</Text>
          <Text style={{ fontSize: 12 }}>{formatTokensExact(usage!.tokens_out)} tok</Text>
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
        data-testid="token-usage-button"
        type="text"
        size="small"
        style={{ fontSize: 12 }}
      >
        {buttonLabel}
      </Button>
    </Popover>
  );
});

export default TokenUsageButton;
