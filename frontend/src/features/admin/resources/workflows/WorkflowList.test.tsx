// =============================================================================
// PH Agent Hub — WorkflowList Tests
// =============================================================================
// Tests for the WorkflowList admin screen component.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { WorkflowList } from "./WorkflowList";

// Hoisted mocks for the admin API module
const {
  mockListWorkflows,
  mockDeleteWorkflow,
  mockUpdateWorkflow,
  mockListUnboundRoles,
} = vi.hoisted(
  () => ({
    mockListWorkflows: vi.fn(),
    mockDeleteWorkflow: vi.fn(),
    mockUpdateWorkflow: vi.fn(),
    mockListUnboundRoles: vi.fn(),
  }),
);

// Mock the admin API module
vi.mock("../../services/admin", () => ({
  listWorkflows: (...args: unknown[]) => mockListWorkflows(...args),
  deleteWorkflow: (...args: unknown[]) => mockDeleteWorkflow(...args),
  updateWorkflow: (...args: unknown[]) => mockUpdateWorkflow(...args),
  listUnboundRoles: (...args: unknown[]) => mockListUnboundRoles(...args),
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
        <WorkflowList />
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

describe("WorkflowList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("renders workflow names and keys returned by listWorkflows", async () => {
    mockListWorkflows.mockResolvedValue({
      items: [
        {
          id: "wf-1",
          tenant_id: "tenant-1",
          key: "summarize-email",
          name: "Summarize Email",
          description: "Summarizes an email thread",
          definition: { key: "summarize-email", name: "Summarize Email", steps: [], branches: [] },
          visibility: "tenant",
          enabled: true,
          signature_hash: "abc123",
          created_by: "u1",
          updated_by: "u1",
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-02T00:00:00Z",
        },
        {
          id: "wf-2",
          tenant_id: "tenant-1",
          key: "generate-report",
          name: "Generate Report",
          description: "Generates a monthly report",
          definition: { key: "generate-report", name: "Generate Report", steps: [], branches: [] },
          visibility: "public",
          enabled: false,
          signature_hash: "def456",
          created_by: "u2",
          updated_by: "u2",
          created_at: "2024-02-01T00:00:00Z",
          updated_at: "2024-02-05T00:00:00Z",
        },
      ],
      total: 2,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    expect(await screen.findByText("Summarize Email")).toBeInTheDocument();
    expect(await screen.findByText("Generate Report")).toBeInTheDocument();
    expect(await screen.findByText("summarize-email")).toBeInTheDocument();
    expect(await screen.findByText("generate-report")).toBeInTheDocument();
  });

  it("clicking Delete and confirming calls deleteWorkflow with the row id", async () => {
    mockListWorkflows.mockResolvedValue({
      items: [
        {
          id: "wf-delete-test",
          tenant_id: "tenant-1",
          key: "test-wf",
          name: "Test Workflow",
          description: "",
          definition: { key: "test-wf", name: "Test Workflow", steps: [], branches: [] },
          visibility: "tenant",
          enabled: true,
          signature_hash: "xyz",
          created_by: null,
          updated_by: null,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    // Find the delete button
    const deleteBtn = await screen.findByRole("button", { name: /delete/i });
    expect(deleteBtn).toBeInTheDocument();

    await act(async () => {
      deleteBtn.click();
    });
    await settle();

    // Confirm on the Popconfirm dialog
    const confirmBtn = await screen.findByRole("button", { name: /ok/i });
    await act(async () => {
      confirmBtn.click();
    });
    await settle();

    // The mutation calls deleteWorkflow with the id as the first argument
    expect(mockDeleteWorkflow).toHaveBeenCalledWith("wf-delete-test", expect.any(Object));
  });

  it("a failed listWorkflows (rejected promise) does not throw and leaves the screen rendered", async () => {
    mockListWorkflows.mockRejectedValue(new Error("API connection failed"));

    // Capture any unhandled errors so they don't break the test run
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    renderScreen();
    await settle();

    // The screen should still render — the title is always visible
    expect(screen.getByText("Workflows")).toBeInTheDocument();

    // There should be an Empty state since no data loaded
    expect(screen.getByText(/No workflows available/i)).toBeInTheDocument();

    errorSpy.mockRestore();
  });

  it("toggles enabled when clicking the switch", async () => {
    mockListWorkflows.mockResolvedValue({
      items: [
        {
          id: "wf-toggle",
          tenant_id: "tenant-1",
          key: "toggle-test",
          name: "Toggle Workflow",
          description: "",
          definition: { key: "toggle-test", name: "Toggle Workflow", steps: [], branches: [] },
          visibility: "tenant",
          enabled: true,
          signature_hash: "abc",
          created_by: null,
          updated_by: null,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    const switchEl = await screen.findByRole("switch");
    await userEvent.click(switchEl);
    await settle();

    expect(mockUpdateWorkflow).toHaveBeenCalledWith("wf-toggle", { enabled: false });
  });

  it("renders the Edit link for each workflow", async () => {
    mockListWorkflows.mockResolvedValue({
      items: [
        {
          id: "wf-edit-test",
          tenant_id: "tenant-1",
          key: "edit-test",
          name: "Edit Test Workflow",
          description: "",
          definition: { key: "edit-test", name: "Edit Test Workflow", steps: [], branches: [] },
          visibility: "tenant",
          enabled: true,
          signature_hash: "edit",
          created_by: null,
          updated_by: null,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    renderScreen();
    await settle();

    expect(await screen.findByRole("link", { name: "Edit" })).toHaveAttribute(
      "href",
      "/admin/workflows/wf-edit-test/edit",
    );
  });

  it("shows an Unbound roles Tag and a Bind roles link when listUnboundRoles returns roles", async () => {
    mockListWorkflows.mockResolvedValue({
      items: [
        {
          id: "wf-unbound",
          tenant_id: "tenant-1",
          key: "unbound-wf",
          name: "Unbound Workflow",
          description: "",
          definition: {
            key: "unbound-wf",
            name: "Unbound Workflow",
            steps: [
              {
                id: "s1",
                name: "Step 1",
                type: "agent",
                model_ref: "@reasoning",
                tool_refs: [],
              },
            ],
            branches: [],
          },
          visibility: "tenant",
          enabled: true,
          signature_hash: "unbound",
          created_by: null,
          updated_by: null,
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 25,
      total_pages: 1,
    });

    mockListUnboundRoles.mockResolvedValue([
      { role: "@reasoning", models: [], workflow_keys: [] },
    ]);

    renderScreen();
    await settle();

    expect(await screen.findByText(/Unbound Workflow/)).toBeInTheDocument();
    expect(await screen.findByText(/Unbound:/)).toBeInTheDocument();
    expect(await screen.findByText(/@reasoning/)).toBeInTheDocument();
    expect(await screen.findByText(/Bind roles/)).toBeInTheDocument();
  });
});
