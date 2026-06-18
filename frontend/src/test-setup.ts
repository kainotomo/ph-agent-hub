// =============================================================================
// Vitest Setup — runs before every test file
// =============================================================================
// 1. Import jest-dom matchers (toBeInTheDocument, toHaveTextContent, etc.)
// 2. Polyfill matchMedia (Ant Design, @refinedev/antd depend on it)
// =============================================================================

import "@testing-library/jest-dom/vitest";

// Ant Design / refine use window.matchMedia for responsive breakpoints.
// jsdom does not implement it, so we provide a minimal stub.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
