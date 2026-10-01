// =============================================================================
// PromptLibrary — Unit Tests (Issue #572)
// =============================================================================
// Verifies that the platform-owned placeholders {{SESSION_ID}} / {{SESSION_URL}}
// are never surfaced as user fill-in fields and are passed through untouched, so
// the backend can substitute them.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockApi = vi.fn();

vi.mock("../../../services/api", () => ({
  default: (...args: unknown[]) => mockApi(...args),
}));

import { PromptLibrary } from "./PromptLibrary";

const SESSION_PROMPT = {
  id: "p1",
  tenant_id: "t1",
  user_id: "u1",
  template_id: null,
  title: "Cite the session",
  description: "",
  content: "Ticker {{TICKER}} — evidence {{SESSION_URL}}",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function renderLibrary(onUse = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PromptLibrary onUse={onUse} />
    </QueryClientProvider>,
  );
  return onUse;
}

async function openPromptUseModal() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Prompts" }));
  await waitFor(() =>
    expect(screen.getByText("Cite the session")).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "Use" }));
  return user;
}

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockResolvedValue([SESSION_PROMPT]);
});

describe("PromptLibrary reserved placeholders", () => {
  it("does not offer a fill-in field for a reserved placeholder", async () => {
    renderLibrary();
    await openPromptUseModal();

    expect(
      screen.getByPlaceholderText("Value for TICKER (optional)"),
    ).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("Value for SESSION_URL (optional)"),
    ).not.toBeInTheDocument();
  });

  it("passes the reserved placeholder through untouched", async () => {
    const onUse = renderLibrary();
    const user = await openPromptUseModal();

    await user.type(
      screen.getByPlaceholderText("Value for TICKER (optional)"),
      "AAPL",
    );
    await user.click(screen.getByRole("button", { name: "Insert" }));

    expect(onUse).toHaveBeenCalledTimes(1);
    const resolved = onUse.mock.calls[0][0] as string;
    expect(resolved).toContain("AAPL");
    expect(resolved).toContain("{{SESSION_URL}}");
  });
});
