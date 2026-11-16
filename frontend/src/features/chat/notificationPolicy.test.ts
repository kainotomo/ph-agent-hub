// =============================================================================
// Notification policy tests (Issue #619)
// =============================================================================

import { describe, expect, it } from "vitest";

import {
  REFOCUS_GRACE_MS,
  shouldNotifyFinishedRun,
  type NotificationContext,
} from "./notificationPolicy";
import type { RunStatusEvent } from "./sessionStatusStore";

const completed: RunStatusEvent = {
  session_id: "s1",
  run_id: "r1",
  state: "finished",
  outcome: "completed",
};

function context(overrides: Partial<NotificationContext> = {}): NotificationContext {
  return {
    enabled: true,
    onCompletion: true,
    onFailure: true,
    permission: "granted",
    visible: false,
    focused: false,
    lastVisibleAt: null,
    now: 10_000,
    ...overrides,
  };
}

describe("shouldNotifyFinishedRun", () => {
  it("notifies for a completion in a hidden tab", () => {
    expect(shouldNotifyFinishedRun(completed, context())).toBe(true);
  });

  it("notifies when the window lost focus", () => {
    expect(
      shouldNotifyFinishedRun(completed, context({ visible: true, focused: false })),
    ).toBe(true);
  });

  it("stays quiet while the user is looking at the app", () => {
    expect(
      shouldNotifyFinishedRun(completed, context({ visible: true, focused: true })),
    ).toBe(false);
  });

  it("requires the master switch and the browser permission", () => {
    expect(shouldNotifyFinishedRun(completed, context({ enabled: false }))).toBe(false);
    expect(shouldNotifyFinishedRun(completed, context({ permission: "default" }))).toBe(false);
    expect(shouldNotifyFinishedRun(completed, context({ permission: "denied" }))).toBe(false);
    expect(shouldNotifyFinishedRun(completed, context({ permission: "unsupported" }))).toBe(false);
  });

  it("respects the per-outcome preferences", () => {
    expect(
      shouldNotifyFinishedRun(completed, context({ onCompletion: false })),
    ).toBe(false);
    const failed: RunStatusEvent = { ...completed, outcome: "failed" };
    expect(shouldNotifyFinishedRun(failed, context())).toBe(true);
    expect(shouldNotifyFinishedRun(failed, context({ onFailure: false }))).toBe(false);
  });

  it("never notifies for cancelled or lost runs", () => {
    for (const outcome of ["cancelled", "lost"] as const) {
      expect(
        shouldNotifyFinishedRun({ ...completed, outcome }, context()),
      ).toBe(false);
    }
  });

  it("does not notify on running events", () => {
    expect(
      shouldNotifyFinishedRun({ ...completed, state: "running" }, context()),
    ).toBe(false);
  });

  it("suppresses stale catch-up events right after refocusing", () => {
    expect(
      shouldNotifyFinishedRun(
        completed,
        context({ lastVisibleAt: 10_000 - (REFOCUS_GRACE_MS - 1) }),
      ),
    ).toBe(false);
    expect(
      shouldNotifyFinishedRun(
        completed,
        context({ lastVisibleAt: 10_000 - REFOCUS_GRACE_MS - 1 }),
      ),
    ).toBe(true);
  });
});
