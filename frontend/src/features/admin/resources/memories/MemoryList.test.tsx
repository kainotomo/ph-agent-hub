// =============================================================================
// PH Agent Hub — MemoryList Tests
// =============================================================================
// Tests for the MemoryList admin screen component.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { MemoryList } from "./MemoryList";

// Hoisted mocks for the admin API module
const {
  mockListAdminMemories,
  mockDeleteAdminMemory,
  mockUpdateAdminMemory,
  mockListTenants,
} = vi.hoisted(
  () => ({
    mockListAdminMemories: vi.fn(),
    mockDeleteAdminMemory: vi.fn(),
    mockUpdateAdminMemory: vi.fn(),
    mockListTenants: vi.fn(),
  }),
);

// Mock the admin API module
vi.mock("../../services/admin", () => ({
  listAdminMemories: (...args: unknown[]) => mockListAdminMemories(...args),
  deleteAdminMemory: (...args: unknown[]) => mockDeleteAdminMemory(...args),
  updateAdminMemory: (...args: unknown[]) => mockUpdateAdminMemory(...args),
  listTenants: (...args: unknown[]) => mockListTenants(...args),
  listUsers: vi.fn(),
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

// Top-level mock: message singleton only (real Ant Design Modal)
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
      loading: vi.fn(),
      useMessage: () => [
        {
          success: vi.fn(),
          error: vi.fn(),
          warning: vi.fn(),
          info: vi.fn(),
          loading: vi.fn(),
        },
        { destroy: vi.fn() },
      ],
    },
  };
});

function renderScreen() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <MemoryList />
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
describe("MemoryList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("renders memory entries from the API", async () => {
    mockListAdminMemories.mockResolvedValue({
      items: [
        {
          id: "mem-1",
          tenant_id: "tenant-1",
          user_id: "user-1",
          session_id: "sess-1",
          key: "myKey",
          value: "myValue",
          source: "manual",
          created_at: "2024-01-15T00:00:00Z",
          updated_at: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListTenants.mockResolvedValue({
      items: [{ id: "tenant-1", name: "Test Tenant", is_demo: false, balance_euros: null, warning_threshold_eur: null, balance_warning: false, created_at: "", updated_at: "", total_tokens_in: 0, total_tokens_out: 0, total_cost: 0 }],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    expect(await screen.findByText("myKey")).toBeInTheDocument();
    expect(screen.getByText("myValue")).toBeInTheDocument();
  });

  it("calls updateAdminMemory with PUT when submitting the edit modal", async () => {
    mockListAdminMemories.mockResolvedValue({
      items: [
        {
          id: "mem-1",
          tenant_id: "tenant-1",
          user_id: "user-1",
          session_id: "sess-1",
          key: "myKey",
          value: "old value",
          source: "manual",
          created_at: "2024-01-15T00:00:00Z",
          updated_at: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListTenants.mockResolvedValue({
      items: [{ id: "tenant-1", name: "Test Tenant", is_demo: false, balance_euros: null, warning_threshold_eur: null, balance_warning: false, created_at: "", updated_at: "", total_tokens_in: 0, total_tokens_out: 0, total_cost: 0 }],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockUpdateAdminMemory.mockResolvedValue({
      id: "mem-1",
      tenant_id: "tenant-1",
      user_id: "user-1",
      session_id: "sess-1",
      key: "myKey",
      value: "new value",
      source: "manual",
      created_at: "2024-01-15T00:00:00Z",
      updated_at: "2024-01-15T01:00:00Z",
    });

    renderScreen();
    await settle();

    // Click the Edit button (icon button with EditOutlined icon)
    const editBtn = await screen.findByRole("button", { name: /edit/i });
    await userEvent.click(editBtn);
    await settle();

    // Fill in the form and submit
    const keyInput = screen.getByLabelText("Key");
    const valueInput = screen.getByLabelText("Value");

    await userEvent.clear(keyInput);
    await userEvent.clear(valueInput);
    await userEvent.type(keyInput, "myKey");
    await userEvent.type(valueInput, "new value");

    const submitBtn = screen.getByRole("button", { name: /ok/i });
    await userEvent.click(submitBtn);
    await settle();

    expect(mockUpdateAdminMemory).toHaveBeenCalledWith("mem-1", {
      key: "myKey",
      value: "new value",
    });
  });

  it("shows the untruncated value in the detail modal", async () => {
    const longValue = "This is a very long value that would normally be ellipsized in the table cell. " +
      "It spans multiple lines and contains a lot of text that should be fully visible in the detail modal. " +
      "Additional line for good measure.";

    // Inline Modal that renders title and children directly in the DOM
    // (jsdom doesn't support Ant Design Modal portal/animation well)
    const InlineModal = ({
      children,
      open,
      title,
    }: {
      children: React.ReactNode;
      open: boolean;
      title?: React.ReactNode;
    }) => (open ? <div data-testid="detail-modal">{title}{children}</div> : null);

    // Override top-level antd mock: replace Modal with InlineModal
    vi.doMock("antd", async (importOriginal) => {
      const actual = await importOriginal<typeof import("antd")>();
      return {
        ...actual,
        Modal: InlineModal,
        message: {
          success: vi.fn(),
          error: vi.fn(),
          warning: vi.fn(),
          info: vi.fn(),
          loading: vi.fn(),
          useMessage: () => [
            {
              success: vi.fn(),
              error: vi.fn(),
              warning: vi.fn(),
              info: vi.fn(),
              loading: vi.fn(),
            },
            { destroy: vi.fn() },
          ],
        },
      };
    });

    // Re-import MemoryList so it picks up the new Modal mock
    vi.resetModules();
    const { MemoryList: MemoryListInline } = await import("./MemoryList");

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    mockListAdminMemories.mockResolvedValue({
      items: [
        {
          id: "mem-2",
          tenant_id: "tenant-1",
          user_id: "user-1",
          session_id: "sess-1",
          key: "detailKey",
          value: longValue,
          source: "automatic",
          created_at: "2024-01-15T00:00:00Z",
          updated_at: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockListTenants.mockResolvedValue({
      items: [{ id: "tenant-1", name: "Test Tenant", is_demo: false, balance_euros: null, warning_threshold_eur: null, balance_warning: false, created_at: "", updated_at: "", total_tokens_in: 0, total_tokens_out: 0, total_cost: 0 }],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });
    mockUpdateAdminMemory.mockResolvedValue({
      id: "mem-2",
      tenant_id: "tenant-1",
      user_id: "user-1",
      session_id: "sess-1",
      key: "detailKey",
      value: longValue,
      source: "automatic",
      created_at: "2024-01-15T00:00:00Z",
      updated_at: "2024-01-15T01:00:00Z",
    });

    render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <MemoryListInline />
        </BrowserRouter>
      </QueryClientProvider>,
    );
    await settle();

    // Confirm the list item rendered
    expect(await screen.findByText("detailKey")).toBeInTheDocument();

    // Open the detail view through its accessible action button
    await userEvent.click(
      screen.getByRole("button", { name: /view value for detailKey/i }),
    );
    await settle();

    // Wait for the inline Modal to render the content
    await waitFor(
      () => {
        expect(screen.getByText("Memory Value")).toBeInTheDocument();
        expect(screen.getByText("Key: detailKey")).toBeInTheDocument();
      },
      { timeout: 5000 },
    );
    // The modal renders the complete value (the cell is ellipsized)
    expect(screen.getAllByText(longValue).length).toBeGreaterThanOrEqual(1);
  });
});
