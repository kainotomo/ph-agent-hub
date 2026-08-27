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
});
