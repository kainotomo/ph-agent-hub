// =============================================================================
// StreamingProvider — browser notification wiring tests (Issue #619)
// =============================================================================

import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StreamingProvider, useStreamingContext } from "./StreamingProvider";

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  show: vi.fn(async () => true),
  record: vi.fn(async () => ({ status: "ok", created: true })),
}));

vi.mock("../services/browserNotifications", () => ({
  getNotificationPermission: () => "granted",
  showBrowserNotification: mocks.show,
}));

vi.mock("../features/chat/services/notifications", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("../features/chat/services/notifications")
    >();
  return { ...actual, recordRunFinishedNotification: mocks.record };
});

vi.mock("./AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: "u1",
      notification_prefs: {
        enabled: true,
        on_completion: true,
        on_failure: true,
        sound: false,
      },
    },
    loading: false,
  }),
}));

vi.mock("../features/chat/hooks/useSessionStatusStream", () => ({
  useSessionStatusStream: mocks.stream,
}));

function setDocumentState({
  visible,
  focused,
}: {
  visible: boolean;
  focused: boolean;
}) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (visible ? "visible" : "hidden"),
  });
  Object.defineProperty(document, "hasFocus", {
    configurable: true,
    value: () => focused,
  });
}

function renderStore() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderHook(() => useStreamingContext(), {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <StreamingProvider>{children}</StreamingProvider>
      </QueryClientProvider>
    ),
  });
}

function latestEvent() {
  const calls = mocks.stream.mock.calls;
  return calls[calls.length - 1][0] as {
    onEvent: (event: Record<string, unknown>) => void;
    onLocalRunDropped?: (sessionId: string) => void;
  };
}

const finished = {
  session_id: "s1",
  run_id: "r1",
  state: "finished" as const,
  outcome: "completed" as const,
};

describe("StreamingProvider browser notifications", () => {
  beforeEach(() => {
    localStorage.clear();
    mocks.show.mockClear();
    mocks.show.mockResolvedValue(true);
    mocks.record.mockClear();
    mocks.record.mockResolvedValue({ status: "ok", created: true });
    mocks.stream.mockClear();
  });

  afterEach(() => {
    setDocumentState({ visible: true, focused: true });
  });

  it("clears an optimistic local run the registry never confirms", () => {
    const { result } = renderStore();
    act(() => {
      result.current.addStreamingSession("s1");
    });
    expect(result.current.streamingSessionIds.has("s1")).toBe(true);

    act(() => {
      latestEvent().onLocalRunDropped?.("s1");
    });

    expect(result.current.streamingSessionIds.has("s1")).toBe(false);
  });

  it("notifies once for a completion in a hidden tab", () => {
    setDocumentState({ visible: false, focused: false });
    const { result } = renderStore();
    act(() => {
      result.current.seedFromSessions([{ id: "s1", title: "Quarterly report" }]);
    });
    act(() => {
      latestEvent().onEvent(finished);
      latestEvent().onEvent(finished);
    });

    expect(mocks.show).toHaveBeenCalledTimes(1);
    expect(mocks.show).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Response ready",
        tag: "ph-session-s1",
        url: "/chat/s1",
      }),
    );
  });

  it("persists a durable bell record for the notified run", async () => {
    setDocumentState({ visible: false, focused: false });
    const { result } = renderStore();
    act(() => {
      result.current.seedFromSessions([{ id: "s1", title: "Quarterly report" }]);
    });
    act(() => {
      latestEvent().onEvent({
        ...finished,
        started_at: "2026-11-27T09:00:00+00:00",
      });
      latestEvent().onEvent({
        ...finished,
        started_at: "2026-11-27T09:00:00+00:00",
      });
    });

    await waitFor(() => expect(mocks.record).toHaveBeenCalledTimes(1));
    expect(mocks.record).toHaveBeenCalledWith({
      session_id: "s1",
      outcome: "completed",
      started_at: "2026-11-27T09:00:00+00:00",
    });
  });

  it("does not persist when the browser notification is suppressed", async () => {
    mocks.show.mockResolvedValue(false);
    setDocumentState({ visible: false, focused: false });
    const { result } = renderStore();
    act(() => {
      result.current.seedFromSessions([{ id: "s1", title: "Quarterly report" }]);
      latestEvent().onEvent(finished);
    });

    await waitFor(() => expect(mocks.show).toHaveBeenCalledTimes(1));
    expect(mocks.record).not.toHaveBeenCalled();
  });

  it("stays quiet for the session on screen", () => {
    setDocumentState({ visible: true, focused: true });
    const { result } = renderStore();
    act(() => {
      result.current.seedFromSessions([{ id: "s1", title: "T" }]);
      result.current.setActiveSession("s1");
      latestEvent().onEvent(finished);
    });
    expect(mocks.show).not.toHaveBeenCalled();
  });

  it("notifies for another session while the app is focused", () => {
    setDocumentState({ visible: true, focused: true });
    const { result } = renderStore();
    act(() => {
      result.current.seedFromSessions([{ id: "s1", title: "Quarterly report" }]);
      result.current.setActiveSession("another-session");
      latestEvent().onEvent(finished);
    });
    expect(mocks.show).toHaveBeenCalledWith(
      expect.objectContaining({ tag: "ph-session-s1" }),
    );
  });

  it("uses the failure copy for a failed run", async () => {
    setDocumentState({ visible: false, focused: false });
    const { result } = renderStore();
    act(() => {
      result.current.seedFromSessions([{ id: "s1", title: "Quarterly report" }]);
      latestEvent().onEvent({ ...finished, run_id: "r2", outcome: "failed" });
    });
    expect(mocks.show).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Run failed" }),
    );
    await waitFor(() =>
      expect(mocks.record).toHaveBeenCalledWith(
        expect.objectContaining({ outcome: "failed" }),
      ),
    );
  });
});
