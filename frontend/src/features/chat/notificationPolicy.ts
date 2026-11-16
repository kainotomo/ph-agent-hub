// =============================================================================
// PH Agent Hub — When a finished run deserves a browser notification
// =============================================================================
// Pure policy so the decision is unit-testable and identical everywhere.
//
// The rule mirrors what ChatGPT/Codex and Claude Code do: only interrupt a
// user who is actually elsewhere.  A result in the session being read is
// already on screen, and an event that arrives right after the tab becomes
// visible is a catch-up from the poll, not news.
// =============================================================================

import type { BrowserNotificationPermission } from "../../services/browserNotifications";
import type { RunStatusEvent } from "./sessionStatusStore";

export interface NotificationContext {
  /** Master switch from the user's notification preferences. */
  enabled: boolean;
  /** Per-outcome switches from the same preferences. */
  onCompletion: boolean;
  onFailure: boolean;
  /** Browser permission for this device. */
  permission: BrowserNotificationPermission;
  visible: boolean;
  focused: boolean;
  /** Epoch ms of the last transition to visible, if it happened. */
  lastVisibleAt: number | null;
  /** Injectable clock for tests. */
  now: number;
}

/** Prompts that arrive right after refocusing are stale catch-up events. */
export const REFOCUS_GRACE_MS = 2_000;

export function shouldNotifyFinishedRun(
  event: RunStatusEvent,
  context: NotificationContext,
): boolean {
  if (!context.enabled) return false;
  if (context.permission !== "granted") return false;
  if (event.state !== "finished") return false;
  if (event.outcome !== "completed" && event.outcome !== "failed") return false;
  if (event.outcome === "completed" && !context.onCompletion) return false;
  if (event.outcome === "failed" && !context.onFailure) return false;

  // A visible, focused tab already shows the result (dot / message).
  if (context.visible && context.focused) return false;

  // After refocusing, the poll re-delivers old finishes; they are not news.
  if (
    context.lastVisibleAt !== null &&
    context.now - context.lastVisibleAt < REFOCUS_GRACE_MS
  ) {
    return false;
  }

  return true;
}
