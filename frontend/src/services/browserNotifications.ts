// =============================================================================
// PH Agent Hub — Browser notifications (Issue #619)
// =============================================================================
// Thin wrapper over the Notification API.  Production shows notifications
// through the service worker (`registration.showNotification`) because
// Android Chrome rejects the page-level constructor; the page-level branch
// covers dev builds, where the PWA service worker is not registered.
//
// The app never asks for permission on load: browsers require a user
// gesture, and a denied prompt is permanent for the origin.
// =============================================================================

export type BrowserNotificationPermission =
  | "granted"
  | "denied"
  | "default"
  | "unsupported";

export interface BrowserNotificationOptions {
  title: string;
  body?: string;
  /** Collapses repeated notifications for the same session. */
  tag?: string;
  /** SPA path opened when the notification is clicked. */
  url?: string;
  silent?: boolean;
}

/** Routes that must never raise OS notifications (anonymous widget/demo). */
const EXCLUDED_PATH_PREFIXES = ["/widget", "/demo"];

export function isNotificationSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function getNotificationPermission(): BrowserNotificationPermission {
  if (!isNotificationSupported()) return "unsupported";
  return Notification.permission as BrowserNotificationPermission;
}

/** Must be called from a user gesture (a click on the settings switch). */
export async function requestNotificationPermission(): Promise<BrowserNotificationPermission> {
  if (!isNotificationSupported()) return "unsupported";
  try {
    const permission = await Notification.requestPermission();
    return permission as BrowserNotificationPermission;
  } catch {
    return getNotificationPermission();
  }
}

function isExcludedPath(): boolean {
  if (typeof window === "undefined") return true;
  return EXCLUDED_PATH_PREFIXES.some((prefix) =>
    window.location.pathname.startsWith(prefix),
  );
}

async function getServiceWorkerRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker) return null;
  try {
    return (await navigator.serviceWorker.getRegistration()) ?? null;
  } catch {
    return null;
  }
}

export async function showBrowserNotification(
  options: BrowserNotificationOptions,
): Promise<boolean> {
  if (!isNotificationSupported() || Notification.permission !== "granted") {
    return false;
  }
  if (isExcludedPath()) return false;

  const target = options.url ?? "/chat";
  const registration = await getServiceWorkerRegistration();

  if (registration?.showNotification) {
    try {
      await registration.showNotification(options.title, {
        body: options.body,
        tag: options.tag,
        silent: options.silent ?? false,
        icon: "/icons/icon-192.png",
        badge: "/icons/icon-192.png",
        data: { url: target },
      });
      return true;
    } catch {
      // Fall through to the page-level constructor (dev / older browsers).
    }
  }

  try {
    const notification = new Notification(options.title, {
      body: options.body,
      tag: options.tag,
      silent: options.silent ?? false,
    });
    notification.onclick = () => {
      window.focus();
      window.location.assign(target);
      notification.close();
    };
    return true;
  } catch {
    return false;
  }
}
