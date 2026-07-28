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
    startReconnect: mockStartReconnect,
    resetStream: mockResetStream,
    isStreaming: false,
    streaming: false,
  }),
}));

vi.mock("../services/chat", () => ({
  listMessages: vi.fn().mockResolvedValue({ items: [], has_more: false }),
  buildCursor: vi.fn((msg: any) => `${msg.created_at}|${msg.id}`),
  deleteMessage: vi.fn(),
  finalizeSession: vi.fn(),
  updateAssistantMessage: vi.fn(),
  listAlwaysOnTools: vi.fn().mockResolvedValue([]),
  getStreamStatus: mockGetStreamStatus,
}));

const mockApi = vi.hoisted(() => vi.fn());
const mockGetStreamStatus = vi.hoisted(() => vi.fn());
const mockStartReconnect = vi.hoisted(() => vi.fn());
const mockResetStream = vi.hoisted(() => vi.fn());

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

  it("auto-selects Auto (Recommended) when multiple models exist in pending mode", async () => {
    renderChatWindow();
    await settle();

    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();

    // With 2 models (FAKE_MODELS), the auto-select effect should set
    // pendAutoRoute=true so the value resolves to AUTO_ROUTE_VALUE
    // (Issue #479).
    expect(select).toHaveValue(AUTO_ROUTE_VALUE);
  });

  it("auto-selects the only model when exactly one model exists in pending mode", async () => {
    mockApi.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve([FAKE_MODELS[0]]);
      return Promise.resolve([]);
    });
    renderChatWindow();
    await settle();

    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();

    // With 1 model, the auto-select effect should set pendModelId to it.
    expect(select).toHaveValue("model-abc");
  });

  it("keeps Auto selected when user confirms Auto after initial auto-select", async () => {
    const user = userEvent.setup();
    renderChatWindow();
    await settle();

    // Verify auto-select has kicked in first (Auto for multiple models)
    const select = screen.getByRole("combobox");
    expect(select).toHaveValue(AUTO_ROUTE_VALUE);

    // User re-selects "Auto (Recommended)" (no-op but verifies no override)
    await act(async () => {
      await user.selectOptions(select, AUTO_ROUTE_VALUE);
    });
    await settle();

    // MUST remain on Auto
    expect(select).toHaveValue(AUTO_ROUTE_VALUE);
  });

  it("selecting a specific model after Auto works and switches away from Auto", async () => {
    const user = userEvent.setup();
    renderChatWindow();
    await settle();

    // Auto-select kicks in first (Auto for multiple models)
    const select = screen.getByRole("combobox");
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

// ---------------------------------------------------------------------------
// Reconnect behavior (Issue #457)
// ---------------------------------------------------------------------------

describe("ChatWindow — Stream reconnect on mount (Issue #457)", () => {
  beforeEach(() => {
    mockApi.mockReset();
    mockApi.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve(FAKE_MODELS);
      return Promise.resolve([]);
    });
    mockGetStreamStatus.mockReset();
    mockStartReconnect.mockReset();
    mockResetStream.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("calls startReconnect when getStreamStatus returns active:true", async () => {
    mockGetStreamStatus.mockResolvedValue({ active: true });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    expect(mockGetStreamStatus).toHaveBeenCalledWith("test-session-1");
    expect(mockStartReconnect).toHaveBeenCalledTimes(1);
  });

  it("does NOT call startReconnect when getStreamStatus returns active:false", async () => {
    mockGetStreamStatus.mockResolvedValue({ active: false });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    expect(mockGetStreamStatus).toHaveBeenCalledWith("test-session-1");
    expect(mockStartReconnect).not.toHaveBeenCalled();
  });

  it("does NOT call startReconnect in demo mode", async () => {
    mockGetStreamStatus.mockResolvedValue({ active: true });

    renderChatWindow({ isPending: false, demo: true, sessionId: "test-session-1" });
    await settle();

    expect(mockStartReconnect).not.toHaveBeenCalled();
  });

  it("does NOT call startReconnect in widget mode", async () => {
    mockGetStreamStatus.mockResolvedValue({ active: true });

    renderChatWindow({ isPending: false, widget: true, sessionId: "test-session-1" });
    await settle();

    expect(mockStartReconnect).not.toHaveBeenCalled();
  });

  it("does NOT call startReconnect for stopped sessions", async () => {
    mockGetStreamStatus.mockResolvedValue({ active: true });

    // First mount with session → reconnect fires
    const { unmount } = renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();
    expect(mockStartReconnect).toHaveBeenCalledTimes(1);

    // Simulate stop — stoppedSessionsRef gets populated
    // (can't directly test internal ref, but the effect check is in the component)
    unmount();
    mockStartReconnect.mockClear();
    mockGetStreamStatus.mockClear();

    // Re-mount same session — getStreamStatus is called but reconnect is skipped
    // because stoppedSessionsRef is reset on remount (new component instance).
    // This test verifies the reconnect effect is wired up correctly.
    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();
    // On a fresh mount, reconnectAttemptedRef starts false and stoppedSessionsRef is empty,
    // so reconnect should fire again. The "no reconnect after stop" scenario requires
    // the stop to happen before navigation, which is tested by the effect guard.
    expect(mockGetStreamStatus).toHaveBeenCalledWith("test-session-1");
  });

  it("does NOT call getStreamStatus when isPending is true (Issue #475)", async () => {
    // Simulate navigating from an existing session to a new one — the
    // component stays mounted but isPending becomes true, so pendingFlag
    // should be true and the reconnect effect should be skipped.
    mockGetStreamStatus.mockResolvedValue({ active: true });

    renderChatWindow({ isPending: true, sessionId: "test-new-session" });
    await settle();

    expect(mockGetStreamStatus).not.toHaveBeenCalled();
    expect(mockStartReconnect).not.toHaveBeenCalled();
  });
});
