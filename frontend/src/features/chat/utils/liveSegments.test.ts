import { describe, it, expect } from "vitest";
import {
  appendReasoningDelta,
  appendToolStart,
  appendToolResult,
  buildLiveContent,
} from "./liveSegments";

// ---------------------------------------------------------------------------
// appendReasoningDelta
// ---------------------------------------------------------------------------

describe("appendReasoningDelta", () => {
  it("appends a new reasoning part when segments is empty", () => {
    const result = appendReasoningDelta([], "hello");
    expect(result).toEqual([{ type: "reasoning", text: "hello" }]);
  });

  it("appends a new reasoning part when last is not reasoning", () => {
    const segments = [{ type: "text", text: "answer" }];
    const result = appendReasoningDelta(segments, "thinking");
    expect(result).toHaveLength(2);
    expect(result[1]).toEqual({ type: "reasoning", text: "thinking" });
  });

  it("extends the trailing reasoning part", () => {
    const segments = [{ type: "reasoning", text: "part1" }];
    const result = appendReasoningDelta(segments, "part2");
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({ type: "reasoning", text: "part1part2" });
  });

  it("does not mutate the input array", () => {
    const segments: any[] = [{ type: "reasoning", text: "hello" }];
    appendReasoningDelta(segments, " world");
    expect(segments).toHaveLength(1);
    expect(segments[0].text).toBe("hello");
  });

  it("handles empty delta", () => {
    const segments = [{ type: "reasoning", text: "hello" }];
    const result = appendReasoningDelta(segments, "");
    expect(result[0].text).toBe("hello");
  });
});

// ---------------------------------------------------------------------------
// appendToolStart
// ---------------------------------------------------------------------------

describe("appendToolStart", () => {
  it("appends a function_call part", () => {
    const result = appendToolStart([], {
      tool_name: "web_search",
      tool_call_id: "c1",
      arguments: { query: "test" },
    });
    expect(result).toEqual([
      {
        type: "function_call",
        name: "web_search",
        arguments: { query: "test" },
        call_id: "c1",
        batch_id: undefined,
      },
    ]);
  });

  it("preserves existing segments", () => {
    const segments = [{ type: "reasoning", text: "thinking" }];
    const result = appendToolStart(segments, { tool_name: "calc" });
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual({ type: "reasoning", text: "thinking" });
    expect(result[1].type).toBe("function_call");
  });

  it("handles missing optional fields", () => {
    const result = appendToolStart([], { tool_name: "noop" });
    expect(result[0].call_id).toBeUndefined();
    expect(result[0].arguments).toBeUndefined();
    expect(result[0].batch_id).toBeUndefined();
  });

  it("does not mutate the input array", () => {
    const segments: any[] = [{ type: "text", text: "hi" }];
    appendToolStart(segments, { tool_name: "test" });
    expect(segments).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// appendToolResult
// ---------------------------------------------------------------------------

describe("appendToolResult", () => {
  it("appends a function_result part", () => {
    const result = appendToolResult([], {
      tool_name: "web_search",
      tool_call_id: "c1",
      result_summary: "Results found",
    });
    expect(result).toEqual([
      {
        type: "function_result",
        name: "web_search",
        output: "Results found",
        is_error: undefined,
        call_id: "c1",
        batch_id: undefined,
      },
    ]);
  });

  it("marks error when success is false", () => {
    const result = appendToolResult([], {
      tool_name: "calc",
      success: false,
      result_summary: "Error: division by zero",
    });
    expect(result[0].is_error).toBe(true);
  });

  it("does not mark error when success is true", () => {
    const result = appendToolResult([], {
      tool_name: "calc",
      success: true,
      result_summary: "42",
    });
    expect(result[0].is_error).toBeUndefined();
  });

  it("preserves existing segments", () => {
    const segments = [{ type: "reasoning", text: "thinking" }];
    const result = appendToolResult(segments, { tool_name: "calc", tool_call_id: "c1" });
    expect(result).toHaveLength(2);
  });

  it("handles missing optional fields", () => {
    const result = appendToolResult([], { tool_name: "noop" });
    expect(result[0].call_id).toBeUndefined();
    expect(result[0].output).toBeUndefined();
    expect(result[0].is_error).toBeUndefined();
    expect(result[0].batch_id).toBeUndefined();
  });

  it("does not mutate the input array", () => {
    const segments: any[] = [{ type: "text", text: "hi" }];
    appendToolResult(segments, { tool_name: "test" });
    expect(segments).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// buildLiveContent
// ---------------------------------------------------------------------------

describe("buildLiveContent", () => {
  it("appends text as final part when present", () => {
    const segments = [
      { type: "reasoning", text: "thinking" },
      { type: "function_call", name: "calc", arguments: {} },
    ];
    const result = buildLiveContent(segments, "The answer is 42.");
    expect(result).toHaveLength(3);
    expect(result[2]).toEqual({ type: "text", text: "The answer is 42." });
  });

  it("returns segments as-is when text is empty", () => {
    const segments = [{ type: "reasoning", text: "thinking" }];
    const result = buildLiveContent(segments, "");
    expect(result).toEqual(segments);
  });

  it("returns segments as-is when text is null/undefined", () => {
    const segments = [{ type: "reasoning", text: "thinking" }];
    expect(buildLiveContent(segments, "")).toEqual(segments);
  });

  it("preserves all segments", () => {
    const segments = [
      { type: "reasoning", text: "thinking" },
      { type: "function_call", name: "a", arguments: {} },
      { type: "function_result", name: "a", output: "ok" },
    ];
    const result = buildLiveContent(segments, "done");
    expect(result).toHaveLength(4);
    expect(result[0]).toEqual(segments[0]);
    expect(result[1]).toEqual(segments[1]);
    expect(result[2]).toEqual(segments[2]);
  });

  it("does not mutate the input array", () => {
    const segments: any[] = [{ type: "reasoning", text: "hi" }];
    buildLiveContent(segments, "text");
    expect(segments).toHaveLength(1);
  });
});
