// =============================================================================
// PH Agent Hub — A2aCallLogList Tests
// =============================================================================
// Tests for the A2A call log admin list component.
// Pattern: A2aServerList.test.tsx
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { A2aCallLogList } from "./A2aCallLogList";

// ---------------------------------------------------------------------------
// Hoisted mocks
// ---------------------------------------------------------------------------
const {
  mockListA2aCallLogs,
  mockListA2aServers,
} = vi.hoisted(() => ({
  mockListA2aCallLogs: vi.fn(),
  mockListA2aServers: vi.fn(),
}));

// Mock the admin API module
vi.mock("../../services/admin", () => ({
  listA2aCallLogs: (...args: unknown[]) => mockListA2aCallLogs(...args),
  listA2aServers: (...args: unknown[]) => mockListA2aServers(...args),
}));

// Mock Ant Design's Grid useBreakpoint (desktop mode)
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return {
    ...antd,
    Grid: {
      useBreakpoint: () => ({ xs: false, sm: true, md: true, lg: true, xl: true }),
    },
  };
});

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------
const MOCK_CALL_LOGS = [
  {
    id: "log-1",
    tenant_id: "tenant-1",
    a2a_server_id: "server-1",
    a2a_server_name: "Research Agent",
    skill_id: "skill-search",
    session_id: "session-1",
    trace_id: "trace-abc-123",
    status: "success",
    latency_ms: 450,
    retry_count: 0,
    error_message: null,
    created_at: "2026-06-22T10:00:00Z",
  },
  {
    id: "log-2",
    tenant_id: "tenant-1",
    a2a_server_id: "server-2",
    a2a_server_name: "Data Agent",
    skill_id: "skill-analyze",
    session_id: "session-2",
    trace_id: "trace-def-456",
    status: "error",
    latency_ms: 3200,
    retry_count: 3,
    error_message: "Connection refused: upstream service unavailable",
    created_at: "2026-06-22T09:30:00Z",
  },
  {
    id: "log-3",
    tenant_id: "tenant-1",
    a2a_server_id: "server-3",
    a2a_server_name: "Slow Agent",
    skill_id: null,
    session_id: null,
    trace_id: "trace-ghi-789",
    status: "timeout",
    latency_ms: 30000,
    retry_count: 5,
    error_message: "Request timed out after 30s",
    created_at: "2026-06-22T09:00:00Z",
  },
  {
    id: "log-4",
    tenant_id: "tenant-1",
    a2a_server_id: "server-4",
    a2a_server_name: "Broken Agent",
    skill_id: "skill-calc",
    session_id: "session-4",
    trace_id: "trace-jkl-012",
    status: "circuit_open",
    latency_ms: null,
    retry_count: 7,
    error_message: "Circuit breaker open: 5 consecutive failures in 60s window",
    created_at: "2026-06-22T08:00:00Z",
  },
];

const MOCK_SERVERS = [
  { id: "server-1", name: "Research Agent" },
  { id: "server-2", name: "Data Agent" },
  { id: "server-3", name: "Slow Agent" },
  { id: "server-4", name: "Broken Agent" },
];

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------
function renderA2aCallLogList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <A2aCallLogList />
      </BrowserRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("A2aCallLogList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("renders the call log table with data", async () => {
    mockListA2aCallLogs.mockResolvedValue({
      items: MOCK_CALL_LOGS,
      total: 4,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListA2aServers.mockResolvedValue({
      items: MOCK_SERVERS,
      total: 4,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      expect(screen.getByText("Research Agent")).toBeInTheDocument();
    });
    expect(screen.getByText("Data Agent")).toBeInTheDocument();
    expect(screen.getByText("Slow Agent")).toBeInTheDocument();
    expect(screen.getByText("Broken Agent")).toBeInTheDocument();
  });

  it("displays correct status tags with colors", async () => {
    mockListA2aCallLogs.mockResolvedValue({
      items: MOCK_CALL_LOGS,
      total: 4,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListA2aServers.mockResolvedValue({
      items: MOCK_SERVERS,
      total: 4,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      expect(screen.getByText("Success")).toBeInTheDocument();
    });
    expect(screen.getByText("Timeout")).toBeInTheDocument();
    expect(screen.getByText("Circuit Open")).toBeInTheDocument();
    const errorElements = screen.getAllByText("Error");
    expect(errorElements.length).toBeGreaterThanOrEqual(1);
  });

  it("renders server and status filter dropdowns", async () => {
    mockListA2aCallLogs.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 0,
    });
    mockListA2aServers.mockResolvedValue({
      items: MOCK_SERVERS,
      total: 4,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      const comboboxes = screen.getAllByRole("combobox");
      expect(comboboxes.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("shows empty state when no data", async () => {
    mockListA2aCallLogs.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 0,
    });
    mockListA2aServers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
      total_pages: 0,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      expect(screen.getByText("No A2A call logs found")).toBeInTheDocument();
    });
  });

  it("renders error message text for failed calls", async () => {
    mockListA2aCallLogs.mockResolvedValue({
      items: [MOCK_CALL_LOGS[1]],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListA2aServers.mockResolvedValue({
      items: [MOCK_SERVERS[1]],
      total: 1,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      expect(screen.getByText(/Connection refused/)).toBeInTheDocument();
    });
  });

  it("calls API on initial render", async () => {
    mockListA2aCallLogs.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
      total_pages: 0,
    });
    mockListA2aServers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
      total_pages: 0,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      expect(mockListA2aCallLogs).toHaveBeenCalledTimes(1);
    });
    expect(mockListA2aServers).toHaveBeenCalledTimes(1);
  });

  it("renders data with single item", async () => {
    const singleLog = {
      id: "log-1",
      tenant_id: "tenant-1",
      a2a_server_id: "server-1",
      a2a_server_name: "Lone Agent",
      skill_id: "skill-xyz",
      session_id: null,
      trace_id: "trace-single-001",
      status: "success",
      latency_ms: 150,
      retry_count: 0,
      error_message: null,
      created_at: "2026-06-22T10:00:00Z",
    };
    mockListA2aCallLogs.mockResolvedValue({
      items: [singleLog],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListA2aServers.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
      total_pages: 0,
    });

    renderA2aCallLogList();

    await waitFor(() => {
      expect(screen.getByText("Lone Agent")).toBeInTheDocument();
    });
    expect(screen.getByText("Success")).toBeInTheDocument();
  });
});
