// =============================================================================
// PH Agent Hub — ProcessSteps
// =============================================================================
// Renders a single process fold per assistant turn.
//
// Fold header: summarizeProcess(steps) with chevron.
// Body: ordered step rows (reasoning, tool_call, tool_result, text).
//
// Lazy-fetch (Issue #539):
//   When content is summary-only (no full text/output), StepRow fetches the
//   full body on expand via GET /session/…/message/…/step/:index.
//
// Props:
//   steps: ProcessStep[] — the ordered process steps for this turn.
//   sessionId: string — session id for lazy fetch.
//   messageId: string — message id for lazy fetch.
//   streaming?: boolean — true while the turn is streaming.
//
// Open state:
//   Always starts closed; user toggles with click.
//   Arrow shows → when closed, ↓ when open.
//
// Step rows:
//   Native <button aria-expanded> header + conditionally mounted body.
//   Per-row open state + separate lazy-fetch state.
//
// Parallel batch:
//   parallel rows render "⚡ Running {n} tools in parallel…" header
//   followed by member rows with paddingLeft: 12.
//   Non-parallel batches render member rows flat.
// =============================================================================

import { useState } from "react";
import {
  CaretDownOutlined,
  CaretRightOutlined,
  BulbOutlined,
  ToolOutlined,
  CheckOutlined,
  CloseOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { Tag, Spin, Button } from "antd";
import type { ProcessStep, ProcessRowBatch } from "../utils/buildSteps";
import { groupProcessRows, reasoningSummary, summarizeProcess } from "../utils/buildSteps";
import { getMessageStep } from "../services/chat";
import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Internal: StepRow (lazy-fetch)
// ---------------------------------------------------------------------------

function StepRow({
  step,
  sessionId,
  messageId,
}: {
  step: ProcessStep;
  sessionId: string;
  messageId: string;
}) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((v) => !v);

  // Headers always show summary preview from the compact content.

  // Lazy-fetch full body on expand.
  // We only need to fetch when the part is summary-only (no text/output
  // already present) AND the step is expanded.
  const needsFetch =
    (step.kind === "reasoning" && !step.text) ||
    (step.kind === "tool_result" && step.output === undefined) ||
    (step.kind === "text" && !step.text);

  const { data: fullStep, isLoading, isError, refetch } = useQuery({
    queryKey: ["message-step", messageId, step.index],
    queryFn: () => getMessageStep(sessionId, messageId, step.index),
    enabled: needsFetch && open,
    staleTime: 10_000, // avoid refetching within 10s
  });

  // Merge full body into step for rendering.
  const displayText = fullStep?.full_text ?? step.text ?? step.summary;
  const displayOutput = fullStep?.full_output ?? step.output;
  // Shown when the body is not (yet) available — keeps the row informative
  // even if the lazy fetch fails.
  const fallbackText =
    step.kind === "tool_result"
      ? step.outputSummary ??
        (step.outputChars ? `${step.outputChars} chars` : "(no output)")
      : displayText || "(no content)";

  const header = (
    <button
      type="button"
      aria-expanded={open}
      onClick={toggle}
      data-testid={`step-row-${step.kind}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: "100%",
        textAlign: "left",
        border: "none",
        background: "transparent",
        padding: "4px 0",
        cursor: "pointer",
        fontSize: 13,
        color: "#333",
        fontFamily: "inherit",
      }}
    >
      <span style={{ fontSize: 10, color: "#999", flexShrink: 0 }}>
        {open ? <CaretDownOutlined /> : <CaretRightOutlined />}
      </span>
      {step.kind === "reasoning" && (
        <>
          <BulbOutlined style={{ color: "#722ed1", fontSize: 13 }} />
          <span style={{ color: "#531dab", fontWeight: 500 }}>Thinking</span>
          <span
            style={{
              color: "#888",
              fontSize: 12,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              marginLeft: 4,
              maxWidth: 300,
            }}
            title={displayText}
          >
            {step.summary
              ? step.summary
              : step.chars
                ? `${step.chars} chars`
                : reasoningSummary(step.text || "", false)}
          </span>
        </>
      )}
      {step.kind === "tool_call" && (
        <>
          <ToolOutlined style={{ color: "#1677ff", fontSize: 13 }} />
          <Tag color="blue" style={{ margin: 0, fontSize: 12 }}>
            {step.name || "tool"}
          </Tag>
        </>
      )}
      {step.kind === "tool_result" && (
        <>
          <ToolOutlined style={{ color: "#52c41a", fontSize: 13 }} />
          <Tag
            color={step.isError ? "red" : "green"}
            style={{ margin: 0, fontSize: 12 }}
          >
            {step.isError ? <CloseOutlined /> : <CheckOutlined />}{" "}
            {step.name || "result"}
          </Tag>
          {(step.outputSummary || step.outputChars) && (
            <span
              style={{
                color: "#888",
                fontSize: 12,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                marginLeft: 4,
                maxWidth: 200,
              }}
              title={step.outputSummary}
            >
              {step.outputSummary || `${step.outputChars} chars`}
            </span>
          )}
        </>
      )}
      {step.kind === "text" && (
        <>
          <FileTextOutlined style={{ color: "#888", fontSize: 13 }} />
          <span style={{ color: "#666", fontSize: 12 }}>
            {step.summary ||
              step.text?.split("\n")[0]?.slice(0, 60) ||
              "(text)"}
          </span>
        </>
      )}
    </button>
  );

  return (
    <div style={{ marginBottom: 4 }}>
      {header}
      {open && (
        <div
          style={{
            marginLeft: 20,
            marginTop: 2,
            maxHeight: 300,
            overflow: "auto",
            borderLeft: "3px solid #d3adf7",
            padding: "4px 12px",
          }}
        >
          {isLoading ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontSize: 12,
                color: "#888",
              }}
            >
              <Spin size="small" /> Loading…
            </div>
          ) : isError ? (
            <div data-testid="step-fetch-error">
              <div
                style={{
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  margin: 0,
                  color: "#999",
                }}
              >
                {fallbackText}
              </div>
              <Button
                type="link"
                size="small"
                style={{ padding: 0, fontSize: 12 }}
                onClick={() => refetch()}
              >
                Retry
              </Button>
            </div>
          ) : (
            <>
              {step.kind === "reasoning" && (
                <div
                  style={{
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    margin: 0,
                    color: "#531dab",
                  }}
                >
                  {displayText}
                </div>
              )}
              {step.kind === "tool_call" && (
                <pre
                  style={{
                    fontSize: 12,
                    margin: 0,
                    maxHeight: 200,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    color: "#333",
                  }}
                >
                  {step.args
                    ? JSON.stringify(step.args, null, 2)
                    : "(no arguments)"}
                </pre>
              )}
              {step.kind === "tool_result" && (
                <div
                  style={{
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    margin: 0,
                    color: "#333",
                    maxHeight: 200,
                    overflow: "auto",
                  }}
                >
                  {displayOutput !== undefined
                    ? typeof displayOutput === "string"
                      ? displayOutput
                      : JSON.stringify(displayOutput)
                    : "(no output)"}
                </div>
              )}
              {step.kind === "text" && (
                <div
                  style={{
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    margin: 0,
                    color: "#333",
                    maxHeight: 200,
                    overflow: "auto",
                  }}
                >
                  {displayText}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ProcessStepsProps {
  steps: ProcessStep[];
  sessionId: string;
  messageId: string;
}

export function ProcessSteps({
  steps,
  sessionId,
  messageId,
}: ProcessStepsProps) {
  const [open, setOpen] = useState(false);
  const bodyId = `process-fold-${messageId}`;

  if (steps.length === 0) return null;

  const rows = groupProcessRows(steps);

  return (
    <div style={{ marginBottom: 8 }}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          textAlign: "left",
          border: "none",
          background: "transparent",
          padding: "4px 0",
          cursor: "pointer",
          fontSize: 12,
          color: "#888",
          fontFamily: "inherit",
        }}
      >
        {open ? <CaretDownOutlined style={{ fontSize: 10 }} /> : <CaretRightOutlined style={{ fontSize: 10 }} />}
        <span>
        {rows.length === 1 && rows[0].type === "step"
          ? reasoningSummary(rows[0].step.text || rows[0].step.summary || "", false)
          : summarizeProcess(steps)}
      </span>
        {rows.length > 1 && (
          <span style={{ marginLeft: 4, color: "#aaa" }}>
            {rows.length} step{rows.length === 1 ? "" : "s"}
          </span>
        )}
      </button>
      {open && (
        <div id={bodyId} style={{ paddingLeft: 16 }}>
          {rows.map((row, i) => {
            if (row.type === "step") {
              return (
                <StepRow
                  key={row.step.key}
                  step={row.step}
                  sessionId={sessionId}
                  messageId={messageId}
                />
              );
            }
            const batch = row as ProcessRowBatch;
            return (
              <div key={i}>
                {batch.parallel && (
                  <div
                    style={{
                      fontSize: 11,
                      color: "#1677ff",
                      marginBottom: 4,
                      fontWeight: 500,
                    }}
                  >
                    ⚡ Running {batch.steps.length} tools in parallel…
                  </div>
                )}
                {batch.steps.map((s) => (
                  <StepRow
                    key={s.key}
                    step={s}
                    sessionId={sessionId}
                    messageId={messageId}
                  />
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default ProcessSteps;
