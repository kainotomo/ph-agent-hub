// =============================================================================
// PH Agent Hub — ProcessSteps
// =============================================================================
// Renders a single process fold per assistant turn.
//
// Fold header: summarizeProcess(steps) with chevron.
// Body: ordered step rows (reasoning, tool_call, tool_result, text).
//
// Props:
//   steps: ProcessStep[] — the ordered process steps for this turn.
//   streaming?: boolean — true while the turn is streaming.
//
// Open state:
//   useState(!!streaming) so the fold is open while streaming.
//   useEffect collapses it when streaming flips to false at turn completion.
//
// Step rows:
//   Native <button aria-expanded> header + conditionally mounted body.
//   Per-row open state as a Set<string> of step.key.
//
// Parallel batch:
//   parallel rows render "⚡ Running {n} tools in parallel…" header
//   followed by member rows with paddingLeft: 12.
//   Non-parallel batches render member rows flat.
// =============================================================================

import { useState, useEffect, useId } from "react";
import {
  DownOutlined,
  UpOutlined,
  BulbOutlined,
  ToolOutlined,
  CheckOutlined,
  CloseOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { Tag } from "antd";
import type { ProcessStep, ProcessRowBatch } from "../utils/buildSteps";
import { groupProcessRows, reasoningSummary } from "../utils/buildSteps";

// ---------------------------------------------------------------------------
// Internal: StepRow
// ---------------------------------------------------------------------------

function StepRow({ step }: { step: ProcessStep }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((v) => !v);

  const header = (
    <button
      type="button"
      aria-expanded={open}
      onClick={toggle}
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
        {open ? <UpOutlined /> : <DownOutlined />}
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
            title={step.text}
          >
            {reasoningSummary(step.text || "", false)}
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
        </>
      )}
      {step.kind === "text" && (
        <>
          <FileTextOutlined style={{ color: "#888", fontSize: 13 }} />
          <span style={{ color: "#666", fontSize: 12 }}>
            {step.text?.split("\n")[0]?.slice(0, 60) || "(text)"}
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
          {step.kind === "reasoning" && (
            <div
              style={{
                fontSize: 12,
                whiteSpace: "pre-wrap",
                margin: 0,
                color: "#531dab",
              }}
            >
              {step.text}
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
              {typeof step.output === "string"
                ? step.output
                : step.output !== undefined
                  ? JSON.stringify(step.output)
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
              {step.text}
            </div>
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
  streaming?: boolean;
}

export function ProcessSteps({ steps, streaming }: ProcessStepsProps) {
  const [open, setOpen] = useState(!!streaming);
  const bodyId = useId();

  useEffect(() => {
    setOpen(!!streaming);
  }, [streaming]);

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
        {open ? <UpOutlined style={{ fontSize: 10 }} /> : <DownOutlined style={{ fontSize: 10 }} />}
        <span>{rows.length === 1 && rows[0].type === "step" ? reasoningSummary(rows[0].step.text || "", false) : ""}</span>
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
              return <StepRow key={row.step.key} step={row.step} />;
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
                  <StepRow key={s.key} step={s} />
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
