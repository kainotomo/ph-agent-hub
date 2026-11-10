// =============================================================================
// useSessionStatusStream — Unit Tests (Issue #604)
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useSessionStatusStream } from "./useSessionStatusStream";

const { mockFetchEventSource, mockRefreshAccessToken } = vi.hoisted(() => ({
  mockFetchEventSource: vi.fn(),
  mockRefreshAccessToken: vi.fn(),
}));

vi.mock("@microsoft/fetch-event-source", () => ({
  fetchEventSource: mockFetchEventSource,
  EventStreamContentType: "text/event-stream",
}));

vi.mock("../../../services/api", () => ({
  getToken: () => "test-token",
  refreshAccessToken: mockRefreshAccessToken,
}));

function okResponse(): Response {
  return new Response("", {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function configAt(index: number = 0) {
  return mockFetchEventSource.mock.calls[index][1] as {
    url?: string;
    onopen: (response: Response) => Promise<void>;
    onmessage: (event: { event: string; data: string }) => void;
    onclose: () => void;
    onerror: (error: unknown) => void;
  };
}

describe("useSessionStatusStream", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockRefreshAccessToken.mockResolvedValue(null);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not connect when disabled", () => {
    renderHook(() =>
      useSessionStatusStream({
        enabled: false,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );
    expect(mockFetchEventSource).not.toHaveBeenCalled();
  });

  it("connects when enabled and dispatches snapshot + run_status events", async () => {
    mockFetchEventSource.mockImplementation(() => new Promise(() => {}));

    const onSnapshot = vi.fn();
    const onEvent = vi.fn();
    renderHook(() =>
      useSessionStatusStream({ enabled: true, onSnapshot, onEvent }),
    );

    await waitFor(() => expect(mockFetchEventSource).toHaveBeenCalled());
    const config = configAt();
    expect(mockFetchEventSource.mock.calls[0][0]).toBe("/api/chat/streams");

    await act(async () => {
      await config.onopen(okResponse());
    });

    const runs = [{ session_id: "s1", run_id: "r1" }];
    const event = {
      session_id: "s1",
      run_id: "r1",
      state: "finished",
      outcome: "completed",
    };
    config.onmessage({
      event: "status_snapshot",
      data: JSON.stringify({ runs }),
    });
    config.onmessage({
      event: "run_status",
      data: JSON.stringify(event),
    });

    expect(onSnapshot).toHaveBeenCalledWith(runs);
    expect(onEvent).toHaveBeenCalledWith(event);
  });

  it("reconnects after the stream closes", async () => {
    vi.useFakeTimers();
    mockFetchEventSource.mockResolvedValue(undefined);

    renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(mockFetchEventSource).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1_000);
      await Promise.resolve();
    });
    expect(mockFetchEventSource).toHaveBeenCalledTimes(2);
  });

  it("does not retry on 401 when the token cannot be refreshed", async () => {
    vi.useFakeTimers();
    mockRefreshAccessToken.mockResolvedValue(null);
    mockFetchEventSource.mockImplementation(async (_url: string, config: any) => {
      await config.onopen(new Response("", { status: 401 }));
    });

    renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mockFetchEventSource).toHaveBeenCalledTimes(1);
  });

  it("reconnects immediately after a successful token refresh on 401", async () => {
    vi.useFakeTimers();
    mockRefreshAccessToken.mockResolvedValue("fresh-token");
    mockFetchEventSource.mockImplementation(async (_url: string, config: any) => {
      await config.onopen(new Response("", { status: 401 }));
    });

    renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(mockRefreshAccessToken).toHaveBeenCalledTimes(1);
    expect(mockFetchEventSource).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(mockFetchEventSource).toHaveBeenCalledTimes(2);
  });

  it("stops reconnecting after unmount", async () => {
    vi.useFakeTimers();
    mockFetchEventSource.mockResolvedValue(undefined);

    const { unmount } = renderHook(() =>
      useSessionStatusStream({
        enabled: true,
        onSnapshot: vi.fn(),
        onEvent: vi.fn(),
      }),
    );

    await act(async () => {
      await Promise.resolve();
    });
    unmount();

    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mockFetchEventSource).toHaveBeenCalledTimes(1);
  });
});
