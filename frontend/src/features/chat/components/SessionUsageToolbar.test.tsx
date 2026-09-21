// =============================================================================
// PH Agent Hub — SessionUsageToolbar tests
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SessionUsageToolbar from "./SessionUsageToolbar";

// ---------------------------------------------------------------------------
// Mock the entire chat service module
// ---------------------------------------------------------------------------

const mockGetSessionUsage = vi.fn();
const mockGetSessionContext = vi.fn();
const mockSummarizeSession = vi.fn();

vi.mock("../services/chat", () => ({
  getSessionUsage: (...args: unknown[]) => mockGetSessionUsage(...args),
  getSessionContext: (...args: unknown[]) => mockGetSessionContext(...args),
  summarizeSession: (...args: unknown[]) => mockSummarizeSession(...args),
  // Re-export types
  SessionUsageData: undefined,
  SessionContextData: undefined,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

function renderWithProvider(ui: React.ReactElement) {
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const mockSessionId = "test-session-123";

const mockUsageData = {
  turns: 2,
  steps: 39,
  tokens_in: 1200,
  tokens_out: 800,
  tokens_total: 1798557,
  cached_input_tokens: 1796000,
  uncached_input_tokens: 2557,
  cache_hit_percent: 97.6,
  llm_time_ms: 4500,
  tool_time_ms: 1200,
  avg_ttft_ms: 320,
  tps: 243.4,
  has_timing_data: true,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SessionUsageToolbar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
  });

  it("renders null when no sessionId is provided", () => {
    renderWithProvider(<SessionUsageToolbar />);
    expect(screen.queryByTestId("session-usage-toolbar")).toBeNull();
    expect(screen.queryByTestId("session-stats-button")).toBeNull();
    expect(screen.queryByTestId("token-usage-button")).toBeNull();
  });

  it("renders all three children when usage data is loaded", async () => {
    mockGetSessionUsage.mockResolvedValue(mockUsageData);
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 1200,
      context_length: 2000,
      percentage: 60,
      system_prompt_tokens: 500,
      tool_definition_tokens: 300,
      messages_tokens: 400,
    });

    renderWithProvider(
      <SessionUsageToolbar sessionId={mockSessionId} />,
    );

    // Wait for the query to resolve and all children to render
    await waitFor(() => {
      expect(screen.getByTestId("session-stats-button")).toBeInTheDocument();
    });

    // Toolbar container should be present
    expect(screen.getByTestId("session-usage-toolbar")).toBeInTheDocument();

    // Keeps a small gap from the message input above (Issue #531 QA).
    expect(
      (screen.getByTestId("session-usage-toolbar") as HTMLElement).style.marginTop,
    ).toBe("8px");

    // SessionStatsButton
    expect(screen.getByTestId("session-stats-button")).toBeInTheDocument();

    // TokenUsageButton
    expect(screen.getByTestId("token-usage-button")).toBeInTheDocument();

    // ContextIndicator ring (stable inner element)
    expect(screen.getByTestId("context-indicator-ring")).toBeInTheDocument();

    // ContextIndicator (the Popover wrapper)
    const contextIndicator = screen.queryByTestId("context-indicator");
    if (contextIndicator) {
      expect(contextIndicator).toBeInTheDocument();
    }
  });

  it("shows error states when getSessionUsage rejects", async () => {
    mockGetSessionUsage.mockRejectedValue(new Error("Service unavailable"));
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 1200,
      context_length: 2000,
      percentage: 60,
      system_prompt_tokens: 500,
      tool_definition_tokens: 300,
      messages_tokens: 400,
    });

    renderWithProvider(
      <SessionUsageToolbar sessionId={mockSessionId} />,
    );

    // Wait for the query to resolve (error state)
    await waitFor(() => {
      expect(screen.getByTestId("session-stats-error")).toBeInTheDocument();
    });

    // Toolbar container is still present (component didn't crash)
    expect(screen.getByTestId("session-usage-toolbar")).toBeInTheDocument();

    // Error states on both buttons
    expect(screen.getByTestId("session-stats-error")).toBeInTheDocument();
    expect(screen.getByTestId("token-usage-error")).toBeInTheDocument();
  });
});
