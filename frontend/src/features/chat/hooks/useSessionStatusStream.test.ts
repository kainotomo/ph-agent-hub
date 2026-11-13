// =============================================================================
// useSessionStatusStream — Unit Tests (Issue #604)
// =============================================================================
// The hook polls the authoritative run registry instead of holding a
// long-lived SSE connection per tab (browser HTTP/1.1 caps an origin at six
// connections, which made the third streaming tab hang until a refresh).
// These tests pin the polling contract: snapshot delivery, start/finish
// transitions with their outcome, backoff, auth refresh, and clean stop.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useSessionStatusStream } from "./useSessionStatusStream";

const { mockRefreshAccessToken, mockGetToken } = vi.hoisted(() => ({
  mockRefreshAccessToken: vi.fn(),
  mockGetToken: vi.fn(() => "test-token"),
}));

vi.mock("../../../services/api", () => ({
  getToken: () => mockGetToken(),
  refreshAccessToken: mockRefreshAccessToken,
}));

const VISIBLE_INTERVAL = 3_000;

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** Let the pending poll promise chain settle. */
async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useSessionStatusStream", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mockRefreshAccessToken.mockResolvedValue(null);
    mockGetToken.mockReturnValue("test-token");
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("does not poll when disabled", () => {
    renderHook(() =>
      useSessionStatusStream({
        enabled: false,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("polls the run snapshot and reports a new run", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        runs: [
          { session_id: "s1", run_id: "r1", kind: "chat", revision: 40 },
        ],
        finished: [],
      }),
    );

    const onSnapshot = vi.fn();
    const onEvent = vi.fn();
    renderHook(() =>
      useSessionStatusStream({ enabled: true, onSnapshot, onEvent }),
    );
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/chat/runs");
    expect(onSnapshot).toHaveBeenCalledWith([
      { session_id: "s1", run_id: "r1", kind: "chat", revision: 40 },
    ]);
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        session_id: "s1",
        run_id: "r1",
        state: "running",
        revision: 40,
      }),
    );
  });

  it("polls again on the visible cadence", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ runs: [], finished: [] }));

    renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(VISIBLE_INTERVAL);
    });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports a finish with its outcome for a run that disappeared", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ runs: [{ session_id: "s1", run_id: "r1" }], finished: [] }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          runs: [],
          finished: [
            { session_id: "s1", run_id: "r1", outcome: "completed", revision: 41 },
          ],
        }),
      );

    const onEvent = vi.fn();
    renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent,
      }),
    );
    await flush();

    // The second poll names the run the tab already knows about so the
    // server can answer with an owner-scoped outcome.
    await act(async () => {
      vi.advanceTimersByTime(VISIBLE_INTERVAL);
    });
    await flush();

    expect(fetchMock.mock.calls[1][0]).toBe("/api/chat/runs?sessions=s1%3Ar1");
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        session_id: "s1",
        run_id: "r1",
        state: "finished",
        outcome: "completed",
        revision: 41,
      }),
    );
  });

  it("does not re-report a run that stayed active", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ runs: [{ session_id: "s1", run_id: "r1" }], finished: [] }),
    );

    const onEvent = vi.fn();
    renderHook(() =>
      useSessionStatusStream({ enabled: true, onSnapshot: vi.fn(), onEvent }),
    );
    await flush();
    await act(async () => {
      vi.advanceTimersByTime(VISIBLE_INTERVAL);
    });
    await flush();

    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("backs off after an error and recovers on the next successful poll", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(jsonResponse({ runs: [], finished: [] }));

    const onSnapshot = vi.fn();
    renderHook(() =>
      useSessionStatusStream({ enabled: true, onSnapshot, onEvent: vi.fn() }),
    );
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onSnapshot).not.toHaveBeenCalled();

    // Failed poll backs off well past the normal cadence.
    await act(async () => {
      vi.advanceTimersByTime(VISIBLE_INTERVAL);
    });
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onSnapshot).toHaveBeenCalledTimes(1);
  });

  it("refreshes the token once on 401 and retries immediately", async () => {
    mockRefreshAccessToken.mockResolvedValue("new-token");
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ runs: [], finished: [] }));

    const onSnapshot = vi.fn();
    renderHook(() =>
      useSessionStatusStream({ enabled: true, onSnapshot, onEvent: vi.fn() }),
    );
    await flush();

    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);

    // Retry is scheduled with delay 0 rather than the 3 s cadence.
    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onSnapshot).toHaveBeenCalledTimes(1);
  });

  it("stops polling after unmount", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ runs: [], finished: [] }));

    const { unmount } = renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      vi.advanceTimersByTime(VISIBLE_INTERVAL * 3);
    });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
