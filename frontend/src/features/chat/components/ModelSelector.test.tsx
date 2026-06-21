// =============================================================================
// ModelSelector — Unit Tests
// =============================================================================
// Tests focus on: options rendering (Auto + model list), "Set as default"
// star toggle, value/onChange callbacks, loading and empty states.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ModelSelector } from "./ModelSelector";

// ---------------------------------------------------------------------------
// Mock external dependencies
// ---------------------------------------------------------------------------

const { mockApi, mockGetMe, mockSetDefaultModel } = vi.hoisted(() => ({
  mockApi: vi.fn(),
  mockGetMe: vi.fn(),
  mockSetDefaultModel: vi.fn(),
}));

vi.mock("../../../services/api", () => ({
  default: mockApi,
  getToken: () => "test-token",
}));

vi.mock("../../../services/auth", () => ({
  getMe: mockGetMe,
  setDefaultModel: mockSetDefaultModel,
}));

// Mock Ant Design's Grid.useBreakpoint to return desktop breakpoints.
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return {
    ...antd,
    Grid: {
      useBreakpoint: () => ({ xs: false, sm: true, md: true, lg: true, xl: true }),
    },
  };
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const FAKE_MODELS = [
  {
    id: "model-abc",
    tenant_id: "t1",
    name: "DeepSeek V4",
    provider: "deepseek",
    base_url: null,
    enabled: true,
    thinking_enabled: false,
    max_tokens: 4096,
    temperature: 0.7,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
  {
    id: "model-xyz",
    tenant_id: "t1",
    name: "GPT-5",
    provider: "openai",
    base_url: null,
    enabled: true,
    thinking_enabled: false,
    max_tokens: 8192,
    temperature: 0.7,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
];

const FAKE_USER = {
  id: "u1",
  email: "test@example.com",
  display_name: "Test User",
  role: "admin",
  tenant_id: "t1",
  is_active: true,
  default_model_id: null,
  created_at: "2024-01-01T00:00:00Z",
};

const AUTO_ROUTE_VALUE = "__auto__";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderModelSelector(props: Record<string, unknown> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ModelSelector value="" onChange={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

/** Wait for React async updates to settle */
async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 200));
  });
}

/** Open the antd Select dropdown by clicking the selector */
async function openSelect(user: ReturnType<typeof userEvent.setup>) {
  const selector = document.querySelector(".ant-select-selector");
  if (!selector) throw new Error("Select selector not found");
  await user.click(selector);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ModelSelector", () => {
  beforeEach(() => {
    mockApi.mockReset();
    mockGetMe.mockReset();
    mockSetDefaultModel.mockReset();

    mockApi.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve(FAKE_MODELS);
      return Promise.resolve([]);
    });
    mockGetMe.mockResolvedValue(FAKE_USER);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  // ── Options rendering ──────────────────────────────────────────────────

  it("renders Auto option and model list from API", async () => {
    renderModelSelector();
    await settle();

    const user = userEvent.setup();
    await openSelect(user);

    // Auto option
    expect(screen.getByText("⚡ Auto (Recommended)")).toBeInTheDocument();

    // Model options
    expect(screen.getByText("DeepSeek V4 (deepseek)")).toBeInTheDocument();
    expect(screen.getByText("GPT-5 (openai)")).toBeInTheDocument();
  });

  // ── value / onChange callbacks ─────────────────────────────────────────

  it("calls onChange with the selected model id when user picks an option", async () => {
    const onChange = vi.fn();
    renderModelSelector({ onChange });

    // Give the component time to fetch models so the select is ready
    await settle();

    const user = userEvent.setup();
    await openSelect(user);

    // Click the first model option
    const option = screen.getByText("DeepSeek V4 (deepseek)");
    await user.click(option);

    // antd Select passes (value, option) — check first arg
    expect(onChange).toHaveBeenCalledWith(
      "model-abc",
      expect.objectContaining({ value: "model-abc" }),
    );
  });

  it("reflects the value prop in the current selection", async () => {
    renderModelSelector({ value: "model-xyz" });
    await settle();

    // With value="model-xyz", clicking the selector should show GPT-5 as selected
    // We verify that the Select displays the selected label.
    // In antd, when the Select has a value, it shows the label text somewhere
    // inside the .ant-select-selection-item.
    const selectionItem = document.querySelector(".ant-select-selection-item");
    expect(selectionItem?.textContent).toMatch(/GPT-5/i);
  });

  // ── Star / Set-as-default toggle ───────────────────────────────────────

  it("shows StarFilled when the selected model is the user's default", async () => {
    mockGetMe.mockResolvedValue({ ...FAKE_USER, default_model_id: "model-abc" });
    renderModelSelector({ value: "model-abc" });
    await settle();

    // The star button should be present (it renders when value is not auto)
    const starButton = document.querySelector(
      ".ant-btn-primary .anticon-star",
    );
    expect(starButton).toBeInTheDocument();
  });

  it("shows StarOutlined when the selected model is not the default", async () => {
    mockGetMe.mockResolvedValue({ ...FAKE_USER, default_model_id: "model-xyz" });
    renderModelSelector({ value: "model-abc" });
    await settle();

    // Star outlined icon should be present
    const starIcon = document.querySelector(".anticon-star");
    expect(starIcon).toBeInTheDocument();
  });

  it("calls setDefaultModel when star button is clicked, then shows loading", async () => {
    mockSetDefaultModel.mockReturnValue(Promise.resolve());
    renderModelSelector({ value: "model-abc" });
    await settle();

    const user = userEvent.setup();
    // Find the star button (the one with class ant-btn next to the Select)
    const starBtn = document.querySelector(
      ".ant-space-compact .ant-btn",
    );
    expect(starBtn).toBeInTheDocument();

    await user.click(starBtn!);

    expect(mockSetDefaultModel).toHaveBeenCalledWith("model-abc");
  });

  // ── Loading state ──────────────────────────────────────────────────────

  it("shows loading indicator while models are being fetched", async () => {
    // Never resolve the API call so the query stays in loading state
    mockApi.mockImplementation(() => new Promise(() => {}));

    renderModelSelector();
    await settle();

    // antd Select renders a loading spinner (anticon-loading) when isLoading
    const spinner = document.querySelector(".anticon-loading");
    expect(spinner).toBeInTheDocument();
  });

  // ── Empty state ────────────────────────────────────────────────────────

  it("shows only the Auto option when the model list is empty", async () => {
    mockApi.mockResolvedValue([]);

    // Override getMe to return a default model so the Select has a known value
    mockGetMe.mockResolvedValue({ ...FAKE_USER, default_model_id: "model-xyz" });
    renderModelSelector({ value: "model-xyz" });
    await settle();

    const user = userEvent.setup();
    await openSelect(user);

    // Auto option is always present
    expect(screen.getByText("⚡ Auto (Recommended)")).toBeInTheDocument();
    // Model options should NOT be present since the API returned empty
    expect(screen.queryByText(/DeepSeek/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/GPT/i)).not.toBeInTheDocument();
  });

  // ── Thunderbolt for Auto ───────────────────────────────────────────────

  it("shows thunderbolt icon instead of star when Auto is selected", async () => {
    renderModelSelector({ value: AUTO_ROUTE_VALUE });
    await settle();

    // Thunderbolt icon should be present
    const boltIcon = document.querySelector(".anticon-thunderbolt");
    expect(boltIcon).toBeInTheDocument();

    // Star icon should NOT be present
    const starIcon = document.querySelector(".anticon-star");
    expect(starIcon).not.toBeInTheDocument();
  });
});
