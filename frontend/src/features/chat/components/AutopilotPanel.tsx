// =============================================================================
// PH Agent Hub — AutopilotPanel
// =============================================================================
// Live progress panel shown during autopilot execution.  Displays the
// current turn number, progress bar, and collapsible tool activity.
// Collapses automatically when the autopilot completes.
// =============================================================================

import { useState } from "react";
import { Card, Progress, Tag, Space, Typography, Collapse, Spin, Button, Input, message } from "antd";
import {
  CheckCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
} from "@ant-design/icons";
import { autopilotSteer } from "../services/chat";

const { Text } = Typography;
const { TextArea } = Input;

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
    | "error"
    | "paused";
  /** Summary from the final autopilot_complete event. */
  summary?: string;
  /** Error message if status is "error". */
  errorMessage?: string;
  /** Reason for pause when status is "paused". */
  pauseReason?: string;
  /** Accumulated per-turn findings. */
  findings?: Array<{ turn: number; summary: string }>;
  /** Cumulative token usage. */
  cumulativeTokens?: { tokensIn: number; tokensOut: number };
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
  /** Session ID for API calls (pause/steer). */
  sessionId?: string;
}

export function AutopilotPanel({ state, onStop, sessionId }: AutopilotPanelProps) {
  const { currentTurn, maxTurns, status, summary, findings, cumulativeTokens, errorMessage, pauseReason } = state;
  const [steerInput, setSteerInput] = useState("");
  const [steering, setSteering] = useState(false);

  // Don't render when idle.
  if (status === "idle") {
    return null;
  }
  const isFinished = status === "complete" || status === "max_turns" || status === "error";
  const isPaused = status === "paused";

  const progressPercent = maxTurns > 0
    ? Math.min(Math.round((currentTurn / maxTurns) * 100), 100)
    : 0;

  const handleSteer = async () => {
    if (!sessionId || !steerInput.trim()) return;
    setSteering(true);
    try {
      await autopilotSteer(sessionId, steerInput.trim());
      message.success("Steering instruction sent — resuming autopilot");
      setSteerInput("");
    } catch {
      message.error("Failed to send steering instruction");
    } finally {
      setSteering(false);
    }
  };

  return (
    <div style={{ padding: "0 16px 12px" }}>
      <Card
        size="small"
        style={{
          borderLeft: isPaused ? "4px solid #faad14" : "4px solid #1677ff",
          background: isPaused ? "#fffbe6" : "#f6f8ff",
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
            ) : isPaused ? (
              <PauseCircleOutlined style={{ color: "#faad14", fontSize: 16 }} />
            ) : (
              <Spin
                indicator={<LoadingOutlined style={{ fontSize: 16, color: "#1677ff" }} />}
              />
            )}
            <Text strong style={{ fontSize: 14 }}>
              {isFinished
                ? "Autopilot finished"
                : isPaused
                ? "Autopilot paused — waiting for your input"
                : `Working on turn ${currentTurn} of ${maxTurns}…`}
            </Text>
          </Space>
          {onStop && !isFinished && (
            <Tag
              color="error"
              style={{ cursor: "pointer" }}
              onClick={onStop}
            >
              {isPaused ? "Cancel" : "Stop"}
            </Tag>
          )}
        </div>

        {/* Progress bar */}
        <Progress
          percent={isFinished ? 100 : progressPercent}
          size="small"
          status={status === "error" ? "exception" : isPaused ? "normal" : isFinished ? "success" : "active"}
          strokeColor={status === "max_turns" || isPaused ? "#faad14" : undefined}
          style={{ marginBottom: 4 }}
        />

        {/* Token counter */}
        {cumulativeTokens && (cumulativeTokens.tokensIn > 0 || cumulativeTokens.tokensOut > 0) && (
          <Text style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>
            Tokens: {cumulativeTokens.tokensIn + cumulativeTokens.tokensOut} total
            ({cumulativeTokens.tokensIn} in / {cumulativeTokens.tokensOut} out)
          </Text>
        )}

        {/* Pause reason + steering input */}
        {isPaused && (
          <div style={{ marginTop: 8 }}>
            {pauseReason && (
              <Text style={{ fontSize: 13, display: "block", marginBottom: 8, fontStyle: "italic" }}>
                Agent says: {pauseReason}
              </Text>
            )}
            <Space direction="vertical" style={{ width: "100%" }} size={4}>
              <TextArea
                placeholder="Enter a steering instruction for the agent…"
                value={steerInput}
                onChange={(e) => setSteerInput(e.target.value)}
                autoSize={{ minRows: 1, maxRows: 3 }}
              />
              <Space>
                <Button
                  type="primary"
                  size="small"
                  onClick={handleSteer}
                  loading={steering}
                  disabled={!steerInput.trim()}
                >
                  Resume with instruction
                </Button>
              </Space>
            </Space>
          </div>
        )}

        {/* Result message */}
        {status === "max_turns" && (
          <Text type="warning" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
            Reached maximum of {maxTurns} turns — showing partial results.
          </Text>
        )}
        {status === "error" && errorMessage && (
          <Text type="danger" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
            Error: {errorMessage}
          </Text>
        )}

        {/* Findings timeline (collapsible) */}
        {findings && findings.length > 0 && (
          <Collapse
            ghost
            size="small"
            style={{ marginTop: 4 }}
            items={[
              {
                key: "findings",
                label: (
                  <Text style={{ fontSize: 12 }}>
                    Accumulated Findings ({findings.length})
                  </Text>
                ),
                children: (
                  <div style={{ maxHeight: 200, overflowY: "auto" }}>
                    {findings.map((f, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "4px 8px",
                          marginBottom: 4,
                          background: "#fff",
                          borderRadius: 4,
                          borderLeft: "3px solid #1677ff",
                        }}
                      >
                        <Text strong style={{ fontSize: 12 }}>
                          Turn {f.turn}
                        </Text>
                        <Text style={{ fontSize: 12, display: "block", whiteSpace: "pre-wrap" }}>
                          {f.summary?.slice(0, 300)}
                        </Text>
                      </div>
                    ))}
                  </div>
                ),
              },
            ]}
          />
        )}

        {/* Summary (shown after completion) */}
        {summary && isFinished && (
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
