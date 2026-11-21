// =============================================================================
// PH Agent Hub — HTTP response helpers
// =============================================================================
// Raw `fetch` call sites (widget, demo) must not assume every response is
// JSON: a failed request can return plain-text `Internal Server Error` or an
// HTML error page.  Parsing those blindly produces `SyntaxError: Unexpected
// token 'I'` instead of a message the user can act on (Issue #622).
// =============================================================================

const MAX_TEXT_PREVIEW = 200;

/** Collapse whitespace and cap a body preview so error text stays readable. */
function previewOf(text: string): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  if (collapsed.length <= MAX_TEXT_PREVIEW) return collapsed;
  return `${collapsed.slice(0, MAX_TEXT_PREVIEW)}…`;
}

/** Extract a human-readable message from a parsed JSON error body. */
function messageFromJson(body: unknown): string | null {
  if (body === null || typeof body !== "object") return null;
  const record = body as Record<string, unknown>;
  for (const key of ["detail", "message", "error"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (value !== undefined && value !== null && typeof value === "object") {
      // FastAPI validation errors carry `detail` as a list of objects.
      try {
        return JSON.stringify(value);
      } catch {
        return null;
      }
    }
  }
  return null;
}

/**
 * Build a readable message for a failed HTTP response.
 *
 * JSON bodies yield their `detail`/`message`/`error` field; otherwise the
 * response text is shown as `HTTP <status>: <text>` (truncated).  When there
 * is no body at all, `HTTP <status> <statusText>` is the fallback.
 *
 * The response body is consumed.
 */
export async function describeHttpError(response: Response): Promise<string> {
  let raw = "";
  try {
    raw = await response.text();
  } catch {
    raw = "";
  }
  const trimmed = raw.trim();

  if (trimmed) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      const message = messageFromJson(parsed);
      if (message) return message;
    } catch {
      // Not JSON — fall through to the text preview.
    }
    return `HTTP ${response.status}: ${previewOf(trimmed)}`;
  }

  return `HTTP ${response.status} ${response.statusText}`.trim();
}

/**
 * Parse a JSON response, failing with a readable error when the body is
 * not JSON or is malformed.
 *
 * The response body is consumed.
 */
export async function readJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type") || "";

  if (!contentType.toLowerCase().includes("application/json")) {
    let raw = "";
    try {
      raw = await response.text();
    } catch {
      raw = "";
    }
    const preview = previewOf(raw);
    const received = contentType || "an unknown content type";
    throw new Error(
      `Expected JSON but received ${received} (HTTP ${response.status})` +
        (preview ? `: ${preview}` : ""),
    );
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error(
      `Invalid JSON response (HTTP ${response.status} ${response.statusText})`.trim(),
    );
  }
}
