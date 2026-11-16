// =============================================================================
// Browser notifications service tests (Issue #619)
// =============================================================================

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getNotificationPermission,
  isNotificationSupported,
  requestNotificationPermission,
  showBrowserNotification,
} from "./browserNotifications";

class MockNotification {
  static permission: NotificationPermission = "granted";
  static requestPermission = vi.fn(
    async () => "granted" as NotificationPermission,
  );

  onclick: (() => void) | null = null;
  close = vi.fn();

  constructor(
    readonly title: string,
    readonly options?: NotificationOptions,
  ) {}
}

function stubServiceWorker(getRegistration: () => Promise<unknown>): void {
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { getRegistration: vi.fn(getRegistration) },
  });
}

describe("browserNotifications", () => {
  beforeEach(() => {
    MockNotification.permission = "granted";
    MockNotification.requestPermission = vi.fn(
      async () => "granted" as NotificationPermission,
    );
    vi.stubGlobal("Notification", MockNotification);
    stubServiceWorker(async () => undefined);
    window.history.replaceState({}, "", "/chat");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports the permission from the browser", () => {
    expect(isNotificationSupported()).toBe(true);
    expect(getNotificationPermission()).toBe("granted");
  });

  it("requests permission once", async () => {
    await expect(requestNotificationPermission()).resolves.toBe("granted");
    expect(MockNotification.requestPermission).toHaveBeenCalledTimes(1);
  });

  it("falls back to the page constructor without a service worker", async () => {
    await expect(
      showBrowserNotification({ title: "Hello", body: "World" }),
    ).resolves.toBe(true);
  });

  it("uses the service worker when one is registered", async () => {
    const showNotification = vi.fn(async () => undefined);
    stubServiceWorker(async () => ({ showNotification }));

    await expect(
      showBrowserNotification({ title: "Hi", url: "/chat/abc" }),
    ).resolves.toBe(true);
    expect(showNotification).toHaveBeenCalledWith(
      "Hi",
      expect.objectContaining({ data: { url: "/chat/abc" } }),
    );
  });

  it("does not notify when permission is not granted", async () => {
    MockNotification.permission = "denied";
    await expect(showBrowserNotification({ title: "Hi" })).resolves.toBe(false);
  });

  it("does not notify on widget or demo routes", async () => {
    window.history.replaceState({}, "", "/widget/session/1");
    await expect(showBrowserNotification({ title: "Hi" })).resolves.toBe(false);
  });
});
