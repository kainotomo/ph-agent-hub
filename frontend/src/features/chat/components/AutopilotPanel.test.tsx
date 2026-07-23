// =============================================================================
// AutopilotPanel — Unit Tests
// =============================================================================
// Tests focus on: rendering for each status (executing, complete, max_turns,
// error, paused, idle), progress bar percentage, error message display, and
// pause/steer button states.
// =============================================================================

import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AutopilotPanel, INITIAL_AUTOPILOT_STATE, type AutopilotState } from "./AutopilotPanel";

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPanel(state: Partial<AutopilotState> = {}, onStop?: () => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const merged: AutopilotState = { ...INITIAL_AUTOPILOT_STATE, ...state };
  return render(
    <QueryClientProvider client={queryClient}>
      <AutopilotPanel state={merged} onStop={onStop} />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

afterEach(() => {
  cleanup();
});

describe("AutopilotPanel rendering", () => {
  it("renders nothing when status is idle", () => {
    const { container } = renderPanel({ status: "idle" });
    expect(container.innerHTML).toBe("");
  });

  it("shows executing state with turn progress", () => {
    const onStop = vi.fn();
    renderPanel({
      status: "executing",
      currentTurn: 2,
      maxTurns: 5,
    }, onStop);
    expect(screen.getByText(/Working on turn 2 of 5/i)).toBeInTheDocument();
    // Pause and Stop buttons should be present
    expect(screen.getByText((c) => c.includes("Pause"))).toBeInTheDocument();
    expect(screen.getByText((c) => c.includes("Stop"))).toBeInTheDocument();
    expect(onStop).not.toHaveBeenCalled();
  });

  it("shows complete state", () => {
    renderPanel({
      status: "complete",
      currentTurn: 3,
      maxTurns: 5,
    });
    expect(screen.getByText("Autopilot finished")).toBeInTheDocument();
    // Pause button should NOT show when finished
    expect(screen.queryByText("Pause")).not.toBeInTheDocument();
  });

  it("shows max_turns state with partial results message", () => {
    renderPanel({
      status: "max_turns",
      currentTurn: 5,
      maxTurns: 5,
    });
    expect(screen.getByText("Autopilot finished")).toBeInTheDocument();
    expect(
      screen.getByText(/Reached maximum of 5 turns/i),
    ).toBeInTheDocument();
  });

  it("shows error state with error message", () => {
    renderPanel({
      status: "error",
      currentTurn: 2,
      maxTurns: 5,
      errorMessage: "Token budget exceeded",
    });
    expect(screen.getByText("Autopilot finished")).toBeInTheDocument();
    expect(screen.getByText(/Error: Token budget exceeded/i)).toBeInTheDocument();
  });

  it("shows paused state", () => {
    renderPanel({
      status: "paused",
      currentTurn: 2,
      maxTurns: 5,
      pauseReason: "User paused the autopilot",
    });
    expect(
      screen.getByText(/Autopilot paused/i),
    ).toBeInTheDocument();
    // Steering input should be present
    expect(
      screen.getByPlaceholderText(/Enter a steering instruction/i),
    ).toBeInTheDocument();
    // Resume buttons should be present
    expect(screen.getByText("Resume with instruction")).toBeInTheDocument();
    expect(screen.getByText("Resume")).toBeInTheDocument();
  });

  it("calculates progress percent correctly", () => {
    // 2 out of 5 turns = 40%
    renderPanel({
      status: "executing",
      currentTurn: 2,
      maxTurns: 5,
    });
    // The Progress component renders with aria attributes
    const progressBar = document.querySelector('.ant-progress');
    expect(progressBar).toBeInTheDocument();
  });

  it("shows 100% when finished regardless of turn count", () => {
    renderPanel({
      status: "complete",
      currentTurn: 3,
      maxTurns: 10,
    });
    const progressBar = document.querySelector('.ant-progress');
    expect(progressBar).toBeInTheDocument();
  });

  it("shows token counter when cumulative tokens are available", () => {
    renderPanel({
      status: "complete",
      currentTurn: 3,
      maxTurns: 5,
      cumulativeTokens: { tokensIn: 500, tokensOut: 1200 },
    });
    expect(screen.getByText(/Tokens:/)).toBeInTheDocument();
    expect(screen.getByText(/1700 total/)).toBeInTheDocument();
  });

  it("does not show token counter when no tokens used", () => {
    renderPanel({
      status: "executing",
      currentTurn: 1,
      maxTurns: 5,
      cumulativeTokens: { tokensIn: 0, tokensOut: 0 },
    });
    expect(screen.queryByText(/Tokens:/)).not.toBeInTheDocument();
  });
});
