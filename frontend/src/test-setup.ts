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

// This environment does not expose a working localStorage (Node's
// experimental implementation shadows jsdom's unless --localstorage-file is
// passed).  Storage-backed UI — e.g. remembered folder collapse state
// (Issue #526) — needs a real one to be testable.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
      setItem: (key: string, value: string) => {
        store.set(key, String(value));
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
      clear: () => {
        store.clear();
      },
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}
