import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TokenUsageButton } from "./TokenUsageButton";

const baseUsage = {
  turns: 10,
  steps: 42,
  tokens_in: 1755349,
  tokens_out: 36808,
  tokens_total: 1798557,
  cached_input_tokens: 1718912,
  uncached_input_tokens: 42837,
  cache_hit_percent: 97.6,
  llm_time_ms: 12000,
  tool_time_ms: 3000,
  avg_ttft_ms: 250,
  tps: 150,
  has_timing_data: true,
};

describe("TokenUsageButton", () => {
  it("renders populated button label with DSH numbers", () => {
    render(
      <TokenUsageButton usage={baseUsage} isLoading={false} isError={false} />
    );

    const button = screen.getByTestId("token-usage-button");
    expect(button).toHaveTextContent("1.8M tok");
    expect(button).toHaveTextContent("Cache hit 98%");
  });

  it("opens popover and shows all token usage details", async () => {
    render(
      <TokenUsageButton usage={baseUsage} isLoading={false} isError={false} />
    );

    const button = screen.getByTestId("token-usage-button");
    await userEvent.click(button);

    expect(screen.getByTestId("token-usage-popover")).toBeInTheDocument();
    expect(screen.getByText("Token usage")).toBeInTheDocument();
    expect(screen.getByText("1,798,557 tok total")).toBeInTheDocument();
    expect(screen.getByText("98%")).toBeInTheDocument();
    expect(screen.getByText("42,837 tok")).toBeInTheDocument();
    expect(screen.getByText("1,718,912 tok")).toBeInTheDocument();
    expect(screen.getByText("36,808 tok")).toBeInTheDocument();
  });

  it("shows '—' when cache_hit_percent is null", async () => {
    const nullUsage = { ...baseUsage, cache_hit_percent: null };
    render(
      <TokenUsageButton usage={nullUsage} isLoading={false} isError={false} />
    );

    const button = screen.getByTestId("token-usage-button");
    expect(button).toHaveTextContent("Cache hit —");

    // Open popover
    await userEvent.click(button);
    // Use document.querySelector for popover (antd Popover renders in a portal)
    const popover = document.querySelector('[data-testid="token-usage-popover"]');
    expect(popover).toBeInTheDocument();
    expect(popover!.textContent).toContain("—");
  });

  it("shows loading state when isLoading is true", () => {
    render(
      <TokenUsageButton usage={baseUsage} isLoading={true} isError={false} />
    );

    expect(screen.getByTestId("token-usage-loading")).toBeInTheDocument();
  });

  it("shows error state when isError is true", () => {
    render(
      <TokenUsageButton usage={baseUsage} isLoading={false} isError={true} />
    );

    expect(screen.getByTestId("token-usage-error")).toBeInTheDocument();
  });
});
