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
}

export const SessionUsageToolbar = React.memo(function SessionUsageToolbar({
  sessionId,
  streaming,
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
      style={{
        display: "flex",
        gap: 4,
        flexWrap: "wrap",
        alignItems: "center",
      }}
    >
      <SessionStatsButton usage={data} isLoading={isLoading} isError={isError} />
      <TokenUsageButton usage={data} isLoading={isLoading} isError={isError} />
      <ContextIndicator sessionId={sessionId} />
    </div>
  );
});

export default SessionUsageToolbar;
