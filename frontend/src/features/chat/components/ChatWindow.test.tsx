// =============================================================================
// ChatWindow — Regression Tests
// =============================================================================
// Tests focused on the auto-model-select effect interaction with the "Auto"
// model setting in pending (lazy-created) sessions.
//
// Bug #400: Selecting "⚡ Auto (Recommended)" in a new chat was immediately
// overridden by the auto-select effect, which re-selected the first model
// from the list because it didn't check pendAutoRoute.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { ChatWindow } from "./ChatWindow";

// ---------------------------------------------------------------------------
// Mock ALL child components from the barrel export so that only the
// ChatWindow orchestration logic (handleModelChange, handleSettingsUpdate,
// the auto-select useEffect) is exercised.  This avoids cascading failures
// from SessionToolActivation, MemoryManager, etc. making unmocked API calls.
// ---------------------------------------------------------------------------

vi.mock("./", () => ({
  ModelSelector: ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (id: string) => void;
  }) => (
    <select
      role="combobox"
      aria-label="Model"
      data-testid="model-selector"
      value={value ?? ""}
      onChange={(e) => onChange?.(e.target.value)}
    >
      <option value="__auto__">⚡ Auto (Recommended)</option>
      <option value="model-abc">DeepSeek V4 (deepseek)</option>
      <option value="model-xyz">GPT-5 (openai)</option>
    </select>
  ),
  TemplateSelector: () => <div data-testid="template-selector" />,
  SkillSelector: () => <div data-testid="skill-selector" />,
  PromptLibrary: () => <div data-testid="prompt-library" />,
  TemporaryChatBadge: () => <div data-testid="temp-badge" />,
  SessionToolActivation: () => <div data-testid="tools-activation" />,
  MemoryManager: () => <div data-testid="memory-manager" />,
}));

const AUTO_ROUTE_VALUE = "__auto__";

// ---------------------------------------------------------------------------
// Mock external dependencies
// ---------------------------------------------------------------------------

vi.mock("../hooks/useStream", () => ({
  useStream: () => ({
    sendMessage: vi.fn(),
    startStream: vi.fn(),
    stopStream: vi.fn(),
    startRegenerateStream: vi.fn(),
    startEditStream: vi.fn(),
    startReconnect: vi.fn(),
    resetStream: vi.fn(),
    isStreaming: false,
    streaming: false,
  }),
}));

vi.mock("../services/chat", () => ({
  listMessages: vi.fn().mockResolvedValue([]),
  deleteMessage: vi.fn(),
  finalizeSession: vi.fn(),
  updateAssistantMessage: vi.fn(),
  listAlwaysOnTools: vi.fn().mockResolvedValue([]),
}));

const mockApi = vi.hoisted(() => vi.fn());
vi.mock("../../../services/api", () => ({
  default: mockApi,
  getToken: () => "test-token",
}));

vi.mock("react-virtuoso", () => ({
  Virtuoso: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="virtuoso">{children}</div>
  ),
  VirtuosoHandle: {},
}));

vi.mock("../services/demo", () => ({
  getDemoMessages: vi.fn().mockResolvedValue([]),
}));

vi.mock("../services/widget", () => ({
  getWidgetMessages: vi.fn().mockResolvedValue([]),
}));

// Mock Ant Design's Grid.useBreakpoint to return desktop breakpoints.
// The default test-setup matchMedia stub returns matches: false for every
// query, which triggers the mobile layout in ChatWindow (the ModelSelector
// is hidden behind an "Options" button).  We mock on the main "antd" entry
// so that `const { useBreakpoint } = Grid` picks up the override.
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
    name: "DeepSeek V4",
    provider: "deepseek",
    enabled: true,
    thinking_enabled: false,
  },
  {
    id: "model-xyz",
    name: "GPT-5",
    provider: "openai",
    enabled: true,
    thinking_enabled: false,
  },
];

function renderChatWindow(props: Record<string, unknown> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatWindow
        sessionId="test-pending-id"
        isPending={true}
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

describe("ChatWindow — Auto model selection (Issue #400)", () => {
  beforeEach(() => {
    mockApi.mockReset();
    mockApi.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve(FAKE_MODELS);
      return Promise.resolve([]);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("auto-selects the first model when no model is chosen in pending mode", async () => {
    renderChatWindow();
    await settle();

    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();

    // The auto-select effect should have set pendModelId to the first model
    // ("model-abc") because pendAutoRoute is false.
    expect(select).toHaveValue("model-abc");
  });

  it("keeps Auto selected when user chooses Auto after initial auto-select", async () => {
    const user = userEvent.setup();
    renderChatWindow();
    await settle();

    // Verify auto-select has kicked in first
    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("model-abc");

    // User selects "Auto (Recommended)" → sets pendModelId=null, pendAutoRoute=true
    await act(async () => {
      await user.selectOptions(select, AUTO_ROUTE_VALUE);
    });
    await settle();

    // MUST remain on Auto — the useEffect must NOT override it back to "model-abc"
    expect(select).toHaveValue(AUTO_ROUTE_VALUE);
  });

  it("selecting a specific model after Auto works and switches away from Auto", async () => {
    const user = userEvent.setup();
    renderChatWindow();
    await settle();

    // Step 1: Select Auto
    const select = screen.getByRole("combobox");
    await act(async () => {
      await user.selectOptions(select, AUTO_ROUTE_VALUE);
    });
    await settle();
    expect(select).toHaveValue(AUTO_ROUTE_VALUE);

    // Step 2: Select GPT-5 specifically
    await act(async () => {
      await user.selectOptions(select, "model-xyz");
    });
    await settle();
    expect(select).toHaveValue("model-xyz");
  });

  it("does not auto-select a model when session already has one (non-pending)", async () => {
    renderChatWindow({ isPending: false, selectedModelId: "model-xyz" });
    await settle();

    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("model-xyz");
  });

  it("shows Auto when autoRouteEnabled is true and no model selected (non-pending)", async () => {
    renderChatWindow({
      isPending: false,
      autoRouteEnabled: true,
      selectedModelId: undefined,
    });
    await settle();

    const select = screen.getByRole("combobox");
    expect(select).toHaveValue(AUTO_ROUTE_VALUE);
  });
});
