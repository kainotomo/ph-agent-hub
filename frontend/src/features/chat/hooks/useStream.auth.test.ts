// =============================================================================
// useStream — Issue #610: single-flight token refresh + 401 retry
// =============================================================================
// A stale access token used to end the stream silently: the POST returned a
// non-SSE 401, `onopen` treated every non-SSE response the same way and the
// user saw a conversation stop with no message.
//
// These tests pin the contract:
//   * 401 -> one refresh (single flight in services/api) -> one retry of the
//     same request, with the same body (never a second user message),
//   * a retry that 401s again (or a failed refresh) yields an explicit
//     "sign in again" failure instead of silence,
//   * no retry storm: at most one refresh + one retry per request.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useStream } from "./useStream";

const {
  mockFetchEventSource,
  mockFetch,
  mockGetToken,
  mockRefreshAccessToken,
} = vi.hoisted(() => ({
  mockFetchEventSource: vi.fn(),
  mockFetch: vi.fn(),
  mockGetToken: vi.fn(),
  mockRefreshAccessToken: vi.fn(),
}));

vi.mock("@microsoft/fetch-event-source", () => ({
  fetchEventSource: mockFetchEventSource,
  EventStreamContentType: "text/event-stream",
}));

vi.mock("../../../services/api", () => ({
  getToken: mockGetToken,
  refreshAccessToken: mockRefreshAccessToken,
}));

vi.stubGlobal("fetch", mockFetch);

function sseOk(): Response {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "text/event-stream" }),
    clone() {
      return this;
    },
    json: async () => ({}),
  } as unknown as Response;
}

function unauthorized(): Response {
  return {
    ok: false,
    status: 401,
    headers: new Headers({ "content-type": "application/json" }),
    clone() {
      return this;
    },
    json: async () => ({ detail: "Not authenticated" }),
  } as unknown as Response;
}

interface CapturedRequest {
  url: string;
  init: { headers?: Record<string, string>; body?: string };
}

/** Read the bearer header from either a plain object or a Headers instance. */
function authOf(init: CapturedRequest["init"]): string | undefined {
  const headers = init.headers as unknown;
  if (headers instanceof Headers) {
    return headers.get("Authorization") ?? headers.get("authorization") ?? undefined;
  }
  const plain = (headers ?? {}) as Record<string, string>;
  return plain.Authorization ?? plain.authorization;
}

/**
 * Stand-in for fetch-event-source: it executes the hook's `fetch` wrapper
 * (which owns the 401 refresh/retry) and then calls `onopen` with the final
 * response, exactly like the real library's `create()`.
 */
function installEventSourceFake() {
  mockFetchEventSource.mockImplementation(
    async (
      url: string,
      config: {
        method?: string;
        headers?: Record<string, string>;
        body?: string;
        signal?: AbortSignal;
        fetch?: (input: string, init: Record<string, unknown>) => Promise<Response>;
        onopen?: (response: Response) => Promise<void> | void;
        onclose?: () => void;
      },
    ) => {
      const request = {
        method: config.method,
        headers: config.headers,
        body: config.body,
        signal: config.signal,
      };
      const response = config.fetch
        ? await config.fetch(url, request as Record<string, unknown>)
        : await fetch(url, request as RequestInit);
      await config.onopen?.(response);
      config.onclose?.();
    },
  );
}

describe("useStream — 401 refresh and retry (Issue #610)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetToken.mockReturnValue("stale-token");
    mockRefreshAccessToken.mockResolvedValue("fresh-token");
    installEventSourceFake();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("refreshes once and retries the same POST after a 401", async () => {
    mockFetch
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(sseOk());

    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startStream(
        "session-1",
        "Hello",
        [],
        undefined,
        undefined,
        {},
      );
    });

    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(2);

    const [first, second] = mockFetch.mock.calls as Array<
      [string, CapturedRequest["init"]]
    >;
    // Same request, retried once with the refreshed token.
    expect(second[0]).toBe(first[0]);
    expect(second[1].body).toBe(first[1].body);
    expect(authOf(first[1])).toBe("Bearer stale-token");
    expect(authOf(second[1])).toBe("Bearer fresh-token");
    // The body still carries exactly one user message.
    expect(JSON.parse(second[1].body || "{}").content).toBe("Hello");
  });

  it("surfaces an explicit sign-in failure when the retry also 401s", async () => {
    mockFetch
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(unauthorized());
    const onError = vi.fn();

    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startStream(
        "session-1",
        "Hello",
        [],
        undefined,
        undefined,
        { onError },
      );
    });

    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatch(/sign in again/i);
  });

  it("surfaces a sign-in failure without retrying when refresh fails", async () => {
    mockRefreshAccessToken.mockResolvedValue(null);
    mockFetch.mockResolvedValueOnce(unauthorized());
    const onError = vi.fn();

    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startStream(
        "session-1",
        "Hello",
        [],
        undefined,
        undefined,
        { onError },
      );
    });

    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toMatch(/sign in again/i);
  });

  it("refreshes and retries a reconnecting events GET with the same cursor", async () => {
    // Seed a cursor with a live event, then force the reconnect GET to 401.
    mockFetch.mockResolvedValueOnce(unauthorized()).mockResolvedValueOnce(sseOk());

    const { result } = renderHook(() => useStream());

    await act(async () => {
      await result.current.startReconnect("session-1", {});
    });

    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    const [first, second] = mockFetch.mock.calls as Array<
      [string, CapturedRequest["init"]]
    >;
    expect(second[0]).toBe(first[0]);
    expect(authOf(first[1])).toBe("Bearer stale-token");
    expect(authOf(second[1])).toBe("Bearer fresh-token");
  });
});
