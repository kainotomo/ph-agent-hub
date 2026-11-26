// =============================================================================
// Tests — shared HTTP response helpers (Issue #622)
// =============================================================================

import { describe, expect, it } from "vitest";
import {
  describeHttpError,
  filenameFromContentDisposition,
  readJsonResponse,
} from "./http";

describe("describeHttpError", () => {
  it("surfaces a JSON `detail` message", async () => {
    const response = new Response(
      JSON.stringify({ detail: "Agent execution failed: boom" }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      },
    );

    await expect(describeHttpError(response)).resolves.toBe(
      "Agent execution failed: boom",
    );
  });

  it("formats a plain-text 500 instead of throwing SyntaxError", async () => {
    const response = new Response("Internal Server Error", {
      status: 500,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });

    await expect(describeHttpError(response)).resolves.toBe(
      "HTTP 500: Internal Server Error",
    );
  });

  it("truncates long non-JSON bodies", async () => {
    const html = `<html><body>${"x".repeat(1000)}</body></html>`;
    const response = new Response(html, {
      status: 502,
      headers: { "Content-Type": "text/html" },
    });

    const message = await describeHttpError(response);
    expect(message.startsWith("HTTP 502: <html>")).toBe(true);
    expect(message.length).toBeLessThan(260);
  });

  it("falls back to status and statusText for an empty body", async () => {
    const response = new Response(null, { status: 503, statusText: "Unavailable" });

    await expect(describeHttpError(response)).resolves.toBe("HTTP 503 Unavailable");
  });
});

describe("readJsonResponse", () => {
  it("parses a JSON body", async () => {
    const response = new Response(JSON.stringify({ content: "hi" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

    await expect(readJsonResponse<{ content: string }>(response)).resolves.toEqual({
      content: "hi",
    });
  });

  it("rejects a non-JSON 200 with a readable error", async () => {
    const response = new Response("<html>proxy error</html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });

    await expect(readJsonResponse(response)).rejects.toThrow(
      /Expected JSON but received text\/html/,
    );
  });

  it("rejects malformed JSON", async () => {
    const response = new Response("{not json", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });

    await expect(readJsonResponse(response)).rejects.toThrow(
      /Invalid JSON response/,
    );
  });
});

describe("filenameFromContentDisposition", () => {
  it("prefers the RFC 5987 filename* parameter", () => {
    const header = `attachment; filename="file.json"; filename*=UTF-8''%CE%A0%CF%89%CF%82.json`;

    expect(filenameFromContentDisposition(header)).toBe("Πως.json");
  });

  it("falls back to the plain filename parameter", () => {
    expect(
      filenameFromContentDisposition('attachment; filename="chat.txt"'),
    ).toBe("chat.txt");
  });

  it("returns null when the header has no filename", () => {
    expect(filenameFromContentDisposition("attachment")).toBeNull();
    expect(filenameFromContentDisposition(null)).toBeNull();
  });

  it("falls back to the plain parameter for malformed percent-encoding", () => {
    const header = `attachment; filename="chat.json"; filename*=UTF-8''%E0%A4%A`;

    expect(filenameFromContentDisposition(header)).toBe("chat.json");
  });
});
