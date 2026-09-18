import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MessageBubble } from "./MessageBubble";

function renderBubble(props: Partial<React.ComponentProps<typeof MessageBubble>> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MessageBubble
        message={{
          id: "msg-1",
          session_id: "session-1",
          sender: "user",
          content: [{ type: "text", text: "short" }],
          model_id: null,
          tool_calls: [],
          tokens_in: null,
          tokens_out: null,
          is_deleted: false,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        }}
        sessionId="session-1"
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("MessageBubble", () => {
  it("keeps the newest user message fully visible", () => {
    const longText = "A".repeat(2000);

    renderBubble({
      message: {
        id: "msg-latest",
        session_id: "session-1",
        sender: "user",
        content: [{ type: "text", text: longText }],
        model_id: null,
        tool_calls: [],
        tokens_in: null,
        tokens_out: null,
        is_deleted: false,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
      isLatestUserMessage: true,
    });

    expect(screen.getByText(longText)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /show more/i })).not.toBeInTheDocument();
  });

  it("collapses older long user messages and expands them on demand", async () => {
    const longText = "This is a long user message. " + "B".repeat(2400);
    const user = userEvent.setup();

    renderBubble({
      message: {
        id: "msg-old",
        session_id: "session-1",
        sender: "user",
        content: [{ type: "text", text: longText }],
        model_id: null,
        tool_calls: [],
        tokens_in: null,
        tokens_out: null,
        is_deleted: false,
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
      isLatestUserMessage: false,
    });

    const toggle = screen.getByRole("button", { name: /show more/i });
    expect(toggle).toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByRole("button", { name: /show less/i })).toBeInTheDocument();
    expect(screen.getByText(longText)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Reasoning panel — Issue #524: must stay collapsed by default
// ---------------------------------------------------------------------------

describe("MessageBubble — reasoning panel (Issue #524)", () => {
  function renderAssistantWithReasoning(overrides: Partial<React.ComponentProps<typeof MessageBubble>> = {}) {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-reason",
            session_id: "session-1",
            sender: "assistant",
            content: [
              { type: "reasoning", text: "REASONING_MARKER_XYZ" },
              { type: "text", text: "Hello, how can I help?" },
            ],
            model_id: "deepseek-r1",
            model_name: "DeepSeek R1",
            model_provider: "DeepSeek",
            tool_calls: null,
            tokens_in: 100,
            tokens_out: 200,
            is_deleted: false,
            created_at: "2024-01-01T00:00:00Z",
            updated_at: "2024-01-01T00:00:00Z",
          } as any}
          sessionId="session-1"
          {...overrides}
        />
      </QueryClientProvider>,
    );
  }

  it("reasoning panel is collapsed (not visible) on first render", () => {
    renderAssistantWithReasoning({ streaming: true });

    // Header must be visible but panel content must be absent
    expect(screen.getByText(/Reasoning \(\d+ chars\)/i)).toBeInTheDocument();
    expect(screen.queryByText("REASONING_MARKER_XYZ")).not.toBeInTheDocument();
  });

  it("reasoning panel stays collapsed while streaming", () => {
    renderAssistantWithReasoning({ streaming: true });

    expect(screen.getByText(/Reasoning \(\d+ chars\)/i)).toBeInTheDocument();
    expect(screen.queryByText("REASONING_MARKER_XYZ")).not.toBeInTheDocument();
  });

  it("reasoning panel stays collapsed when not streaming", () => {
    renderAssistantWithReasoning({ streaming: false });

    expect(screen.getByText(/Reasoning \(\d+ chars\)/i)).toBeInTheDocument();
    expect(screen.queryByText("REASONING_MARKER_XYZ")).not.toBeInTheDocument();
  });

  it("expands reasoning content on header click", async () => {
    const user = userEvent.setup();
    renderAssistantWithReasoning();

    // Should be collapsed initially
    expect(screen.queryByText("REASONING_MARKER_XYZ")).not.toBeInTheDocument();

    // Click the collapse header to expand
    const headerBtn = screen.getByRole("button", { name: /Reasoning \(\d+ chars\)/i });
    await user.click(headerBtn);

    // Panel should now be visible
    expect(screen.getByText("REASONING_MARKER_XYZ")).toBeInTheDocument();

    // Click again to collapse (jsdom needs waitFor due to CSSMotion animations)
    await user.click(headerBtn);
    await waitFor(() => {
      expect(screen.queryByText("REASONING_MARKER_XYZ")).not.toBeInTheDocument();
    });
  });
});
