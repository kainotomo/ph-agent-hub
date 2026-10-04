// =============================================================================
// PH Agent Hub — SessionUsageToolbar
// =============================================================================
// Single query that owns usage data and passes it to SessionStatsButton,
// TokenUsageButton, and ContextIndicator.
// =============================================================================

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { getSessionUsage } from "../services/chat";
import { SessionStatsButton } from "./SessionStatsButton";
import { TokenUsageButton } from "./TokenUsageButton";
import { ContextIndicator } from "./ContextIndicator";

export interface SessionUsageToolbarProps {
  sessionId?: string;
  streaming?: boolean;
  /** Issue #573 — phone layout: single non-wrapping line with icon-only buttons. */
  compact?: boolean;
}

export const SessionUsageToolbar = React.memo(function SessionUsageToolbar({
  sessionId,
  streaming,
  compact = false,
}: SessionUsageToolbarProps) {
  // Hook must run unconditionally (Rules of Hooks); `enabled` guards the fetch.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["sessionUsage", sessionId],
    queryFn: () => getSessionUsage(sessionId!),
    enabled: !!sessionId,
    refetchInterval: streaming ? 3000 : false,
  });

  if (!sessionId) {
    return null;
  }

  return (
    <div
      data-testid="session-usage-toolbar"
      data-compact={compact ? "true" : "false"}
      style={{
        display: "flex",
        gap: compact ? 2 : 4,
        flexWrap: compact ? "nowrap" : "wrap",
        alignItems: "center",
        minWidth: 0,
        // Breathing room from the message input directly above (Issue #531 QA).
        marginTop: 8,
      }}
    >
      <SessionStatsButton
        usage={data}
        isLoading={isLoading}
        isError={isError}
        compact={compact}
      />
      <TokenUsageButton
        usage={data}
        isLoading={isLoading}
        isError={isError}
        compact={compact}
      />
      <ContextIndicator sessionId={sessionId} />
    </div>
  );
});

export default SessionUsageToolbar;
