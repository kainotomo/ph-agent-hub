// =============================================================================
// SessionToolActivation — Unit Tests
// =============================================================================
// Tests focus on: tool list rendering grouped by category, activation toggle
// interactions, always-on tools display, isPending mode, and empty state.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionToolActivation } from "./SessionToolActivation";

// ---------------------------------------------------------------------------
// Mock external dependencies
// ---------------------------------------------------------------------------

const { mockApi, mockListSessionTools, mockAddSessionTool, mockRemoveSessionTool, mockSetToolAlwaysOn, mockListAlwaysOnTools } = vi.hoisted(() => ({
  mockApi: vi.fn(),
  mockListSessionTools: vi.fn(),
  mockAddSessionTool: vi.fn(),
  mockRemoveSessionTool: vi.fn(),
  mockSetToolAlwaysOn: vi.fn(),
  mockListAlwaysOnTools: vi.fn(),
}));

vi.mock("../../../services/api", () => ({
  default: mockApi,
  getToken: () => "test-token",
}));

vi.mock("../services/chat", () => ({
  listSessionTools: mockListSessionTools,
  addSessionTool: mockAddSessionTool,
  removeSessionTool: mockRemoveSessionTool,
  setToolAlwaysOn: mockSetToolAlwaysOn,
  listAlwaysOnTools: mockListAlwaysOnTools,
}));

// Mock Ant Design's Grid.useBreakpoint for desktop
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

const AVAILABLE_TOOLS = [
  {
    id: "tool-web",
    name: "Web Search",
    description: "Search the web",
    category: "web",
    enabled: true,
    tenant_id: "t1",
    config: {},
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
  {
    id: "tool-fin",
    name: "Stock Price",
    description: "Get stock prices",
    category: "financial",
    enabled: true,
    tenant_id: "t1",
    config: {},
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
  {
    id: "tool-util",
    name: "Calculator",
    description: "Perform calculations",
    category: "utility",
    enabled: true,
    tenant_id: "t1",
    config: {},
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  },
];

const ACTIVE_TOOLS = [
  { id: "tool-web", name: "Web Search", description: "Search the web", category: "web" },
];

const ALWAYS_ON_IDS = ["tool-util"];

const SKILLS_RESPONSE = {
  items: [
    { id: "skill-1", tool_ids: ["tool-web"] },
  ],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderToolActivation(props: Record<string, unknown> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionToolActivation
        sessionId="test-session"
        open={true}
        onClose={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 200));
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SessionToolActivation", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mockApi.mockImplementation((url: string) => {
      if (url === "/chat/session/tools/available") return Promise.resolve(AVAILABLE_TOOLS);
      if (url === "/skills") return Promise.resolve(SKILLS_RESPONSE);
      return Promise.resolve([]);
    });
    mockListSessionTools.mockResolvedValue(ACTIVE_TOOLS);
    mockListAlwaysOnTools.mockResolvedValue(ALWAYS_ON_IDS);
    mockAddSessionTool.mockResolvedValue(undefined);
    mockRemoveSessionTool.mockResolvedValue(undefined);
    mockSetToolAlwaysOn.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
  });

  // ── Tool list rendering grouped by category ────────────────────────────

  it("renders tools grouped by category with headers", async () => {
    renderToolActivation();
    await settle();

    // Category headers (in order: financial, web, utility)
    const financialHeader = screen.getByText("Financial");
    const webHeader = screen.getByText("Web");
    const utilityHeader = screen.getByText("Utility");

    expect(financialHeader).toBeInTheDocument();
    expect(webHeader).toBeInTheDocument();
    expect(utilityHeader).toBeInTheDocument();

    // Tool names
    expect(screen.getByText("Stock Price")).toBeInTheDocument();
    expect(screen.getByText("Web Search")).toBeInTheDocument();
    expect(screen.getByText("Calculator")).toBeInTheDocument();
  });

  // ── Activation toggle interactions ─────────────────────────────────────

  /** Find the always-on and active switch elements for a named tool row. */
  function toolSwitches(toolName: string) {
    const item = screen.getByText(toolName).closest(".ant-list-item");
    if (!item) throw new Error(`Tool "${toolName}" not found`);
    const switches = item.querySelectorAll<HTMLElement>(".ant-switch");
    return { alwaysOn: switches[0], active: switches[1] };
  }

  it("calls addSessionTool when activating a tool", async () => {
    renderToolActivation();
    await settle();

    const user = userEvent.setup();

    // "Stock Price" is not in ACTIVE_TOOLS → its active switch is unchecked
    const { active } = toolSwitches("Stock Price");
    expect(active).not.toHaveClass("ant-switch-checked");

    await user.click(active);

    expect(mockAddSessionTool).toHaveBeenCalledWith("test-session", "tool-fin");
  });

  it("calls removeSessionTool when deactivating an active tool", async () => {
    renderToolActivation();
    await settle();

    const user = userEvent.setup();

    // "Web Search" IS in ACTIVE_TOOLS → its active switch is checked
    const { active } = toolSwitches("Web Search");
    expect(active).toHaveClass("ant-switch-checked");

    await user.click(active);

    expect(mockRemoveSessionTool).toHaveBeenCalledWith("test-session", "tool-web");
  });

  // ── Always-on tools display ────────────────────────────────────────────

  it("shows always-on switch checked for always-on tools", async () => {
    renderToolActivation();
    await settle();

    // Calculator (tool-util) is in ALWAYS_ON_IDS
    const { alwaysOn } = toolSwitches("Calculator");
    expect(alwaysOn).toHaveClass("ant-switch-checked");
  });

  it("calls setToolAlwaysOn when toggling always-on", async () => {
    renderToolActivation();
    await settle();

    const user = userEvent.setup();

    // Calculator is always-on → toggling it off calls setToolAlwaysOn(..., false)
    const { alwaysOn } = toolSwitches("Calculator");
    expect(alwaysOn).toHaveClass("ant-switch-checked");

    await user.click(alwaysOn);

    expect(mockSetToolAlwaysOn).toHaveBeenCalledWith("tool-util", false);
  });

  // ── Auto-select tools toggle ───────────────────────────────────────────

  it("fires onAutoSelectToolsChange when toggling the auto-select switch", async () => {
    const onAutoSelectToolsChange = vi.fn();
    renderToolActivation({ onAutoSelectToolsChange });
    await settle();

    const user = userEvent.setup();

    // The auto-select switch is inside the drawer body, before any tool list
    const allSwitches = document.querySelectorAll<HTMLElement>(".ant-drawer-body .ant-switch");
    expect(allSwitches.length).toBeGreaterThanOrEqual(1);

    // First switch in the drawer body is the auto-select toggle
    await user.click(allSwitches[0]);

    expect(onAutoSelectToolsChange).toHaveBeenCalledWith(false);
  });

  // ── isPending mode ─────────────────────────────────────────────────────

  it("uses pendingActiveToolIds and calls onPendingToolToggle in isPending mode", async () => {
    const onPendingToolToggle = vi.fn();
    renderToolActivation({
      isPending: true,
      pendingActiveToolIds: ["tool-fin"],
      onPendingToolToggle,
    });
    await settle();

    const user = userEvent.setup();

    // In pending mode, listSessionTools should NOT be called
    expect(mockListSessionTools).not.toHaveBeenCalled();

    // "Stock Price" (tool-fin) is in pendingActiveToolIds → active switch checked
    // "Web Search" (tool-web) is NOT → active switch unchecked → clicking calls toggle
    const { active } = toolSwitches("Web Search");
    expect(active).not.toHaveClass("ant-switch-checked");

    await user.click(active);

    expect(onPendingToolToggle).toHaveBeenCalledWith("tool-web", true);
  });

  // ── Skill tool tagging ─────────────────────────────────────────────────

  it('shows "from skill" tag for tools associated with the selected skill', async () => {
    renderToolActivation({ selectedSkillId: "skill-1" });
    await settle();

    // skill-1 has tool_ids: ["tool-web"], so Web Search should have the tag
    expect(screen.getByText("from skill")).toBeInTheDocument();
  });

  // ── Empty state ────────────────────────────────────────────────────────

  it('shows "No tools available" when the available tools list is empty', async () => {
    mockApi.mockResolvedValue([]);

    renderToolActivation();
    await settle();

    expect(screen.getByText("No tools available for your tenant")).toBeInTheDocument();
  });
});
