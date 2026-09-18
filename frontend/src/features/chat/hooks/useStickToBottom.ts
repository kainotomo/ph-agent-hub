// =============================================================================
// PH Agent Hub — useStickToBottom
// =============================================================================
// Sticky "is the user following the conversation?" intent, detected from scroll
// position ONLY (no gesture listeners, no layout measurement).
//
// Replaces the inline Virtuoso followOutput/atBottomStateChange logic in
// ChatWindow.tsx with a reusable hook that:
//   1. Tracks whether the user is "following" (scrolled near the bottom).
//   2. Freezes following when the user scrolls up mid-stream.
//   3. Shows a badge on the scroll-to-bottom button when a response finishes
//      while the user is not at the bottom.
//
// See issue #521.
// =============================================================================

import { useRef, useState, useCallback, useEffect } from "react";

// ---------------------------------------------------------------------------
// Pure detector — exported for unit testing
// ---------------------------------------------------------------------------

/**
 * The hysteresis band in pixels.  A scroll position within this distance
 * below the anchor is considered "at bottom" (following).
 */
const HYSTERESIS = 24;

/**
 * Pure scroll-position detector.  Given the current `scrollTop` of the
 * scroller element, returns the updated `anchor` and whether the user
 * has lost following intent.
 *
 * Rules:
 *   - If `following` is true and `scrollTop < anchor - HYSTERESIS` → intent lost.
 *   - Otherwise, `anchor = max(anchor, scrollTop)` (monotonic growth).
 *
 * This is safe because programmatic follow only ever INCREASES scrollTop,
 * and content-shrink/browser clamp DECREASES it but lands at the anchor
 * (inside the hysteresis band).  Only a genuine user scroll-away can trip it.
 */
export function noteScrollTop(
  scrollTop: number,
  anchor: number,
  following: boolean,
): { anchor: number; lost: boolean } {
  if (following && scrollTop < anchor - HYSTERESIS) {
    return { anchor, lost: true };
  }
  return { anchor: Math.max(anchor, scrollTop), lost: false };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

interface UseStickToBottomOptions {
  /** Whether the stream is currently active. */
  streaming: boolean;
  /** Whether the handoff window is open (post-completion, pre-persisted-row). */
  handoffPending: boolean;
  /** Callback to programmatically scroll to the bottom. */
  scrollToBottom: () => void;
}

interface UseStickToBottomReturn {
  /** Callback ref for Virtuoso's `scrollerRef` prop. */
  scrollerRef: (el: HTMLElement | Window | null) => void;
  /** Whether the scroll-to-bottom button should be visible. */
  showScrollButton: boolean;
  /** Whether a finished response is waiting while the user is not at the bottom. */
  unseenComplete: boolean;
  /** Call this when the user clicks the scroll-to-bottom button. */
  followNow: () => void;
  /** Call this when a turn ends (message_complete / stop / error). */
  markTurnEnded: () => void;
  /** Virtuoso's `atBottomStateChange` handler. */
  atBottomStateChange: (atBottom: boolean) => void;
  /** Virtuoso's `followOutput` handler. */
  followOutput: (isAtBottom: boolean) => "smooth" | false;
}

export function useStickToBottom({
  streaming,
  handoffPending,
  scrollToBottom,
}: UseStickToBottomOptions): UseStickToBottomReturn {
  const followingRef = useRef(true);
  const anchorRef = useRef(0);
  const scrollerElRef = useRef<HTMLElement | null>(null);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const [unseenComplete, setUnseenComplete] = useState(false);

  // Reset on session change
  useEffect(() => {
    followingRef.current = true;
    anchorRef.current = 0;
    setShowScrollButton(false);
    setUnseenComplete(false);
  }, []);

  const onScroll = useCallback(() => {
    const el = scrollerElRef.current;
    if (!el) return;
    const scrollTop = el.scrollTop;
    const { anchor, lost } = noteScrollTop(scrollTop, anchorRef.current, followingRef.current);
    anchorRef.current = anchor;
    if (lost) {
      followingRef.current = false;
      setShowScrollButton(true);
    }
  }, []);

  const scrollerRef = useCallback((el: HTMLElement | Window | null) => {
    // Remove listener from old element if the ref is remounting
    if (el && scrollerElRef.current !== el && typeof (scrollerElRef.current as any)?.addEventListener === 'function') {
      (scrollerElRef.current as HTMLElement | null | Window)?.removeEventListener?.("scroll", onScroll);
    }
    if (el && typeof (el as any)?.addEventListener === 'function') {
      el.addEventListener("scroll", onScroll, { passive: true });
    }
    scrollerElRef.current = el as HTMLElement | null;
  }, [onScroll]);

  const followNow = useCallback(() => {
    followingRef.current = true;
    anchorRef.current = 0;
    setUnseenComplete(false);
    setShowScrollButton(false);
    scrollToBottom();
  }, [scrollToBottom]);

  const markTurnEnded = useCallback(() => {
    if (!followingRef.current) {
      setUnseenComplete(true);
    }
  }, []);

  const atBottomStateChange = useCallback((atBottom: boolean) => {
    setShowScrollButton(!atBottom);
    if (atBottom) {
      followingRef.current = true;
      anchorRef.current = 0;
      setUnseenComplete(false);
    }
  }, []);

  const followOutput = useCallback(
    (isAtBottom: boolean): "smooth" | false => {
      if (isAtBottom && followingRef.current && (streaming || handoffPending)) {
        return "smooth";
      }
      return false;
    },
    [streaming, handoffPending],
  );

  return {
    scrollerRef,
    showScrollButton,
    unseenComplete,
    followNow,
    markTurnEnded,
    atBottomStateChange,
    followOutput,
  };
}
