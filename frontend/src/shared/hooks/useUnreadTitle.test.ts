// =============================================================================
// Unread title badge tests (Issue #619)
// =============================================================================

import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BASE_TITLE, useUnreadTitle } from "./useUnreadTitle";

describe("useUnreadTitle", () => {
  it("prefixes the tab title with the unread count", () => {
    const { rerender, unmount } = renderHook(
      ({ count }: { count: number }) => useUnreadTitle(count),
      { initialProps: { count: 2 } },
    );

    expect(document.title).toBe(`(2) ${BASE_TITLE}`);

    rerender({ count: 0 });
    expect(document.title).toBe(BASE_TITLE);

    rerender({ count: 5 });
    expect(document.title).toBe(`(5) ${BASE_TITLE}`);

    unmount();
  });
});
