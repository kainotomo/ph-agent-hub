// =============================================================================
// Tests — chat export service (Issue #649)
// =============================================================================
// The conversation export must honour the RFC 5987 filename sent by the
// backend (so Greek titles download under their real name) and must surface
// a readable error instead of silently doing nothing.
// =============================================================================

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { exportSession } from "./chat";

const mockFetch = vi.hoisted(() => vi.fn());
vi.stubGlobal("fetch", mockFetch);

const clicks: HTMLAnchorElement[] = [];
let originalCreateObjectURL: typeof URL.createObjectURL | undefined;
let originalRevokeObjectURL: typeof URL.revokeObjectURL | undefined;

beforeEach(() => {
  clicks.length = 0;
  mockFetch.mockReset();

  // jsdom does not implement the blob URL API.
  originalCreateObjectURL = URL.createObjectURL;
  originalRevokeObjectURL = URL.revokeObjectURL;
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(() => "blob:x"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function (this: HTMLAnchorElement) {
      clicks.push(this);
    },
  );
});

afterEach(() => {
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    writable: true,
    value: originalCreateObjectURL,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    writable: true,
    value: originalRevokeObjectURL,
  });
  vi.restoreAllMocks();
});

describe("exportSession (Issue #649)", () => {
  it("saves the file under the RFC 5987 filename", async () => {
    mockFetch.mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: {
          "Content-Disposition": `attachment; filename="file.json"; filename*=UTF-8''%CE%A0%CF%89%CF%82.json`,
        },
      }),
    );

    await exportSession("session-1", "json");

    expect(clicks).toHaveLength(1);
    expect(clicks[0].download).toBe("Πως.json");
  });

  it("falls back to conversation.<format> without a header", async () => {
    mockFetch.mockResolvedValue(new Response("{}", { status: 200 }));

    await exportSession("session-1", "txt");

    expect(clicks[0].download).toBe("conversation.txt");
  });

  it("rejects with a readable error when the export fails", async () => {
    mockFetch.mockResolvedValue(
      new Response("Internal Server Error", {
        status: 500,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      }),
    );

    await expect(exportSession("session-1", "json")).rejects.toThrow(
      "HTTP 500: Internal Server Error",
    );
  });
});
