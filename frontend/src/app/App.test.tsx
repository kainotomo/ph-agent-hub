// =============================================================================
// App — Smoke Tests
// =============================================================================
// Verifies that the root App component can be imported and rendered without
// crashing. API-dependent providers (AuthProvider, TenantProvider) are mocked
// so the test does not require a running backend.
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render } from "@testing-library/react";

// ── Mock API-dependent providers ──────────────────────────────────────────
vi.mock("../providers/AuthProvider", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => ({ user: null, loading: false }),
}));

vi.mock("../providers/TenantProvider", () => ({
  TenantProvider: ({ children }: { children: React.ReactNode}) => <>{children}</>,
  useTenant: () => ({ tenant: null, loading: false }),
}));

// Mock the demo service so LoginPage's useEffect fetch doesn't throw in jsdom
vi.mock("../features/chat/services/demo", () => ({
  getDemoStatus: () => Promise.resolve({ enabled: false }),
  createDemoSession: () => Promise.resolve({ session_id: "demo", token: "" }),
}));

// ── Tests ─────────────────────────────────────────────────────────────────
afterEach(() => {
  cleanup();
});

describe("App", () => {
  it("exports a function component", async () => {
    const mod = await import("./App");
    expect(typeof mod.default).toBe("function");
  });

  it("renders without crashing", async () => {
    const { default: App } = await import("./App");
    const { container } = render(<App />);
    // The providers mount successfully — the test passes if no error is thrown.
    expect(container).toBeTruthy();
  });
});
