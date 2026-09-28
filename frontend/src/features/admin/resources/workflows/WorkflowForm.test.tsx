// =============================================================================
// PH Agent Hub — WorkflowForm Tests
// =============================================================================
// Tests for the WorkflowForm admin form component.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Modal } from "antd";
import { WorkflowForm } from "./WorkflowForm";

// ---------------------------------------------------------------------------
// Hoisted mock functions — referenced by both the vi.mock factory and the
// test bodies so the same fn instance is returned to the component.
// ---------------------------------------------------------------------------
const mockFns = vi.hoisted(() => ({
  previewWorkflowTopology: vi.fn(),
  getWorkflow: vi.fn(),
  createWorkflow: vi.fn(),
  updateWorkflow: vi.fn(),
  getWorkflowSchema: vi.fn(),
  listRegisteredAgents: vi.fn(),
  listUnboundRoles: vi.fn(),
  listModels: vi.fn(),
  listTools: vi.fn(),
}));

const {
  previewWorkflowTopology: mockPreviewWorkflowTopology,
  getWorkflow: mockGetWorkflow,
  createWorkflow: mockCreateWorkflow,
  updateWorkflow: mockUpdateWorkflow,
  getWorkflowSchema: mockGetWorkflowSchema,
  listRegisteredAgents: mockListRegisteredAgents,
  listUnboundRoles: mockListUnboundRoles,
  listModels: mockListModels,
  listTools: mockListTools,
} = mockFns;

vi.mock("../../services/admin", () => ({
  previewWorkflowTopology: (...args: unknown[]) =>
    mockPreviewWorkflowTopology(...args),
  getWorkflow: (...args: unknown[]) => mockGetWorkflow(...args),
  createWorkflow: (...args: unknown[]) => mockCreateWorkflow(...args),
  updateWorkflow: (...args: unknown[]) => mockUpdateWorkflow(...args),
  getWorkflowSchema: (...args: unknown[]) => mockGetWorkflowSchema(...args),
  listRegisteredAgents: (...args: unknown[]) =>
    mockListRegisteredAgents(...args),
  listUnboundRoles: (...args: unknown[]) => mockListUnboundRoles(...args),
  listModels: (...args: unknown[]) => mockListModels(...args),
  listTools: (...args: unknown[]) => mockListTools(...args),
}));

// ---------------------------------------------------------------------------
// Mock the AuthProvider so useAuth() returns a test user
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function renderScreen(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>,
  );
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 200));
  });
}

vi.setConfig({ testTimeout: 20000 });

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("WorkflowForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => cleanup());

  it("renders metadata fields (Key, Name) and an Add step button in create mode", async () => {
    mockGetWorkflowSchema.mockResolvedValue({
      step_types: ["inline", "agent"],
      context_modes: ["full", "last_agent"],
      on_error_values: ["stop", "continue"],
      model_roles: ["@fast"],
      tool_roles: ["@tool"],
      agent_roles: ["@agent"],
      conditions: ["always"],
      default_branch: "always",
      max_steps: 20,
    });
    mockListRegisteredAgents.mockResolvedValue([]);
    mockListModels.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200, total_pages: 1 });
    mockListTools.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200, total_pages: 1 });

    renderScreen(<WorkflowForm />);
    await settle();

    // Verify the metadata card section is present with Key and Name fields
    expect(screen.getByText("Key")).toBeInTheDocument();
    expect(screen.getByText("Name")).toBeInTheDocument();

    // Verify the input placeholders are present
    expect(screen.getByPlaceholderText("my-workflow")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Human Agent Hub")).toBeInTheDocument();

    // There are two "Add step" buttons. Ant Design wraps the text in an
    // inner <span>, so getAllByText finds the span, not the <button>.  We
    // query all <button> elements and filter by text content.
    const allButtons = Array.from(document.querySelectorAll("button"));
    const addStepButtons = allButtons.filter((b) =>
      b.textContent?.trim().includes("Add step"),
    );
    expect(addStepButtons.length).toBeGreaterThanOrEqual(1);
  });

  it("clicking Add step adds one step row", async () => {
    mockGetWorkflowSchema.mockResolvedValue({
      step_types: ["inline", "agent"],
      context_modes: ["full", "last_agent"],
      on_error_values: ["stop", "continue"],
      model_roles: ["@fast"],
      tool_roles: ["@tool"],
      agent_roles: ["@agent"],
      conditions: ["always"],
      default_branch: "always",
      max_steps: 20,
    });
    mockListRegisteredAgents.mockResolvedValue([]);
    mockListModels.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200, total_pages: 1 });
    mockListTools.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200, total_pages: 1 });

    renderScreen(<WorkflowForm />);
    await settle();

    // Click the first "Add step" button
    const allButtons = Array.from(document.querySelectorAll("button"));
    const addStepBtn = allButtons.find((b) =>
      b.textContent?.trim().includes("Add step"),
    );
    if (!addStepBtn) throw new Error("No Add step button found");
    await act(async () => {
      await userEvent.click(addStepBtn);
    });
    await settle();

    // The step list renders an input with the step id placeholder
    expect(screen.getByPlaceholderText("step_1")).toBeInTheDocument();
  });

  it(
    "in edit mode, topology warning confirmation calls updateWorkflow with confirm_topology_edit: true",
    async () => {
      const mockWorkflow = {
        id: "wf-1",
        tenant_id: "tenant-1",
        key: "my-wf",
        name: "My Workflow",
        description: "Test workflow",
        visibility: "tenant",
        enabled: true,
        signature_hash: "abc123",
        created_by: "u1",
        updated_by: "u1",
        created_at: "2025-01-01T00:00:00Z",
        updated_at: "2025-01-02T00:00:00Z",
        definition: {
          key: "my-wf",
          name: "My Workflow",
          steps: [
            {
              id: "a",
              name: "Step A",
              type: "inline",
              instructions: "Do thing",
              tool_refs: [],
              context_mode: "full",
              on_error: "stop",
            },
          ],
          branches: [],
        },
      };

      mockGetWorkflow.mockResolvedValue(mockWorkflow);
      mockListUnboundRoles.mockResolvedValue([]);
      mockGetWorkflowSchema.mockResolvedValue({
        step_types: ["inline", "agent"],
        context_modes: ["full", "last_agent"],
        on_error_values: ["stop", "continue"],
        model_roles: ["@fast"],
        tool_roles: ["@tool"],
        agent_roles: ["@agent"],
        conditions: ["always"],
        default_branch: "always",
        max_steps: 20,
      });
      mockListRegisteredAgents.mockResolvedValue([]);
      mockListModels.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200, total_pages: 1 });
      mockListTools.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200, total_pages: 1 });
      mockPreviewWorkflowTopology.mockResolvedValue({
        requires_confirmation: true,
        report: "This is a topology edit: added step b",
        added_step_ids: ["b"],
        removed_step_ids: [],
        routing_changed: false,
        paused_run_count: 2,
      });

      const confirmSpy = vi.spyOn(Modal, "confirm");

      renderScreen(<WorkflowForm id="wf-1" />);
      await settle();

      // In edit mode the workflow is pre-populated by the form; submit triggers
      // the topology preview which opens the confirm modal.
      const saveBtn = screen.getByRole("button", { name: "Save" });
      await act(async () => {
        await userEvent.click(saveBtn);
      });

      // Wait for the topology check to settle and Modal.confirm to be called
      await settle();

      expect(confirmSpy).toHaveBeenCalled();
      const modalConfig = confirmSpy.mock.calls[0][0];

      // Check that the report text appears in the modal content
      const modalEl = screen.getByRole("dialog");
      expect(modalEl).toBeInTheDocument();
      expect(modalEl.textContent).toContain(
        "This is a topology edit: added step b",
      );

      // Simulate user clicking OK in the confirmation modal
      if (modalConfig.onOk) {
        await act(async () => {
          await modalConfig.onOk!();
        });
        await settle();
      }

      expect(mockUpdateWorkflow).toHaveBeenCalledWith(
        "wf-1",
        expect.objectContaining({
          confirm_topology_edit: true,
        }),
      );
    },
  );
});
