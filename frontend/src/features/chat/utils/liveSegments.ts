/**
 * PH Agent Hub — liveSegments utility
 *
 * Pure functions that mirror the backend `_accumulate_stream_state` rule:
 * a tool event closes the open reasoning segment; text is always the
 * final part.  Used by ChatWindow to build the live `content` array
 * from streaming events.
 *
 * All functions are non-mutating (return new arrays).
 */

import type { ContentPart } from "./buildSteps";

// ---------------------------------------------------------------------------
// appendReasoningDelta
// ---------------------------------------------------------------------------

/**
 * Extend the trailing reasoning part when it exists, else append a new
 * `{type:"reasoning", text: delta}` part.
 *
 * Mirrors the backend rule: reasoning tokens accumulate into one segment
 * until a tool event arrives.
 */
export function appendReasoningDelta(segments: ContentPart[], delta: string): ContentPart[] {
  const last = segments[segments.length - 1];
  if (last && last.type === "reasoning") {
    const updated = { ...last, text: (last.text || "") + delta };
    return [...segments.slice(0, -1), updated];
  }
  return [...segments, { type: "reasoning", text: delta }];
}

// ---------------------------------------------------------------------------
// appendToolStart
// ---------------------------------------------------------------------------

/**
 * Append a `{type:"function_call", ...}` part.
 * Mirrors today's `displayMessages` mapping.
 */
export function appendToolStart(
  segments: ContentPart[],
  data: {
    tool_name: string;
    tool_call_id?: string;
    arguments?: Record<string, unknown>;
    batch_id?: string;
  },
): ContentPart[] {
  return [
    ...segments,
    {
      type: "function_call",
      name: data.tool_name,
      arguments: data.arguments,
      call_id: data.tool_call_id,
      batch_id: data.batch_id,
    },
  ];
}

// ---------------------------------------------------------------------------
// appendToolResult
// ---------------------------------------------------------------------------

/**
 * Append a `{type:"function_result", ...}` part.
 * Mirrors today's `displayMessages` mapping.
 */
export function appendToolResult(
  segments: ContentPart[],
  data: {
    tool_name?: string;
    tool_call_id?: string;
    success?: boolean;
    result_summary?: unknown;
    batch_id?: string;
  },
): ContentPart[] {
  return [
    ...segments,
    {
      type: "function_result",
      name: data.tool_name,
      output: data.result_summary,
      is_error: data.success === false ? true : undefined,
      call_id: data.tool_call_id,
      batch_id: data.batch_id,
    },
  ];
}

// ---------------------------------------------------------------------------
// buildLiveContent
// ---------------------------------------------------------------------------

/**
 * Build the final content array: [...segments, ...(text ? [{type:"text", text}] : [])].
 * Text is always the final part, matching the backend rule.
 */
export function buildLiveContent(segments: ContentPart[], text: string): ContentPart[] {
  if (text) {
    return [...segments, { type: "text", text }];
  }
  return segments;
}
