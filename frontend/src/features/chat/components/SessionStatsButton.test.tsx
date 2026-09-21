import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionStatsButton } from "./SessionStatsButton";

const fullUsage = {
  turns: 42,
  steps: 17,
  tokens_in: 12000,
  tokens_out: 8500,
  tokens_total: 20500,
  cached_input_tokens: 5000,
  uncached_input_tokens: 7000,
  cache_hit_percent: 41,
  llm_time_ms: 198000,
  tool_time_ms: 275000,
  avg_ttft_ms: 1200,
  tps: 243.4,
  has_timing_data: true,
};

const legacyUsage = {
  ...fullUsage,
  llm_time_ms: 0,
  tool_time_ms: 0,
  avg_ttft_ms: null,
  tps: null,
  has_timing_data: false,
};

describe("SessionStatsButton", () => {
  it("shows formatted stats when all fields are populated", async () => {
    render(
      <SessionStatsButton usage={fullUsage} isLoading={false} isError={false} />,
    );

    // The button label should be visible
    expect(screen.getByText(/42 turns · 17 steps · 243 tok\/s/)).toBeInTheDocument();

    // Click the button to open the popover
    await userEvent.click(screen.getByTestId("session-stats-button"));

    // Popover content should be visible
    expect(screen.getByText("Session statistics")).toBeInTheDocument();
    expect(screen.getByText("LLM time")).toBeInTheDocument();
    expect(screen.getByText("Tool time")).toBeInTheDocument();
    expect(screen.getByText("Avg time to first token (TTFT)")).toBeInTheDocument();
    expect(screen.getByText("Tokens per second (TPS)")).toBeInTheDocument();

    // Verify the formatted values
    expect(screen.getByText("3m18s")).toBeInTheDocument();
    expect(screen.getByText("4m35s")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();
    expect(screen.getByText("243 tok/s")).toBeInTheDocument();
  });

  it("shows em-dashes for all timing values when has_timing_data is false", async () => {
    render(
      <SessionStatsButton usage={legacyUsage} isLoading={false} isError={false} />,
    );

    // Click the button to open the popover
    await userEvent.click(screen.getByTestId("session-stats-button"));

    // All four values should be "—" (em dash)
    const emDashes = screen.getAllByText("\u2014");
    expect(emDashes.length).toBe(4);
  });

  it("shows loading state when isLoading is true", () => {
    render(<SessionStatsButton isLoading={true} isError={false} />);
    expect(screen.getByTestId("session-stats-loading")).toBeInTheDocument();
  });

  it("shows error state when isError is true", () => {
    render(<SessionStatsButton isError={true} isLoading={false} />);
    expect(screen.getByTestId("session-stats-error")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });
});
