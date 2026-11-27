// =============================================================================
// NotificationBell tests (Issue #669 — clear read notifications)
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationBell } from "./NotificationBell";

const mocks = vi.hoisted(() => ({
  listNotifications: vi.fn(),
  getUnreadCount: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  markNotificationRead: vi.fn(),
  deleteReadNotifications: vi.fn(),
}));

vi.mock("../../features/chat/services/notifications", () => ({
  listNotifications: mocks.listNotifications,
  getUnreadCount: mocks.getUnreadCount,
  markAllNotificationsRead: mocks.markAllNotificationsRead,
  markNotificationRead: mocks.markNotificationRead,
  deleteReadNotifications: mocks.deleteReadNotifications,
}));

const readNotification = {
  id: "n-read",
  type: "TASK_COMPLETED",
  title: "Old notification",
  body: "Already seen",
  reference_id: null,
  reference_type: null,
  is_read: true,
  created_at: new Date().toISOString(),
};

const unreadNotification = {
  id: "n-unread",
  type: "TASK_COMPLETED",
  title: "New notification",
  body: null,
  reference_id: null,
  reference_type: null,
  is_read: false,
  created_at: new Date().toISOString(),
};

function renderBell(options?: { staleTime?: number }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: options?.staleTime ?? 0 },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <NotificationBell />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openBell(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Notifications" }));
}

describe("NotificationBell — clear read", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("deletes read notifications and refreshes the panel", async () => {
    mocks.getUnreadCount.mockResolvedValue({ count: 1 });
    mocks.listNotifications
      .mockResolvedValueOnce({
        items: [readNotification, unreadNotification],
        total: 2,
      })
      .mockResolvedValue({
        items: [unreadNotification],
        total: 1,
      });
    mocks.deleteReadNotifications.mockResolvedValue({
      status: "ok",
      deleted: 1,
    });

    const user = userEvent.setup();
    renderBell();
    await openBell(user);

    expect(
      await screen.findByRole("button", { name: "Clear read" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear read" }));
    await user.click(await screen.findByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(mocks.deleteReadNotifications).toHaveBeenCalledTimes(1),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Clear read" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("Old notification")).not.toBeInTheDocument();
    expect(screen.getByText("New notification")).toBeInTheDocument();
  });

  it("refetches on reopen even inside the app-wide stale window", async () => {
    mocks.getUnreadCount.mockResolvedValue({ count: 0 });
    mocks.listNotifications
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [unreadNotification], total: 1 });

    const user = userEvent.setup();
    renderBell({ staleTime: 60_000 });
    await openBell(user);
    expect(await screen.findByText("No notifications")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Notifications" }));
    await openBell(user);

    expect(await screen.findByText("New notification")).toBeInTheDocument();
    expect(mocks.listNotifications).toHaveBeenCalledTimes(2);
  });

  it("hides Clear read when every notification is unread", async () => {
    mocks.getUnreadCount.mockResolvedValue({ count: 2 });
    mocks.listNotifications.mockResolvedValue({
      items: [
        unreadNotification,
        { ...unreadNotification, id: "n-2", title: "Another notification" },
      ],
      total: 2,
    });

    const user = userEvent.setup();
    renderBell();
    await openBell(user);

    expect(await screen.findByText("New notification")).toBeInTheDocument();
    expect(screen.getByText("Another notification")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear read" }),
    ).not.toBeInTheDocument();
  });

  it("keeps Clear read available when deletion fails", async () => {
    mocks.getUnreadCount.mockResolvedValue({ count: 1 });
    mocks.listNotifications.mockResolvedValue({
      items: [readNotification, unreadNotification],
      total: 2,
    });
    mocks.deleteReadNotifications.mockRejectedValue(new Error("boom"));

    const user = userEvent.setup();
    renderBell();
    await openBell(user);

    await user.click(await screen.findByRole("button", { name: "Clear read" }));
    await user.click(await screen.findByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(mocks.deleteReadNotifications).toHaveBeenCalledTimes(1),
    );
    expect(
      await screen.findByRole("button", { name: /clear read/i }),
    ).toBeInTheDocument();
  });
});
