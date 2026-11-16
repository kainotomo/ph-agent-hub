// =============================================================================
// PH Agent Hub — Unread completion badge in the tab title (Issue #619)
// =============================================================================
// The cheapest "something finished" signal: the tab title carries a count
// while unread completions exist.  It needs no permission and survives a
// denied Notification prompt, so it is the fallback that always works.
// =============================================================================

import { useEffect } from "react";

export const BASE_TITLE = "PH Agent Hub";

export function useUnreadTitle(count: number): void {
  useEffect(() => {
    document.title = count > 0 ? `(${count}) ${BASE_TITLE}` : BASE_TITLE;
    return () => {
      document.title = BASE_TITLE;
    };
  }, [count]);
}
