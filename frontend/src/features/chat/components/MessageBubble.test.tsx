import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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

  it("shows no process fold for assistant messages without process steps", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-no-process",
            session_id: "session-1",
            sender: "assistant",
            content: [{ type: "text", text: "Hello, how can I help?" }],
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
          streaming={false}
        />
      </QueryClientProvider>,
    );

    // No process fold header for messages with no process steps
    expect(screen.queryByRole("button", { name: /step/i })).not.toBeInTheDocument();
    expect(screen.getByText("Hello, how can I help?")).toBeInTheDocument();
  });

  it("shows process fold header for assistant messages with reasoning", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-reason",
            session_id: "session-1",
            sender: "assistant",
            content: [
              { type: "reasoning", text: "Let me think about this..." },
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
          streaming={false}
        />
      </QueryClientProvider>,
    );

    // Process fold header should be visible (shows reasoning summary for single step)
    expect(screen.getByRole("button", { name: /Let me think/i })).toBeInTheDocument();
    // Fold is collapsed (aria-expanded=false)
    expect(screen.getByRole("button", { expanded: false })).toHaveAttribute("aria-expanded", "false");
    // Answer text should be visible
    expect(screen.getByText("Hello, how can I help?")).toBeInTheDocument();
  });

  it("process fold is open while streaming", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-streaming",
            session_id: "session-1",
            sender: "assistant",
            content: [
              { type: "reasoning", text: "Thinking..." },
              { type: "text", text: "Hello" },
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
          streaming={true}
        />
      </QueryClientProvider>,
    );

    // Fold should be open while streaming
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    // Content visible in body (appears twice: once in header summary, once in body)
    expect(screen.getAllByText("Thinking...").length).toBe(2);
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("expands process fold on header click when collapsed", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-expand",
            session_id: "session-1",
            sender: "assistant",
            content: [
              { type: "reasoning", text: "Let me think..." },
              { type: "text", text: "Answer text" },
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
          streaming={false}
        />
      </QueryClientProvider>,
    );

    // Should be collapsed initially (only in header, not in body)
    expect(screen.getByRole("button", { expanded: false })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getAllByText("Let me think...").length).toBe(1);

    // Click the fold header to expand
    const headerBtn = screen.getByRole("button", { name: /Let me think/i });
    await user.click(headerBtn);

    // Content should now be visible in body (appears twice: header + body)
    expect(screen.getAllByText("Let me think...").length).toBe(2);
    expect(screen.getByText("Answer text")).toBeInTheDocument();
  });

  it("shows tool call steps in process fold", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-tools",
            session_id: "session-1",
            sender: "assistant",
            content: [
              { type: "reasoning", text: "I'll search for that." },
              { type: "function_call", name: "web_search", arguments: { query: "test" }, call_id: "call-1", batch_id: null },
              { type: "function_result", name: "web_search", output: "Results found", call_id: "call-1", batch_id: null },
              { type: "text", text: "Here are the results." },
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
          streaming={false}
        />
      </QueryClientProvider>,
    );

    // Process fold header exists (button with aria-expanded)
    const foldBtn = screen.getByRole("button", { expanded: false });
    expect(foldBtn).toBeInTheDocument();
    // Content is collapsed by default (non-streaming)
    expect(foldBtn).toHaveAttribute("aria-expanded", "false");
    // Answer text should be visible
    expect(screen.getByText("Here are the results.")).toBeInTheDocument();
  });

  it("shows tool call steps expanded when streaming", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MessageBubble
          message={{
            id: "msg-tools-streaming",
            session_id: "session-1",
            sender: "assistant",
            content: [
              { type: "reasoning", text: "I'll search for that." },
              { type: "function_call", name: "web_search", arguments: { query: "test" }, call_id: "call-1", batch_id: null },
              { type: "function_result", name: "web_search", output: "Results found", call_id: "call-1", batch_id: null },
              { type: "text", text: "Here are the results." },
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
          streaming={true}
        />
      </QueryClientProvider>,
    );

    // Fold is open while streaming
    expect(screen.getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(screen.getByText("I'll search for that.")).toBeInTheDocument();
    // web_search appears in both tool_call and tool_result tags
    expect(screen.getAllByText("web_search").length).toBe(2);
    // Click the tool_result step header to expand it (header text is "down tool check web_search")
    const resultHeader = screen.getByRole("button", { name: /down tool check web_search/i });
    await user.click(resultHeader);
    // Tool result output is now visible
    expect(screen.getByText("Results found")).toBeInTheDocument();
    expect(screen.getByText("Here are the results.")).toBeInTheDocument();
  });
});
