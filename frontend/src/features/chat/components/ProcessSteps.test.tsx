import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProcessSteps } from "./ProcessSteps";

// --- Mock module --------------------------------------------------------
// vi.mock is hoisted, so we use vi.hoisted for the shared state.
const mocked = vi.hoisted(() => ({
  fetchPromise: null as Promise<any> | null,
  resolveFetch: null as ((v: any) => void) | null,
}));

vi.mock("../services/chat", async () => {
  if (!mocked.fetchPromise) {
    mocked.fetchPromise = new Promise<any>((resolve) => {
      mocked.resolveFetch = resolve;
    });
  }
  return {
    getMessageStep: vi.fn(() => mocked.fetchPromise),
  };
});

import { getMessageStep } from "../services/chat";

function createQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function renderProcessSteps(props: Partial<React.ComponentProps<typeof ProcessSteps>>) {
  const queryClient = createQueryClient();
  const defaultProps: React.ComponentProps<typeof ProcessSteps> = {
    steps: [],
    sessionId: "session-1",
    messageId: "msg-1",
    ...props,
  };
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <ProcessSteps {...defaultProps} />
      </QueryClientProvider>,
    ),
  };
}

function resetFetchPromise() {
  if (mocked.resolveFetch) {
    mocked.resolveFetch!({ index: 0, type: "reasoning", full_text: "resolved" });
    mocked.resolveFetch = null;
  }
  mocked.fetchPromise = new Promise<any>((resolve) => {
    mocked.resolveFetch = resolve;
  });
}

describe("ProcessSteps", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetFetchPromise();
  });

  afterEach(() => {
    resetFetchPromise();
  });

  it("renders nothing when no steps", () => {
    renderProcessSteps({ steps: [] });
    const buttons = screen.queryAllByRole("button");
    expect(buttons.length).toBe(0);
  });

  it("shows fold header when there are steps", () => {
    const steps = [
      { kind: "reasoning" as const, key: "reasoning:0", index: 0, summary: "Thinking…" },
      { kind: "tool_call" as const, key: "tool_call:1", index: 1, name: "search" },
      { kind: "tool_result" as const, key: "tool_result:2", index: 2, name: "search" },
    ];
    renderProcessSteps({ steps });
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("shows descriptive text in fold header when there are multiple steps", () => {
    const steps = [
      { kind: "reasoning" as const, key: "reasoning:0", index: 0, summary: "Thinking…" },
      { kind: "tool_call" as const, key: "tool_call:1", index: 1, name: "search" },
      { kind: "tool_result" as const, key: "tool_result:2", index: 2, name: "search" },
    ];
    renderProcessSteps({ steps });
    const button = screen.getByRole("button");
    // With reasoning + tools, summarizeProcess returns "Thought for a while - 1 tool call"
    expect(button.textContent).toContain("1 tool call");
  });

  it("opens and closes the fold on click", async () => {
    const user = userEvent.setup();
    const steps = [
      { kind: "reasoning" as const, key: "reasoning:0", index: 0, summary: "Thinking…" },
    ];
    renderProcessSteps({ steps });

    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-expanded", "false");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  describe("lazy-fetch behavior", () => {
    it("renders a thinking-only fold (no tool calls)", async () => {
      const user = userEvent.setup();
      const steps = [
        {
          kind: "reasoning" as const,
          key: "reasoning:0",
          index: 0,
          summary: "Only thinking here",
          chars: 999,
        },
      ];

      renderProcessSteps({ steps });

      const foldButton = screen.getByRole("button");
      await user.click(foldButton);

      // The reasoning row must exist even though there are no tool calls.
      expect(screen.getByTestId("step-row-reasoning")).toBeInTheDocument();
    });

    it("shows the summary (not just the char count) in the step header", async () => {
      const user = userEvent.setup();
      const steps = [
        {
          kind: "reasoning" as const,
          key: "reasoning:0",
          index: 0,
          summary: "Preview of thinking",
          chars: 1234,
        },
        { kind: "tool_call" as const, key: "tool_call:1", index: 1, name: "search" },
      ];

      renderProcessSteps({ steps });

      await user.click(screen.getByRole("button"));

      // Fold header is the summarizeProcess text; the step row must show the
      // reasoning preview rather than falling back to "<chars> chars".
      expect(screen.getByText("Preview of thinking")).toBeInTheDocument();
      expect(screen.queryByText("1234 chars")).not.toBeInTheDocument();
    });

    it("shows the summary and a retry affordance when the fetch fails", async () => {
      const user = userEvent.setup();
      let rejectFetch!: (e: unknown) => void;
      mocked.fetchPromise = new Promise<any>((_resolve, reject) => {
        rejectFetch = reject;
      });

      const steps = [
        {
          kind: "reasoning" as const,
          key: "reasoning:0",
          index: 0,
          summary: "Preview text",
          chars: 99,
        },
      ];

      renderProcessSteps({ steps });

      await user.click(screen.getByRole("button"));
      await user.click(screen.getByTestId("step-row-reasoning"));

      rejectFetch(new Error("network"));

      await waitFor(() => {
        expect(screen.getByTestId("step-fetch-error")).toBeInTheDocument();
      });
      expect(
        within(screen.getByTestId("step-fetch-error")).getByText("Preview text"),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });

    it("fetches full body when a reasoning step is expanded", async () => {
      const user = userEvent.setup();
      const steps = [
        { kind: "reasoning" as const, key: "reasoning:0", index: 0, summary: "..." },
      ];

      renderProcessSteps({ steps });

      // 1. Click fold button → opens fold body with step rows.
      const foldButton = screen.getByRole("button");
      await user.click(foldButton);
      expect(foldButton).toHaveAttribute("aria-expanded", "true");

      // 2. Click step row button → should trigger lazy fetch.
      const stepButton = screen.getByTestId("step-row-reasoning");
      await user.click(stepButton);

      // useQuery enabled = needsFetch && open → should have fired.
      await waitFor(() => {
        expect(getMessageStep).toHaveBeenCalledWith("session-1", "msg-1", 0);
      });

      // 3. Resolve the promise → full body renders.
      const newValue = {
        index: 0,
        type: "reasoning",
        full_text: "Deep reasoning text that was not in the summary.",
      };
      mocked.resolveFetch!(newValue);
      resetFetchPromise(); // prepare for next test

      await waitFor(() => {
        expect(
          screen.getByText("Deep reasoning text that was not in the summary."),
        ).toBeInTheDocument();
      });
    });

    it("shows loading spinner during fetch", async () => {
      const user = userEvent.setup();
      const steps = [
        { kind: "reasoning" as const, key: "reasoning:0", index: 0, summary: "..." },
      ];

      renderProcessSteps({ steps });

      // Open fold, then open step row.
      await user.click(screen.getByRole("button"));
      await user.click(screen.getByTestId("step-row-reasoning"));

      // While the promise is pending, should see loading.
      await waitFor(() => {
        expect(screen.getByText(/Loading/)).toBeInTheDocument();
      });
    });

    it("does not fetch when step already has full text", async () => {
      const user = userEvent.setup();
      const steps = [
        {
          kind: "reasoning" as const,
          key: "reasoning:0",
          index: 0,
          text: "Full reasoning text here",
          summary: "...",
        },
      ];

      renderProcessSteps({ steps });

      await user.click(screen.getAllByRole("button")[0]);
      await user.click(screen.getByTestId("step-row-reasoning"));

      // needsFetch is false because step.text exists.
      expect(getMessageStep).not.toHaveBeenCalled();
    });

    it("does not fetch when step already has full output", async () => {
      const user = userEvent.setup();
      const steps = [
        {
          kind: "tool_result" as const,
          key: "tool_result:0",
          index: 0,
          output: "result data",
          name: "search",
        },
      ];

      renderProcessSteps({ steps });

      await user.click(screen.getByRole("button"));
      await user.click(screen.getByTestId("step-row-tool_result"));

      expect(getMessageStep).not.toHaveBeenCalled();
      expect(screen.getByText("result data")).toBeInTheDocument();
    });

    it("caches fetched step within staleTime", async () => {
      const user = userEvent.setup();
      const steps = [
        { kind: "reasoning" as const, key: "reasoning:0", index: 0, summary: "..." },
      ];

      renderProcessSteps({ steps });

      // Open fold + step row.
      await user.click(screen.getByRole("button"));
      await user.click(screen.getByTestId("step-row-reasoning"));

      await waitFor(() => {
        expect(getMessageStep).toHaveBeenCalled();
      });

      // Close fold and re-open.
      await user.click(screen.getAllByRole("button")[0]);
      expect(screen.getAllByRole("button")[0]).toHaveAttribute("aria-expanded", "false");

      await user.click(screen.getAllByRole("button")[0]);
      await user.click(screen.getByTestId("step-row-reasoning"));

      // Should still be cached — no second call.
      expect(getMessageStep).toHaveBeenCalledTimes(1);
    });
  });
});
