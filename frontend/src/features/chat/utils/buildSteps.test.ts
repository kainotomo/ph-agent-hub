import { describe, it, expect } from "vitest";
import {
  buildSteps,
  parseContent,
  groupProcessRows,
  summarizeProcess,
  reasoningSummary,
  type ProcessRowBatch,
} from "./buildSteps";

// ---------------------------------------------------------------------------
// parseContent
// ---------------------------------------------------------------------------

describe("parseContent", () => {
  it("returns empty array for null", () => {
    expect(parseContent(null)).toEqual([]);
  });

  it("returns empty array for undefined", () => {
    expect(parseContent(undefined)).toEqual([]);
  });

  it("returns empty array for empty string", () => {
    expect(parseContent("")).toEqual([]);
  });

  it("wraps a string as a text part", () => {
    expect(parseContent("hello")).toEqual([{ type: "text", text: "hello" }]);
  });

  it("returns array as-is", () => {
    const parts = [{ type: "reasoning", text: "thinking" }];
    expect(parseContent(parts)).toEqual(parts);
  });

  it("wraps a single object as an array", () => {
    const obj = { type: "text", text: "hi" };
    expect(parseContent(obj)).toEqual([obj]);
  });
});

// ---------------------------------------------------------------------------
// buildSteps
// ---------------------------------------------------------------------------

describe("buildSteps", () => {
  it("extracts answer from last text part", () => {
    const result = buildSteps([
      { type: "reasoning", text: "Let me think..." },
      { type: "text", text: "The answer is 42." },
    ]);
    expect(result.answer).toBe("The answer is 42.");
    expect(result.process).toHaveLength(1);
    expect(result.process[0].kind).toBe("reasoning");
  });

  it("handles tool calls in order", () => {
    const result = buildSteps([
      { type: "reasoning", text: "I'll search." },
      { type: "function_call", name: "web_search", arguments: { query: "test" }, call_id: "c1", batch_id: null },
      { type: "function_result", name: "web_search", output: "Results found", call_id: "c1", batch_id: null },
      { type: "text", text: "Done." },
    ]);
    expect(result.answer).toBe("Done.");
    expect(result.process).toHaveLength(3);
    expect(result.process[0].kind).toBe("reasoning");
    expect(result.process[1].kind).toBe("tool_call");
    expect(result.process[1].name).toBe("web_search");
    expect(result.process[2].kind).toBe("tool_result");
    expect(result.process[2].output).toBe("Results found");
  });

  it("skips whitespace-only reasoning", () => {
    const result = buildSteps([
      { type: "reasoning", text: "   " },
      { type: "text", text: "Answer" },
    ]);
    expect(result.process).toHaveLength(0);
    expect(result.answer).toBe("Answer");
  });

  // ---- Issue #539: persisted content arrives as summary-only projections ----

  it("keeps summary-only reasoning (no text) as a step", () => {
    // list_messages strips reasoning.text and returns {chars, summary}.
    const result = buildSteps([
      { type: "reasoning", chars: 12_345, summary: "First line of thinking" },
      { type: "text", text: "Answer" },
    ]);
    expect(result.process).toHaveLength(1);
    expect(result.process[0].kind).toBe("reasoning");
    expect(result.process[0].summary).toBe("First line of thinking");
    expect(result.process[0].chars).toBe(12_345);
  });

  it("keeps reasoning-only content so the process fold renders", () => {
    const result = buildSteps([
      { type: "reasoning", chars: 42, summary: "Thinking only" },
    ]);
    expect(result.process.map((s) => s.kind)).toEqual(["reasoning"]);
    expect(result.answer).toBe("");
  });

  it("reads snake_case output_summary/output_chars for tool results", () => {
    const result = buildSteps([
      { type: "function_call", name: "search", arguments: { q: "x" }, id: "c1" },
      {
        type: "function_result",
        name: "search",
        is_error: true,
        output_chars: 42,
        output_summary: "some output",
      },
      { type: "text", text: "Done." },
    ]);
    const toolResult = result.process.find((s) => s.kind === "tool_result")!;
    expect(toolResult.name).toBe("search");
    expect(toolResult.isError).toBe(true);
    expect(toolResult.outputSummary).toBe("some output");
    expect(toolResult.outputChars).toBe(42);
    expect(toolResult.output).toBeUndefined();
  });

  it("still skips summary-only reasoning when the summary is blank", () => {
    const result = buildSteps([
      { type: "reasoning", chars: 0, summary: "   " },
      { type: "text", text: "Answer" },
    ]);
    expect(result.process).toHaveLength(0);
  });

  it("skips whitespace-only text before answer", () => {
    const result = buildSteps([
      { type: "text", text: "   " },
      { type: "text", text: "Answer" },
    ]);
    expect(result.process).toHaveLength(0);
    expect(result.answer).toBe("Answer");
  });

  it("handles empty content", () => {
    const result = buildSteps([]);
    expect(result.process).toHaveLength(0);
    expect(result.answer).toBe("");
  });

  it("handles string content", () => {
    const result = buildSteps("just text");
    expect(result.process).toHaveLength(0);
    expect(result.answer).toBe("just text");
  });

  it("handles no text part — answer is empty", () => {
    const result = buildSteps([
      { type: "reasoning", text: "Thinking..." },
      { type: "function_call", name: "calc", arguments: { expr: "1+1" }, call_id: "c1", batch_id: null },
    ]);
    expect(result.answer).toBe("");
    expect(result.process).toHaveLength(2);
  });

  it("preserves batch_id on steps", () => {
    const result = buildSteps([
      { type: "function_call", name: "a", arguments: {}, call_id: "c1", batch_id: "b1" },
      { type: "function_call", name: "b", arguments: {}, call_id: "c2", batch_id: "b1" },
      { type: "function_result", name: "a", output: "ok", call_id: "c1", batch_id: "b1" },
      { type: "function_result", name: "b", output: "ok", call_id: "c2", batch_id: "b1" },
      { type: "text", text: "Done." },
    ]);
    expect(result.process[0].batchId).toBe("b1");
    expect(result.process[1].batchId).toBe("b1");
  });
});

// ---------------------------------------------------------------------------
// groupProcessRows
// ---------------------------------------------------------------------------

describe("groupProcessRows", () => {
  it("returns individual steps for non-tool kinds", () => {
    const steps = [
      { kind: "reasoning" as const, key: "reasoning:0", index: 0, text: "thinking" },
      { kind: "text" as const, key: "text:1", index: 1, text: "answer" },
    ];
    const rows = groupProcessRows(steps);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({ type: "step", step: steps[0] });
    expect(rows[1]).toEqual({ type: "step", step: steps[1] });
  });

  it("groups consecutive tool_call/tool_result into a batch", () => {
    const steps = [
      { kind: "tool_call" as const, key: "tool_call:0", index: 0, name: "a", args: {}, batchId: null },
      { kind: "tool_result" as const, key: "tool_result:1", index: 1, name: "a", output: "ok", batchId: null },
    ];
    const rows = groupProcessRows(steps);
    expect(rows).toHaveLength(1);
    const batch = rows[0] as ProcessRowBatch;
    expect(batch.type).toBe("toolBatch");
    expect(batch.batchId).toBeNull();
    expect(batch.parallel).toBe(false);
    expect(batch.steps).toHaveLength(2);
  });

  it("marks parallel batch when batch_id is set and multiple items", () => {
    const steps = [
      { kind: "tool_call" as const, key: "tool_call:0", index: 0, name: "a", args: {}, batchId: "b1" },
      { kind: "tool_call" as const, key: "tool_call:1", index: 1, name: "b", args: {}, batchId: "b1" },
      { kind: "tool_result" as const, key: "tool_result:2", index: 2, name: "a", output: "ok", batchId: "b1" },
      { kind: "tool_result" as const, key: "tool_result:3", index: 3, name: "b", output: "ok", batchId: "b1" },
    ];
    const rows = groupProcessRows(steps);
    expect(rows).toHaveLength(1);
    const batch = rows[0] as ProcessRowBatch;
    expect(batch.type).toBe("toolBatch");
    expect(batch.parallel).toBe(true);
    expect(batch.steps).toHaveLength(4);
  });

  it("splits tool buffer at non-tool steps", () => {
    const steps = [
      { kind: "tool_call" as const, key: "tool_call:0", index: 0, name: "a", args: {}, batchId: null },
      { kind: "reasoning" as const, key: "reasoning:1", index: 1, text: "middle" },
      { kind: "tool_result" as const, key: "tool_result:2", index: 2, name: "a", output: "ok", batchId: null },
    ];
    const rows = groupProcessRows(steps);
    expect(rows).toHaveLength(3);
    const batch0 = rows[0] as ProcessRowBatch;
    expect(batch0.type).toBe("toolBatch");
    expect(batch0.batchId).toBeNull();
    expect(batch0.parallel).toBe(false);
    expect(batch0.steps).toHaveLength(1);
    expect(rows[1]).toEqual({ type: "step", step: steps[1] });
    const batch2 = rows[2] as ProcessRowBatch;
    expect(batch2.type).toBe("toolBatch");
    expect(batch2.batchId).toBeNull();
    expect(batch2.parallel).toBe(false);
    expect(batch2.steps).toHaveLength(1);
  });

  it("splits by batch_id changes", () => {
    const steps = [
      { kind: "tool_call" as const, key: "tool_call:0", index: 0, name: "a", args: {}, batchId: "b1" },
      { kind: "tool_call" as const, key: "tool_call:1", index: 1, name: "b", args: {}, batchId: "b2" },
    ];
    const rows = groupProcessRows(steps);
    expect(rows).toHaveLength(2);
    const batch0 = rows[0] as ProcessRowBatch;
    const batch1 = rows[1] as ProcessRowBatch;
    expect(batch0.batchId).toBe("b1");
    expect(batch1.batchId).toBe("b2");
  });
});

// ---------------------------------------------------------------------------
// summarizeProcess
// ---------------------------------------------------------------------------

describe("summarizeProcess", () => {
  it("returns 'Thought for a while' for reasoning only", () => {
    expect(summarizeProcess([{ kind: "reasoning" as const, key: "r:0", index: 0 }])).toBe("Thought for a while");
  });

  it("returns 'Thought for a while - N tool calls' for reasoning + tools", () => {
    const steps = [
      { kind: "reasoning" as const, key: "r:0", index: 0 },
      { kind: "tool_call" as const, key: "tc:1", index: 1 },
      { kind: "tool_result" as const, key: "tr:2", index: 2 },
    ];
    expect(summarizeProcess(steps)).toBe("Thought for a while - 1 tool call");
  });

  it("returns 'N tool calls' for tools only", () => {
    const steps = [
      { kind: "tool_call" as const, key: "tc:0", index: 0 },
      { kind: "tool_result" as const, key: "tr:1", index: 1 },
    ];
    // callCount only counts tool_call entries (1), not tool_result
    expect(summarizeProcess(steps)).toBe("1 tool call");
  });

  it("returns 'Thought for a while' for reasoning + text (no tools)", () => {
    const steps = [
      { kind: "reasoning" as const, key: "r:0", index: 0 },
      { kind: "text" as const, key: "t:1", index: 1 },
    ];
    // reasoningCount > 0 && callCount === 0 → "Thought for a while"
    expect(summarizeProcess(steps)).toBe("Thought for a while");
  });
});

// ---------------------------------------------------------------------------
// reasoningSummary
// ---------------------------------------------------------------------------

describe("reasoningSummary", () => {
  it("returns first line for non-streaming", () => {
    expect(reasoningSummary("Line 1\nLine 2\nLine 3", false)).toBe("Line 1");
  });

  it("returns last line for streaming", () => {
    expect(reasoningSummary("Line 1\nLine 2\nLine 3", true)).toBe("Line 3");
  });

  it("handles single line", () => {
    expect(reasoningSummary("Only line", false)).toBe("Only line");
    expect(reasoningSummary("Only line", true)).toBe("Only line");
  });

  it("handles empty string", () => {
    expect(reasoningSummary("", false)).toBe("");
    expect(reasoningSummary("", true)).toBe("");
  });

  it("trims whitespace-only lines", () => {
    expect(reasoningSummary("  \n  \nActual text", false)).toBe("Actual text");
  });
});
