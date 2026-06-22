// =============================================================================
// PH Agent Hub — A2aServerForm Tests
// =============================================================================
// Tests for the A2A server admin create/edit form.
// Pattern: SessionToolActivation.test.tsx
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { A2aServerForm } from "./A2aServerForm";

// Hoisted mocks
const { mockCreateA2aServer, mockUpdateA2aServer } = vi.hoisted(() => ({
  mockCreateA2aServer: vi.fn(),
  mockUpdateA2aServer: vi.fn(),
}));

// Mock the admin API module
vi.mock("../../services/admin", () => ({
  createA2aServer: (...args: unknown[]) => mockCreateA2aServer(...args),
  updateA2aServer: (...args: unknown[]) => mockUpdateA2aServer(...args),
}));

// Mock Ant Design's message
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return {
    ...antd,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

const MOCK_SERVER = {
  id: "server-1",
  name: "Test Agent",
  protocol_binding: "rest",
  url: "https://agent.example.com",
  agent_card_path: "/.well-known/agent-card.json",
  auth_scheme: "bearer",
  auth_token: "",
  headers: null,
  allowed_skills: ["skill-1"],
  enabled: true,
  retry_max_attempts: 3,
  retry_backoff_base_seconds: 1,
  retry_backoff_max_seconds: 60,
  timeout_connect_seconds: 30,
  timeout_read_seconds: 300,
  timeout_stream_seconds: 600,
  circuit_breaker_threshold: 5,
  circuit_breaker_window_seconds: 60,
  circuit_breaker_cooldown_seconds: 300,
  created_at: "2025-01-01T00:00:00Z",
  updated_at: "2025-01-01T00:00:00Z",
};

function renderA2aServerForm(props: {
  open: boolean;
  server?: typeof MOCK_SERVER | null;
  onClose?: () => void;
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <A2aServerForm
        open={props.open}
        server={props.server ?? null}
        onClose={props.onClose ?? vi.fn()}
      />
    </QueryClientProvider>,
  );
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 300));
  });
}

describe("A2aServerForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("renders create form with name and URL fields", async () => {
    renderA2aServerForm({ open: true });
    await settle();

    expect(screen.getByText(/add a2a server/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/server name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/base url/i)).toBeInTheDocument();
  });

  it("shows resilience configuration fields", async () => {
    renderA2aServerForm({ open: true });
    await settle();

    // Open the advanced/resilience section
    const summary = screen.getByText(/advanced \/ resilience/i);
    await userEvent.click(summary);
    await settle();

    // Retry config fields
    expect(screen.getByLabelText(/max retry attempts/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/retry backoff base/i)).toBeInTheDocument();

    // Timeout config fields
    expect(screen.getByLabelText(/connect timeout/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/read timeout/i)).toBeInTheDocument();

    // Circuit breaker config fields
    expect(screen.getByLabelText(/circuit breaker threshold/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/circuit breaker window/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/circuit breaker cooldown/i)).toBeInTheDocument();
  });

  it("renders edit form with pre-filled values", async () => {
    renderA2aServerForm({ open: true, server: MOCK_SERVER });
    await settle();

    expect(screen.getByDisplayValue("Test Agent")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://agent.example.com")).toBeInTheDocument();
  });
});
