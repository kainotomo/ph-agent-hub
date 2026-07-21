// =============================================================================
// PH Agent Hub — AutopilotPanel
// =============================================================================
// Live progress panel shown during autopilot execution.  Displays the
// current turn number, progress bar, and collapsible tool activity.
// Collapses automatically when the autopilot completes.
// =============================================================================

import { Card, Progress, Tag, Space, Typography, Collapse, Spin } from "antd";
import {
  CheckCircleOutlined,
  LoadingOutlined,
} from "@ant-design/icons";

const { Text } = Typography;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AutopilotState {
  /** Current turn number (1-based). */
  currentTurn: number;
  /** Maximum turns configured for this autopilot run. */
  maxTurns: number;
  /** Overall status of the autopilot session. */
  status:
    | "idle"
    | "executing"
    | "complete"
    | "max_turns"
    | "error";
  /** Summary from the final autopilot_complete event. */
  summary?: string;
  /** Error message if status is "error". */
  errorMessage?: string;
}

export const INITIAL_AUTOPILOT_STATE: AutopilotState = {
  currentTurn: 0,
  maxTurns: 20,
  status: "idle",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface AutopilotPanelProps {
  state: AutopilotState;
  /** Called when the user clicks "Stop" */
  onStop?: () => void;
}

export function AutopilotPanel({ state, onStop }: AutopilotPanelProps) {
  const { currentTurn, maxTurns, status, summary } = state;

  // Don't render when idle.
  // When complete, show finished state for a brief moment then auto-hide
  // (reactivated by onClose resetting to idle).
  if (status === "idle") {
    return null;
  }
  const isFinished = status === "complete" || status === "max_turns" || status === "error";

  const progressPercent = maxTurns > 0
    ? Math.min(Math.round((currentTurn / maxTurns) * 100), 100)
    : 0;

  return (
    <div style={{ padding: "0 16px 12px" }}>
      <Card
        size="small"
        style={{
          borderLeft: "4px solid #1677ff",
          background: "#f6f8ff",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 8,
          }}
        >
          <Space>
            {isFinished ? (
              <CheckCircleOutlined style={{ color: "#faad14", fontSize: 16 }} />
            ) : (
              <Spin
                indicator={<LoadingOutlined style={{ fontSize: 16, color: "#1677ff" }} />}
              />
            )}
            <Text strong style={{ fontSize: 14 }}>
              {isFinished
                ? "Autopilot finished"
                : `Working on turn ${currentTurn} of ${maxTurns}…`}
            </Text>
          </Space>
          {onStop && (
            <Tag
              color="error"
              style={{ cursor: "pointer" }}
              onClick={onStop}
            >
              Stop
            </Tag>
          )}
        </div>

        {/* Progress bar */}
        <Progress
          percent={isFinished ? 100 : progressPercent}
          size="small"
          status={status === "error" ? "exception" : isFinished ? "success" : "active"}
          strokeColor={status === "max_turns" ? "#faad14" : undefined}
          style={{ marginBottom: 4 }}
        />

        {/* Result message */}
        {status === "max_turns" && (
          <Text type="warning" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
            Reached maximum of {maxTurns} turns — showing partial results.
          </Text>
        )}
        {status === "error" && state.errorMessage && (
          <Text type="danger" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
            Error: {state.errorMessage}
          </Text>
        )}

        {/* Summary (shown briefly before collapsing) */}
        {summary && (
          <Collapse
            ghost
            size="small"
            style={{ marginTop: 4 }}
            items={[
              {
                key: "summary",
                label: <Text style={{ fontSize: 12 }}>View final summary</Text>,
                children: (
                  <Text style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
                    {summary}
                  </Text>
                ),
              },
            ]}
          />
        )}
      </Card>
    </div>
  );
}

export default AutopilotPanel;
