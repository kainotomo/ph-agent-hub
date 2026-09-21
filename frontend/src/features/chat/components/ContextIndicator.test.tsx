// =============================================================================
// ContextIndicator — Unit Tests
// =============================================================================
// Tests cover: severity bands, formatTokenCount, percentage rendering,
// loading/error/unconfigured states, critical warning icon, aria-label,
// and popover interaction.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { ContextIndicator, bandForPercentage, arcColorForBand, formatTokenCount } from "./ContextIndicator";

// ---------------------------------------------------------------------------
// Mock getSessionContext (hoisted by vitest)
// ---------------------------------------------------------------------------

vi.mock("../services/chat", () => ({
  getSessionContext: vi.fn(),
  summarizeSession: vi.fn().mockResolvedValue({
    summarized_message_count: 5,
    tokens_saved: 12000,
  }),
}));

// Import the mocked function
import { getSessionContext } from "../services/chat";

const mockGetSessionContext = vi.mocked(getSessionContext);

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// bandForPercentage
// ---------------------------------------------------------------------------

describe("bandForPercentage", () => {
  it("returns normal for 0", () => {
    expect(bandForPercentage(0)).toBe("normal");
  });
  it("returns normal for 59.9", () => {
    expect(bandForPercentage(59.9)).toBe("normal");
  });
  it("returns elevated for 60", () => {
    expect(bandForPercentage(60)).toBe("elevated");
  });
  it("returns elevated for 74.9", () => {
    expect(bandForPercentage(74.9)).toBe("elevated");
  });
  it("returns critical for 75", () => {
    expect(bandForPercentage(75)).toBe("critical");
  });
  it("returns critical for 100", () => {
    expect(bandForPercentage(100)).toBe("critical");
  });
});

// ---------------------------------------------------------------------------
// arcColorForBand
// ---------------------------------------------------------------------------

describe("arcColorForBand", () => {
  it("returns normal blue", () => {
    expect(arcColorForBand("normal")).toBe("#1677ff");
  });
  it("returns elevated darker blue", () => {
    expect(arcColorForBand("elevated")).toBe("#0958d9");
  });
  it("returns critical darkest blue", () => {
    expect(arcColorForBand("critical")).toBe("#003eb3");
  });
});

// ---------------------------------------------------------------------------
// formatTokenCount
// ---------------------------------------------------------------------------

describe("formatTokenCount", () => {
  it("returns 999 as-is", () => {
    expect(formatTokenCount(999)).toBe("999");
  });
  it("returns 4400 as 4.4k", () => {
    expect(formatTokenCount(4400)).toBe("4.4k");
  });
  it("returns 128000 as 128k (no trailing .0)", () => {
    expect(formatTokenCount(128000)).toBe("128k");
  });
  it("returns 1000000 as 1m (no trailing .0)", () => {
    expect(formatTokenCount(1000000)).toBe("1m");
  });
  it("returns 1500000 as 1.5m", () => {
    expect(formatTokenCount(1500000)).toBe("1.5m");
  });
  it("returns 1000 as 1k", () => {
    expect(formatTokenCount(1000)).toBe("1k");
  });
});

// ---------------------------------------------------------------------------
// Rendering — percentage 0 and 100
// ---------------------------------------------------------------------------

describe("percentage rendering", () => {
  it("renders 0% label when percentage is 0", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 0,
      context_length: 128000,
      percentage: 0,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.getByTestId("context-indicator-label")).toHaveTextContent("0%");
  });

  it("renders 100% label when percentage is 100", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 128000,
      context_length: 128000,
      percentage: 100,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.getByTestId("context-indicator-label")).toHaveTextContent("100%");
  });
});

// ---------------------------------------------------------------------------
// Unconfigured state
// ---------------------------------------------------------------------------

describe("unconfigured state", () => {
  it("shows unconfigured when context_length is null", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 0,
      context_length: null,
      percentage: null,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.getByTestId("context-indicator-unconfigured")).toBeInTheDocument();
    expect(screen.queryByTestId("context-indicator-ring")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Loading state
// ---------------------------------------------------------------------------

describe("loading state", () => {
  it("shows loading indicator while query is pending", async () => {
    mockGetSessionContext.mockReturnValue(new Promise(() => {})); // never resolves
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("context-indicator-loading")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Error state
// ---------------------------------------------------------------------------

describe("error state", () => {
  it("shows error indicator when query fails", async () => {
    mockGetSessionContext.mockRejectedValue(new Error("Network error"));
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.getByTestId("context-indicator-error")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Critical warning icon
// ---------------------------------------------------------------------------

describe("critical warning icon", () => {
  it("renders warning icon when band is critical (≥75%)", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 96000,
      context_length: 128000,
      percentage: 75,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.getByTestId("context-indicator-warning")).toBeInTheDocument();
  });

  it("does not render warning icon when band is elevated (60%)", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 76800,
      context_length: 128000,
      percentage: 60,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.queryByTestId("context-indicator-warning")).not.toBeInTheDocument();
  });

  it("does not render warning icon when band is normal (50%)", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 64000,
      context_length: 128000,
      percentage: 50,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.queryByTestId("context-indicator-warning")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// aria-label
// ---------------------------------------------------------------------------

describe("aria-label", () => {
  it("carries exact percentage and token counts", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 48000,
      context_length: 128000,
      percentage: 37.5,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    const btn = screen.getByRole("button");
    expect(btn).toHaveAttribute(
      "aria-label",
      "Context window: 38% used, 48k of 128k tokens. Opens context details and compaction.",
    );
  });
});

// ---------------------------------------------------------------------------
// Popover interaction
// ---------------------------------------------------------------------------

describe("popover interaction", () => {
  it("click opens the popover", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 48000,
      context_length: 128000,
      percentage: 37.5,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    const user = userEvent.setup();
    await act(async () => {
      await user.click(screen.getByRole("button"));
    });
    expect(screen.getByText("Context Window")).toBeInTheDocument();
    expect(screen.getByText("Compact Conversation")).toBeInTheDocument();
    expect(screen.getByText("Auto-compact at 75% usage")).toBeInTheDocument();
  });

  it("Compact Conversation button is present in popover", async () => {
    mockGetSessionContext.mockResolvedValue({
      tokens_used: 48000,
      context_length: 128000,
      percentage: 37.5,
    });
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ContextIndicator sessionId="s1" />
      </QueryClientProvider>,
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    const user = userEvent.setup();
    await act(async () => {
      await user.click(screen.getByRole("button"));
    });
    expect(screen.getByText("Compact Conversation")).toBeInTheDocument();
  });
});
