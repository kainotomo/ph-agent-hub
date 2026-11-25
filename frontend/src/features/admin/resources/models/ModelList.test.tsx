// =============================================================================
// PH Agent Hub — ModelList Tests
// =============================================================================
// Tests that the admin models table exposes the Context Window column and no
// longer shows the removed API Key / Max Tokens / Input Price / Output Price
// columns.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ModelList } from "./ModelList";

const { mockListModels, mockListTenants } = vi.hoisted(() => ({
  mockListModels: vi.fn(),
  mockListTenants: vi.fn(),
}));

vi.mock("../../services/admin", () => ({
  listModels: (...args: unknown[]) => mockListModels(...args),
  listTenants: (...args: unknown[]) => mockListTenants(...args),
  deleteModel: vi.fn(),
  updateModel: vi.fn(),
}));

vi.mock("./ModelForm", () => ({
  ModelForm: () => null,
}));

vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return {
    ...antd,
    Grid: {
      useBreakpoint: () => ({ xs: false, sm: true, md: true, lg: true, xl: true }),
    },
  };
});

const MOCK_MODELS = [
  {
    id: "model-1",
    tenant_id: "tenant-1",
    name: "DeepSeek V4",
    model_id: "deepseek-v4",
    provider: "deepseek",
    base_url: null,
    enabled: true,
    is_public: false,
    max_tokens: 4096,
    temperature: 0.7,
    thinking_enabled: false,
    reasoning_effort: null,
    follow_up_questions_enabled: true,
    auto_route_eligible: true,
    context_length: 128000,
    input_price_per_1m: 0.14,
    output_price_per_1m: 0.28,
    cache_hit_price_per_1m: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "model-2",
    tenant_id: "tenant-1",
    name: "Local Model",
    model_id: "local-model",
    provider: "ollama",
    base_url: null,
    enabled: true,
    is_public: false,
    max_tokens: 2048,
    temperature: 0.5,
    thinking_enabled: false,
    reasoning_effort: null,
    follow_up_questions_enabled: false,
    auto_route_eligible: false,
    context_length: null,
    input_price_per_1m: null,
    output_price_per_1m: null,
    cache_hit_price_per_1m: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

function renderModelList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/admin/models"]}>
        <ModelList />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ModelList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListTenants.mockResolvedValue({
      items: [{ id: "tenant-1", name: "Acme" }],
      total: 1,
      page: 1,
      page_size: 100,
      total_pages: 1,
    });
  });

  afterEach(() => cleanup());

  it("shows Context Window and hides the removed columns", async () => {
    mockListModels.mockResolvedValue({
      items: MOCK_MODELS,
      total: 2,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    renderModelList();

    await waitFor(() => {
      expect(screen.getByText("DeepSeek V4")).toBeInTheDocument();
    });

    expect(screen.getByText("Context Window")).toBeInTheDocument();
    expect(screen.getByText((128000).toLocaleString())).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();

    for (const removed of ["API Key", "Max Tokens", "Input Price", "Output Price"]) {
      expect(screen.queryByText(removed)).not.toBeInTheDocument();
    }
  });
});
