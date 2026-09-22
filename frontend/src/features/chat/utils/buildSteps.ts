/**
 * PH Agent Hub — buildSteps utility
 *
 * Pure functions that parse message content into ordered process steps
 * and an answer string.  Used by MessageBubble (persisted) and
 * ChatWindow (live streaming) so both paths converge on the same
 * ordering rule.
 *
 * Content part types produced by the backend:
 *   text | reasoning | function_call | function_result | metrics
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ContentPart {
  type: string;
  text?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  output?: unknown;
  is_error?: boolean;
  id?: string;
  batch_id?: string;
  call_id?: string;
  [k: string]: unknown;
}

export function parseContent(content: unknown): ContentPart[] {
  if (!content) return [];
  if (Array.isArray(content)) {
    return content as ContentPart[];
  }
  if (typeof content === "string") {
    return [{ type: "text", text: content }];
  }
  if (typeof content === "object" && content !== null) {
    return [content as ContentPart];
  }
  return [];
}

export type StepKind = "reasoning" | "tool_call" | "tool_result" | "text";

export interface ProcessStep {
  kind: StepKind;
  key: string;
  index: number;
  text?: string;
  name?: string;
  args?: Record<string, unknown>;
  output?: unknown;
  isError?: boolean;
  batchId?: string | null;
}

export interface TurnSteps {
  process: ProcessStep[];
  answer: string;
}

// ---------------------------------------------------------------------------
// groupProcessRows
// ---------------------------------------------------------------------------

export interface ProcessRow {
  type: "step";
  step: ProcessStep;
}

export interface ProcessRowBatch {
  type: "toolBatch";
  batchId: string | null;
  parallel: boolean;
  steps: ProcessStep[];
}

export function groupProcessRows(steps: ProcessStep[]): (ProcessRow | ProcessRowBatch)[] {
  const rows: (ProcessRow | ProcessRowBatch)[] = [];
  const toolBuffer: ProcessStep[] = [];

  function flushBuffer(): void {
    if (toolBuffer.length === 0) return;
    // Split by batch_id changes
    const groups: { batchId: string | null; items: ProcessStep[] }[] = [];
    for (const step of toolBuffer) {
      const bid = step.batchId || null;
      const last = groups[groups.length - 1];
      if (last && last.batchId === bid) {
        last.items.push(step);
      } else {
        groups.push({ batchId: bid, items: [step] });
      }
    }
    for (const group of groups) {
      const parallel = group.batchId !== null && group.items.length > 1;
      rows.push({
        type: "toolBatch",
        batchId: group.batchId,
        parallel,
        steps: group.items,
      });
    }
    toolBuffer.length = 0;
  }

  for (const step of steps) {
    if (step.kind === "tool_call" || step.kind === "tool_result") {
      toolBuffer.push(step);
    } else {
      flushBuffer();
      rows.push({ type: "step", step });
    }
  }
  flushBuffer();
  return rows;
}

// ---------------------------------------------------------------------------
// summarizeProcess
// ---------------------------------------------------------------------------

export function summarizeProcess(steps: ProcessStep[]): string {
  const reasoningCount = steps.filter((s) => s.kind === "reasoning").length;
  const callCount = steps.filter((s) => s.kind === "tool_call").length;
  const resultCount = steps.filter((s) => s.kind === "tool_result").length;
  const textCount = steps.filter((s) => s.kind === "text").length;
  const total = reasoningCount + callCount + resultCount + textCount;

  if (reasoningCount > 0 && callCount > 0) {
    return `Thought for a while - ${callCount} tool call${callCount === 1 ? "" : "s"}`;
  }
  if (reasoningCount > 0) {
    return "Thought for a while";
  }
  if (callCount > 0) {
    return `${callCount} tool call${callCount === 1 ? "" : "s"}`;
  }
  return `${total} step${total === 1 ? "" : "s"}`;
}

// ---------------------------------------------------------------------------
// reasoningSummary
// ---------------------------------------------------------------------------

export function reasoningSummary(text: string, streaming: boolean): string {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0);
  if (lines.length === 0) return "";
  return streaming ? lines[lines.length - 1] : lines[0];
}

// ---------------------------------------------------------------------------
// buildSteps
// ---------------------------------------------------------------------------

export function buildSteps(content: unknown): TurnSteps {
  const parts = parseContent(content);

  // Find the last "text" part — that's the answer
  let answerIndex = -1;
  for (let i = parts.length - 1; i >= 0; i--) {
    if (parts[i].type === "text") {
      answerIndex = i;
      break;
    }
  }

  const answer = answerIndex >= 0 ? (parts[answerIndex].text ?? "") : "";

  // Scan indices [0, answerIndex) (or whole array when answerIndex < 0)
  const scanEnd = answerIndex >= 0 ? answerIndex : parts.length;
  const process: ProcessStep[] = [];

  for (let i = 0; i < scanEnd; i++) {
    const part = parts[i];
    const kind = part.type === "reasoning"
      ? "reasoning"
      : part.type === "function_call"
        ? "tool_call"
        : part.type === "function_result"
          ? "tool_result"
          : part.type === "text"
            ? "text"
            : null;

    if (kind === null) continue; // metrics or unknown type → skip

    // Skip whitespace-only text/reasoning
    if ((kind === "reasoning" || kind === "text") && !part.text?.trim()) continue;

    process.push({
      kind,
      key: `${kind}:${i}`,
      index: i,
      text: kind === "reasoning" || kind === "text" ? part.text : undefined,
      name: kind === "tool_call" || kind === "tool_result" ? part.name : undefined,
      args: kind === "tool_call" ? part.arguments : undefined,
      output: kind === "tool_result" ? part.output : undefined,
      isError: kind === "tool_result" ? !!part.is_error : undefined,
      batchId: part.batch_id ?? null,
    });
  }

  return { process, answer };
}
