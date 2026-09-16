// =============================================================================
// messagePaging — react-virtuoso firstItemIndex contract (Issue #515)
// =============================================================================
// `firstItemIndex` must stay constant when items are appended at the tail and
// decrease by exactly the number of items prepended at the head.  In this app
// `pages[0]` is the newest page (append target) and `pages[1..]` are older
// pages that get prepended to the chronological display array.
// =============================================================================

import { describe, it, expect } from "vitest";
import {
  FIRST_ITEM_INDEX_BASE,
  computeFirstItemIndex,
} from "./messagePaging";

describe("computeFirstItemIndex", () => {
  it("returns the base for undefined pages", () => {
    expect(computeFirstItemIndex(undefined)).toBe(FIRST_ITEM_INDEX_BASE);
  });

  it("returns the base for an empty pages array", () => {
    expect(computeFirstItemIndex([])).toBe(FIRST_ITEM_INDEX_BASE);
  });

  it("returns the base for a single page (only the newest page loaded)", () => {
    expect(computeFirstItemIndex([{ items: [1, 2, 3] }])).toBe(
      FIRST_ITEM_INDEX_BASE,
    );
  });

  it("leaves the index unchanged when the newest page grows (tail append)", () => {
    const before = computeFirstItemIndex([{ items: ["a", "b"] }]);
    const after = computeFirstItemIndex([{ items: ["a", "b", "c"] }]);
    expect(after).toBe(before);
    expect(after).toBe(FIRST_ITEM_INDEX_BASE);
  });

  it("decreases the index by the older page's item count", () => {
    const newest = { items: ["a", "b"] };
    const older = { items: ["x", "y", "z"] };
    expect(computeFirstItemIndex([newest, older])).toBe(
      FIRST_ITEM_INDEX_BASE - 3,
    );
  });

  it("sums the item counts of multiple older pages", () => {
    const pages = [
      { items: ["n1"] }, // newest page — never subtracted
      { items: ["o1", "o2"] }, // older page #1 → 2
      { items: ["o3", "o4", "o5"] }, // older page #2 → 3
    ];
    expect(computeFirstItemIndex(pages)).toBe(FIRST_ITEM_INDEX_BASE - 5);
  });
});
