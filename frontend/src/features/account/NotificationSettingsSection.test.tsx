// =============================================================================
// Notification settings section tests (Issue #619)
// =============================================================================

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationSettingsSection } from "./NotificationSettingsSection";

type Permission = "granted" | "denied" | "default" | "unsupported";

const mocks = vi.hoisted(() => ({
  permission: "default" as Permission,
  refreshUser: vi.fn(async () => {}),
  setPrefs: vi.fn(async (prefs: unknown) => prefs),
  requestPermission: vi.fn(async (): Promise<Permission> => "granted"),
  show: vi.fn(async () => true),
}));

vi.mock("../../providers/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: "u1",
      notification_prefs: {
        enabled: false,
        on_completion: true,
        on_failure: true,
        sound: false,
      },
    },
    refreshUser: mocks.refreshUser,
  }),
}));

vi.mock("../../services/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../services/auth")>();
  return { ...actual, setNotificationPrefs: mocks.setPrefs };
});

vi.mock("../../services/browserNotifications", () => ({
  isNotificationSupported: () => true,
  getNotificationPermission: () => mocks.permission,
  requestNotificationPermission: mocks.requestPermission,
  showBrowserNotification: mocks.show,
}));

describe("NotificationSettingsSection", () => {
  beforeEach(() => {
    mocks.permission = "default";
    mocks.refreshUser.mockClear();
    mocks.setPrefs.mockClear();
    mocks.requestPermission.mockClear();
    mocks.show.mockClear();
    mocks.requestPermission.mockImplementation(async () => "granted");
  });

  it("asks for the permission before saving the master switch", async () => {
    const user = userEvent.setup();
    render(<NotificationSettingsSection />);

    await user.click(screen.getAllByRole("switch")[0]);

    expect(mocks.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(mocks.setPrefs).toHaveBeenCalledWith(
        expect.objectContaining({ enabled: true }),
      ),
    );
  });

  it("does not enable anything when the permission is denied", async () => {
    mocks.requestPermission.mockImplementation(async () => "denied");
    const user = userEvent.setup();
    render(<NotificationSettingsSection />);

    await user.click(screen.getAllByRole("switch")[0]);

    await waitFor(() => expect(mocks.requestPermission).toHaveBeenCalled());
    expect(mocks.setPrefs).not.toHaveBeenCalled();
  });

  it("sends a test notification when the device is enabled", async () => {
    mocks.permission = "granted";
    const user = userEvent.setup();
    render(<NotificationSettingsSection />);

    await user.click(
      screen.getByRole("button", { name: /send test notification/i }),
    );

    expect(mocks.show).toHaveBeenCalledWith(
      expect.objectContaining({ tag: "ph-test", url: "/settings" }),
    );
  });
});
