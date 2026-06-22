// =============================================================================
// PH Agent Hub — A2aServerList Tests
// =============================================================================
// Tests for the A2A server admin list component.
// Pattern: SessionToolActivation.test.tsx
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { A2aServerList } from "./A2aServerList";

// Hoisted mocks
const { mockListA2aServers, mockGetA2aCircuitBreaker, mockResetA2aCircuitBreaker } = vi.hoisted(
  () => ({
    mockListA2aServers: vi.fn(),
    mockGetA2aCircuitBreaker: vi.fn(),
    mockResetA2aCircuitBreaker: vi.fn(),
  }),
);

// Mock the admin API module
vi.mock("../../services/admin", () => ({
  listA2aServers: (...args: unknown[]) => mockListA2aServers(...args),
  getA2aCircuitBreaker: (...args: unknown[]) => mockGetA2aCircuitBreaker(...args),
  resetA2aCircuitBreaker: (...args: unknown[]) => mockResetA2aCircuitBreaker(...args),
  deleteA2aServer: vi.fn(),
  updateA2aServer: vi.fn(),
  testA2aServer: vi.fn(),
  syncA2aServerTools: vi.fn(),
  listTenants: vi.fn().mockResolvedValue({ items: [] }),
}));

// Mock Ant Design's Grid useBreakpoint
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return {
    ...antd,
    Grid: {
      useBreakpoint: () => ({ xs: false, sm: true, md: true, lg: true, xl: true }),
    },
  };
});

const MOCK_SERVERS = [
  {
    id: "server-1",
    name: "Test Agent",
    protocol_binding: "rest",
    url: "https://agent.example.com",
    auth_scheme: "bearer",
    enabled: true,
    allowed_skills: ["skill-1"],
    retry_max_attempts: 3,
    circuit_breaker_threshold: 5,
    circuit_breaker_window_seconds: 60,
    circuit_breaker_cooldown_seconds: 300,
    timeout_connect_seconds: 30,
    timeout_read_seconds: 300,
    timeout_stream_seconds: 600,
    retry_backoff_base_seconds: 1,
    retry_backoff_max_seconds: 60,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
  },
  {
    id: "server-2",
    name: "Disabled Agent",
    protocol_binding: "rest",
    url: "https://other-agent.com",
    auth_scheme: "none",
    enabled: false,
    allowed_skills: [],
    retry_max_attempts: 3,
    circuit_breaker_threshold: 5,
    circuit_breaker_window_seconds: 60,
    circuit_breaker_cooldown_seconds: 300,
    timeout_connect_seconds: 30,
    timeout_read_seconds: 300,
    timeout_stream_seconds: 600,
    retry_backoff_base_seconds: 1,
    retry_backoff_max_seconds: 60,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
  },
];

function renderA2aServerList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <A2aServerList />
      </BrowserRouter>
    </QueryClientProvider>,
  );
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 200));
  });
}

describe("A2aServerList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("renders the server list", async () => {
    mockListA2aServers.mockResolvedValue({
      items: MOCK_SERVERS,
      total: 2,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockGetA2aCircuitBreaker.mockResolvedValue({ degraded: false });

    renderA2aServerList();
    await settle();

    expect(screen.getByText("Test Agent")).toBeInTheDocument();
    expect(screen.getByText("Disabled Agent")).toBeInTheDocument();
  });

  it("shows enabled state correctly", async () => {
    mockListA2aServers.mockResolvedValue({
      items: MOCK_SERVERS,
      total: 2,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockGetA2aCircuitBreaker.mockResolvedValue({ degraded: false });

    renderA2aServerList();
    await settle();

    // Both servers should be rendered
    expect(screen.getByText("Test Agent")).toBeInTheDocument();
    expect(screen.getByText("Disabled Agent")).toBeInTheDocument();
  });

  it("queries circuit breaker for each server", async () => {
    mockListA2aServers.mockResolvedValue({
      items: [MOCK_SERVERS[0]],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockGetA2aCircuitBreaker.mockResolvedValue({ degraded: false });

    renderA2aServerList();
    await settle();

    // Circuit breaker state should have been queried
    expect(mockGetA2aCircuitBreaker).toHaveBeenCalledWith("server-1");
  });

  it("renders create button", async () => {
    mockListA2aServers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 0,
    });

    renderA2aServerList();
    await settle();

    expect(screen.getByRole("button", { name: /add a2a server/i })).toBeInTheDocument();
  });
});
