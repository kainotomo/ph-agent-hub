// =============================================================================
// PH Agent Hub — ModelRoleList Tests
// =============================================================================
// Tests for the ModelRoleList admin screen component.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { ModelRoleList } from "./ModelRoleList";
import { message } from "antd";

// Hoisted mocks for the admin API module
const {
  mockListModelRoleBindings,
  mockListModels,
  mockSetModelRoleBindings,
  mockClearModelRoleBindings,
} = vi.hoisted(
  () => ({
    mockListModelRoleBindings: vi.fn(),
    mockListModels: vi.fn(),
    mockSetModelRoleBindings: vi.fn(),
    mockClearModelRoleBindings: vi.fn(),
  }),
);

// Mock the admin API module
vi.mock("../../services/admin", () => ({
  listModelRoleBindings: (...args: unknown[]) => mockListModelRoleBindings(...args),
  listModels: (...args: unknown[]) => mockListModels(...args),
  setModelRoleBindings: (...args: unknown[]) => mockSetModelRoleBindings(...args),
  clearModelRoleBindings: (...args: unknown[]) => mockClearModelRoleBindings(...args),
}));

// Mock the AuthProvider so useAuth() returns a test user
vi.mock("../../../../providers/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: "u1",
      tenant_id: "tenant-1",
      role: "admin",
      email: "a@example.com",
      display_name: "Admin",
    },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

function renderScreen() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ModelRoleList />
      </BrowserRouter>
    </QueryClientProvider>,
  );
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 200));
  });
}

vi.setConfig({ testTimeout: 20000 });
describe("ModelRoleList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("renders a card for each role the API returns", async () => {
    mockListModelRoleBindings.mockResolvedValue([
      { role: "@fast", models: [] },
      { role: "@general", models: [{ id: "model-1", name: "Fast Model", model_id: "fast-1", enabled: true }] },
      { role: "@reasoning", models: [{ id: "model-2", name: "Old Model", model_id: "old-1", enabled: false }] },
    ]);
    mockListModels.mockResolvedValue({
      items: [{ id: "model-1", name: "Fast Model", model_id: "fast-1", enabled: true }],
      total: 1,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    expect(await screen.findByText("@fast")).toBeInTheDocument();
    expect(await screen.findByText("@general")).toBeInTheDocument();
    expect(await screen.findByText("@reasoning")).toBeInTheDocument();
  });

  it("renders an Unbound alert when models array is empty", async () => {
    mockListModelRoleBindings.mockResolvedValue([
      { role: "@fast", models: [] },
    ]);
    mockListModels.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
      total_pages: 0,
    });

    renderScreen();
    await settle();

    expect(await screen.findByText(/Unbound — workflows using this role will fail/i)).toBeInTheDocument();
  });

  it("renders a No enabled model alert when all models are disabled", async () => {
    mockListModelRoleBindings.mockResolvedValue([
      { role: "@reasoning", models: [{ id: "model-2", name: "Old Model", model_id: "old-1", enabled: false }] },
    ]);
    mockListModels.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
      total_pages: 0,
    });

    renderScreen();
    await settle();

    expect(await screen.findByText(/No enabled model — workflows using this role will fail/i)).toBeInTheDocument();
  });

  it("calls setModelRoleBindings when clicking Save @fast", async () => {
    mockListModelRoleBindings.mockResolvedValue([
      { role: "@fast", models: [] },
    ]);
    mockListModels.mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 200,
      total_pages: 0,
    });

    renderScreen();
    await settle();

    const saveBtn = await screen.findByRole("button", { name: "Save @fast" });
    await userEvent.click(saveBtn);
    await settle();

    expect(mockSetModelRoleBindings).toHaveBeenCalledWith("@fast", [], { tenant_id: "tenant-1" });
  });

  it("calls clearModelRoleBindings when clicking Clear then OK", async () => {
    mockListModelRoleBindings.mockResolvedValue([
      { role: "@general", models: [{ id: "model-1", name: "Fast Model", model_id: "fast-1", enabled: true }] },
    ]);
    mockListModels.mockResolvedValue({
      items: [{ id: "model-1", name: "Fast Model", model_id: "fast-1", enabled: true }],
      total: 1,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    const clearBtn = await screen.findByRole("button", { name: "Clear @general" });
    await userEvent.click(clearBtn);
    await settle();

    const confirmBtn = await screen.findByRole("button", { name: /ok/i });
    await userEvent.click(confirmBtn);
    await settle();

    expect(mockClearModelRoleBindings).toHaveBeenCalledWith("@general", { tenant_id: "tenant-1" });
  });

  it("shows an error message when setModelRoleBindings rejects", async () => {
    mockListModelRoleBindings.mockResolvedValue([
      { role: "@general", models: [{ id: "model-1", name: "Fast Model", model_id: "fast-1", enabled: true }] },
    ]);
    mockListModels.mockResolvedValue({
      items: [{ id: "model-1", name: "Fast Model", model_id: "fast-1", enabled: true }],
      total: 1,
      page: 1,
      page_size: 200,
      total_pages: 1,
    });
    mockSetModelRoleBindings.mockRejectedValue(new Error("Model 'x' belongs to a different tenant"));

    const errorSpy = vi.spyOn(message, "error");

    renderScreen();
    await settle();

    const saveBtn = await screen.findByRole("button", { name: "Save @general" });
    await userEvent.click(saveBtn);
    await settle();

    expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining("different tenant"));
  });
});
