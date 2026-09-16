// =============================================================================
// PH Agent Hub — Message paging helpers
// =============================================================================
// react-virtuoso's `firstItemIndex` contract: the index must stay UNCHANGED
// when items are appended at the tail and DECREASE by exactly the number of
// items PREPENDED at the head.
//
// `pages[0]` is the newest page and the items in `pages[1..]` are older, so
// they are the only items prepended at the head of the chronological display
// array (which is built by reversing `pages`).  Appending a new message at the
// tail only grows `pages[0]` (index unchanged); loading an older page grows
// `pages[1..]` (index decreases by exactly the prepended count).  This
// satisfies react-virtuoso's `firstItemIndex` contract.
//
// Starting from a large positive base keeps the index non-negative for any
// realistic number of prepended pages.
// =============================================================================

/** Large positive base so decrementing for prepends never goes negative. */
export const FIRST_ITEM_INDEX_BASE = 1_000_000;

/**
 * Derive react-virtuoso's `firstItemIndex` from an infinite-query page array.
 *
 * All items in `pages[1..]` sit at the head of the chronological display
 * array, so they are subtracted from the base.  The number is therefore
 * constant while only the newest page (`pages[0]`) grows and drops by exactly
 * the prepended count when an older page loads.
 */
export function computeFirstItemIndex(
  pages: Array<{ items: unknown[] }> | undefined,
): number {
  let prepended = 0;
  for (let i = 1; i < (pages?.length ?? 0); i++) {
    prepended += pages![i].items.length;
  }
  return FIRST_ITEM_INDEX_BASE - prepended;
}
