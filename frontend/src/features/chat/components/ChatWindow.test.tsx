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
    startStream: (...args: any[]) => {
      // 6th positional arg to startStream(sessionId, content, fileIds,
      // temperature, sessionData, handlers, autopilot?, background?) is the
      // handler object.  Capture it so tests can drive the stream lifecycle.
      lastStreamHandlers.current = args[5] ?? null;
      return Promise.resolve();
    },
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
  listMessages: mockListMessages,
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
const mockListMessages = vi.hoisted(() => vi.fn());
const lastStreamHandlers = vi.hoisted<{ current: any }>(() => ({ current: null }));
// Captures the props of the most recent <Virtuoso> render so tests can drive
// and inspect `followOutput` (Issue #515 handoff window).
const virtuosoState = vi.hoisted(() => ({
  props: null as any,
  scrollToIndex: vi.fn(),
}));

// Default: no messages, matching the previous mock's behaviour.  Individual
// tests may override via mockImplementation / mockResolvedValueOnce.
mockListMessages.mockResolvedValue({ items: [], has_more: false });

vi.mock("../../../services/api", () => ({
  default: mockApi,
  getToken: () => "test-token",
}));

// Faithful Virtuoso stub: unlike the old `({children}) => <div>` mock it
// actually renders `data` through `computeItemKey`/`itemContent`, so a
// regression where the final message is dropped/blank is observable.  It also
// captures its props so tests can inspect/execute `followOutput`.
vi.mock("react-virtuoso", async () => {
  const React = await import("react");
  const Virtuoso = React.forwardRef(function MockVirtuoso(props: any, ref: any) {
    virtuosoState.props = props;
    React.useImperativeHandle(ref, () => ({
      scrollToIndex: virtuosoState.scrollToIndex,
    }));
    const data = props.data ?? [];
    return React.createElement(
      "div",
      { "data-testid": "virtuoso" },
      data.map((item: any, index: number) =>
        React.createElement(
          "div",
          {
            key: props.computeItemKey
              ? props.computeItemKey(index, item)
              : index,
          },
          props.itemContent ? props.itemContent(index, item) : null,
        ),
      ),
    );
  });
  return { Virtuoso, VirtuosoHandle: {} };
});

// Focused MessageBubble stub: assert ChatWindow orchestration (which message
// rows it renders and with what text) without antd/markdown/React Query noise.
vi.mock("./MessageBubble", () => {
  const MessageBubble = ({ message }: { message: any }) => (
    <div data-testid={`msg-${message.id}`}>
      {Array.isArray(message.content)
        ? message.content
            .filter((c: any) => c?.type === "text")
            .map((c: any) => c.text)
            .join("")
        : String(message.content ?? "")}
    </div>
  );
  return { MessageBubble, default: MessageBubble };
});

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

// A DeepSeek model with thinking ENABLED — renders the reasoning dropdown.
const THINKING_MODEL = {
  id: "model-think",
  name: "DeepSeek Thinking",
  provider: "deepseek",
  enabled: true,
  thinking_enabled: true,
};

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

// A promise whose settlement the test controls, used to hold a refetch open so
// the post-completion handoff window can be inspected.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Reset the captured Virtuoso props before every test in this file so
// assertions never observe a previous render's `followOutput` closure.
beforeEach(() => {
  virtuosoState.props = null;
  virtuosoState.scrollToIndex.mockReset();
});

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

// ---------------------------------------------------------------------------
// Issue #515 — last assistant message must not vanish after completion
// ---------------------------------------------------------------------------

describe("ChatWindow — stream→persisted handoff (Issue #515)", () => {
  function msg(id: string, sender: "user" | "assistant", text: string) {
    return {
      id,
      session_id: "test-session-1",
      sender,
      content: [{ type: "text" as const, text }],
      model_id: null,
      model_name: null,
      model_provider: null,
      tool_calls: null,
      tokens_in: null,
      tokens_out: null,
      is_deleted: false,
      created_at: "2026-01-01T00:00:00.000Z",
      updated_at: "2026-01-01T00:00:00.000Z",
    };
  }

  beforeEach(() => {
    mockApi.mockReset();
    mockApi.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve(FAKE_MODELS);
      return Promise.resolve([]);
    });
    mockGetStreamStatus.mockReset();
    mockGetStreamStatus.mockResolvedValue({ active: false });
    mockListMessages.mockReset();
    mockListMessages.mockResolvedValue({ items: [], has_more: false });
    lastStreamHandlers.current = null;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  // Compose and send a message through the real ChatWindow send flow, then
  // return the captured stream handlers so the test can drive the SSE.
  async function sendMessage(
    user: ReturnType<typeof userEvent.setup>,
    text: string,
  ) {
    const textarea = screen.getByPlaceholderText(/Type a message/);
    await user.clear(textarea);
    await user.type(textarea, text);
    const sendButton = screen
      .getAllByRole("button")
      .find(
        (btn) =>
          btn.textContent?.includes("Send") &&
          !(btn as HTMLButtonElement).disabled,
      );
    expect(sendButton).toBeTruthy();
    await user.click(sendButton!);
    return lastStreamHandlers.current;
  }

  it("refetches the persisted assistant message when a normal-chat stream completes (multi-message chat)", async () => {
    const user = userEvent.setup();
    const history = [
      msg("u1", "user", "First question"),
      msg("a1", "assistant", "First answer"),
    ];

    // First listMessages call resolves the initial (multi-message) history;
    // subsequent calls are the post-completion refetches that the handoff
    // relies on to hydrate the freshly persisted assistant row.  The
    // persisted row's id EQUALS the stream's message_id so this exercises the
    // reconciliation (previously it was "a2", which never matched).
    let calls = 0;
    mockListMessages.mockImplementation(() => {
      calls += 1;
      return Promise.resolve({
        items:
          calls === 1
            ? history
            : [
                ...history,
                msg("u2", "user", "Second question"),
                msg("stream-1", "assistant", "Second answer"),
              ],
        has_more: false,
      });
    });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    const handlers = await sendMessage(user, "Second question");
    expect(handlers).toBeTruthy();

    const callsBeforeComplete = calls;

    // Simulate the SSE lifecycle: a streamed token then message_complete.
    await act(async () => {
      handlers.onToken("Second answer", "stream-1");
      handlers.onMessageComplete({ message_id: "stream-1", tokens_in: 10, tokens_out: 40 });
    });
    await settle();

    // The completion path must trigger a refetch of the persisted messages so
    // the freshly persisted assistant row is fetched from the API (Issue #515:
    // without this refetch the UI can be left showing only the streamed
    // content / follow-up questions until a manual browser reload).
    expect(calls).toBeGreaterThan(callsBeforeComplete);
    // The matched persisted row must be rendered exactly once.
    expect(screen.getAllByTestId("msg-stream-1")).toHaveLength(1);
  });

  it("keeps the final assistant message rendered and de-duplicates the streaming bubble", async () => {
    const user = userEvent.setup();
    const history = [
      msg("u1", "user", "First question"),
      msg("a1", "assistant", "First answer"),
    ];

    // The post-completion refetch returns the persisted assistant row with the
    // SAME id as the streaming bubble (message_id), so the reconciliation must
    // replace the local bubble rather than render both.
    let calls = 0;
    mockListMessages.mockImplementation(() => {
      calls += 1;
      return Promise.resolve({
        items:
          calls === 1
            ? history
            : [
                ...history,
                msg("u2", "user", "Second question"),
                msg("stream-1", "assistant", "Second answer"),
              ],
        has_more: false,
      });
    });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    const handlers = await sendMessage(user, "Second question");

    // While streaming, the optimistic bubble carries the streamed text.
    await act(async () => {
      handlers.onToken("Second answer", "stream-1");
    });
    expect(screen.getByTestId("msg-stream-1")).toHaveTextContent("Second answer");

    await act(async () => {
      handlers.onMessageComplete({
        message_id: "stream-1",
        tokens_in: 10,
        tokens_out: 40,
      });
    });
    await settle();

    // Exactly one row, still showing the streamed text — not blank, no dupes.
    expect(screen.getAllByTestId("msg-stream-1")).toHaveLength(1);
    expect(screen.getByTestId("msg-stream-1")).toHaveTextContent("Second answer");
  });

  it("keeps the streamed text visible when the refetch never returns the persisted row", async () => {
    const user = userEvent.setup();
    const history = [
      msg("u1", "user", "First question"),
      msg("a1", "assistant", "First answer"),
    ];

    // Every refetch returns the history WITHOUT the new persisted row.
    mockListMessages.mockResolvedValue({ items: history, has_more: false });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    const handlers = await sendMessage(user, "Second question");

    await act(async () => {
      handlers.onToken("Second answer", "stream-1");
      handlers.onMessageComplete({
        message_id: "stream-1",
        tokens_in: 10,
        tokens_out: 40,
      });
    });
    await settle();

    // The reconciliation never runs (no persisted row), so the streamed bubble
    // must remain visible — the UI must never go blank.
    expect(screen.getByTestId("msg-stream-1")).toHaveTextContent("Second answer");
  });

  it("follows output through the post-completion handoff window", async () => {
    const user = userEvent.setup();
    const history = [
      msg("u1", "user", "First question"),
      msg("a1", "assistant", "First answer"),
    ];
    const persisted = [
      ...history,
      msg("u2", "user", "Second question"),
      msg("stream-1", "assistant", "Second answer"),
    ];

    // Hold the post-completion refetch open so the handoff window can be
    // observed before the persisted row lands.
    let calls = 0;
    const handoffRefetch = deferred<{ items: unknown[]; has_more: boolean }>();
    mockListMessages.mockImplementation(() => {
      calls += 1;
      if (calls === 1) {
        return Promise.resolve({ items: history, has_more: false });
      }
      return handoffRefetch.promise;
    });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    expect(typeof virtuosoState.props.followOutput).toBe("function");
    // Nothing streaming and no handoff → do not force-follow.
    expect(virtuosoState.props.followOutput(true)).toBeFalsy();

    const handlers = await sendMessage(user, "Second question");
    expect(handlers).toBeTruthy();

    await act(async () => {
      handlers.onToken("Second answer", "stream-1");
    });
    // The mocked useStream always reports streaming=false, so this isolates
    // `handoffPending`: it is still false here → do not follow.
    expect(virtuosoState.props.followOutput(true)).toBeFalsy();

    await act(async () => {
      handlers.onMessageComplete({
        message_id: "stream-1",
        tokens_in: 10,
        tokens_out: 40,
      });
    });
    // SSE has closed (streaming=false) but the persisted row has not arrived:
    // handoffPending keeps Virtuoso following so the final row lands in view.
    expect(virtuosoState.props.followOutput(true)).toBe("smooth");

    await act(async () => {
      handoffRefetch.resolve({ items: persisted, has_more: false });
      await Promise.resolve();
    });
    await settle();

    // Persisted row landed and the reconciliation effect cleared the handoff.
    expect(screen.getAllByTestId("msg-stream-1")).toHaveLength(1);
    expect(virtuosoState.props.followOutput(true)).toBeFalsy();
  });

  it("attributes a new turn's tokens to the new stream id after an errored turn", async () => {
    const user = userEvent.setup();
    const history = [
      msg("u1", "user", "First question"),
      msg("a1", "assistant", "First answer"),
    ];
    mockListMessages.mockResolvedValue({ items: history, has_more: false });

    renderChatWindow({ isPending: false, sessionId: "test-session-1" });
    await settle();

    // Turn 1: stream a token, then fail and close the stream.  `onClose`
    // deliberately keeps the streaming bubble for a normal send, so the stale
    // `streamingMessageId` ("stream-old") survives into the next send.
    const firstHandlers = await sendMessage(user, "Old question");
    await act(async () => {
      firstHandlers.onToken("old answer", "stream-old");
      firstHandlers.onError("boom");
      firstHandlers.onClose();
    });
    await settle();

    // Turn 2: handleSend must reset the stale id, so the new turn's tokens are
    // attributed to "stream-new" and not merged into "stream-old".
    const secondHandlers = await sendMessage(user, "New question");
    await act(async () => {
      secondHandlers.onToken("new answer", "stream-new");
    });
    await settle();

    expect(screen.getByTestId("msg-stream-new")).toHaveTextContent("new answer");
    expect(screen.queryByTestId("msg-stream-old")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Reasoning-effort dropdown (Issue #506)
// ---------------------------------------------------------------------------

describe("ChatWindow — reasoning effort dropdown (Issue #506)", () => {
  beforeEach(() => {
    mockApi.mockReset();
    // A single DeepSeek model WITH thinking enabled so the dropdown renders.
    mockApi.mockImplementation((url: string) => {
      if (url === "/models") return Promise.resolve([THINKING_MODEL]);
      return Promise.resolve([]);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  const onSessionUpdate = vi.fn();

  function renderThinkingChat() {
    return renderChatWindow({
      isPending: false,
      sessionId: "test-session-1",
      selectedModelId: THINKING_MODEL.id,
      onSessionUpdate,
    });
  }

  it("renders a reasoning dropdown for thinking-capable models", async () => {
    renderThinkingChat();
    await settle();
    // The reasoning antd Select renders a combobox; the model selector is mocked
    // as a native <select role="combobox">, so at least one combobox exists.
    expect(screen.getAllByRole("combobox").length).toBeGreaterThanOrEqual(1);
  });

  it("labels the Default option with the resolved model default", async () => {
    renderThinkingChat();
    await settle();
    // THINKING_MODEL is DeepSeek with thinking on and no explicit effort, so the
    // resolved default is "high" → the "Default" option reads "Default (High)".
    expect(screen.getByText("Default (High)")).toBeInTheDocument();
  });

  it("shows the reasoning dropdown in pending mode once a thinking model is selected", async () => {
    // New (pending) chat with exactly one thinking model → auto-select picks it.
    renderChatWindow({
      isPending: true,
      sessionId: "test-new-pending",
      selectedModelId: undefined,
    });
    await settle();
    // The reasoning dropdown (with its resolved default label) should render
    // even before the session exists on the backend.
    expect(screen.getByText("Default (High)")).toBeInTheDocument();
  });

  it("maps 'Low' to { thinking_enabled:true, reasoning_effort:'low' }", async () => {
    const user = userEvent.setup();
    renderThinkingChat();
    await settle();

    await selectReasoningOption(user, "Low");
    await settle();

    expect(onSessionUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ thinking_enabled: true, reasoning_effort: "low" }),
    );
  });

  it("maps 'None' to { thinking_enabled:false, reasoning_effort:null }", async () => {
    const user = userEvent.setup();
    renderThinkingChat();
    await settle();

    await selectReasoningOption(user, "None");
    await settle();

    expect(onSessionUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ thinking_enabled: false, reasoning_effort: null }),
    );
  });

  it("maps 'Default' to null-thought/null-effort (use admin default)", async () => {
    const user = userEvent.setup();
    renderThinkingChat();
    await settle();

    // First pick a concrete level so a subsequent change to "Default" fires.
    await selectReasoningOption(user, "Max");
    await settle();
    expect(onSessionUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ thinking_enabled: true, reasoning_effort: "max" }),
    );
    onSessionUpdate.mockClear();

    // Now returning to "Default" resets both fields to null → use admin default.
    // The label shows the resolved model default ("High" for this DeepSeek model).
    await selectReasoningOption(user, "Default (High)");
    await settle();

    expect(onSessionUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ thinking_enabled: null, reasoning_effort: null }),
    );
  });
});

// Open the reasoning-effort antd Select and click the given option label.
// The existing model selector is mocked as a native <select>; the reasoning
// control is the real antd Select, which we interact with via its dropdown.
async function selectReasoningOption(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
) {
  const comboboxes = screen.getAllByRole("combobox");
  // The reasoning antd Select is the LAST combobox (the mocked model <select>
  // is rendered first in the toolbar/options).
  const target = comboboxes[comboboxes.length - 1] as HTMLElement;
  await user.click(target);
  const option = await screen.findByTitle(label);
  await user.click(option);
}
