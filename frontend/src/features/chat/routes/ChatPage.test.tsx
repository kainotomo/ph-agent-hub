// =============================================================================
// ChatPage — Unit Tests
// =============================================================================
// Tests cover: routing to new/saved/temp sessions, correct isPending prop
// derivation, session loading and 404 fallback, welcome screen with
// New Chat / Temporary Chat buttons.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ChatPage } from "./ChatPage";

// ---------------------------------------------------------------------------
// Mock external dependencies
// ---------------------------------------------------------------------------

const {
  mockGetSession,
  mockCreateSession,
  mockUpdateSession,
} = vi.hoisted(() => ({
  mockGetSession: vi.fn(),
  mockCreateSession: vi.fn(),
  mockUpdateSession: vi.fn(),
}));

vi.mock("../services/chat", () => ({
  getSession: mockGetSession,
  createSession: mockCreateSession,
  updateSession: mockUpdateSession,
}));

// Mock react-router-dom at the test level so we can control sessionId per test
const mockNavigate = vi.fn();
let mockSessionId: string | undefined = undefined;

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useParams: () => ({ sessionId: mockSessionId }),
    useNavigate: () => mockNavigate,
  };
});

// Mock child components
vi.mock("../components/SessionSidebar", () => ({
  SessionSidebar: () => <div data-testid="session-sidebar" />,
}));

vi.mock("../components/ChatWindow", () => ({
  ChatWindow: (props: Record<string, unknown>) => (
    <div
      data-testid="chat-window"
      data-session-id={props.sessionId as string}
      data-is-pending={String(!!props.isPending)}
      data-is-temporary={String(!!props.isTemporary)}
    />
  ),
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

const FAKE_SESSION = {
  id: "session-1",
  title: "Test Session",
  is_temporary: false,
  selected_model_id: "model-abc",
  selected_template_id: null,
  selected_skill_id: "skill-1",
  temperature: 0.7,
  cross_session_retrieval_enabled: true,
  auto_route_enabled: false,
  auto_select_tools: true,
  is_pinned: false,
  tags: [],
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderChatPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
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

describe("ChatPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockReset();
    mockGetSession.mockReset();
    mockCreateSession.mockReset();
    mockUpdateSession.mockReset();
    mockSessionId = undefined;

    mockGetSession.mockResolvedValue(FAKE_SESSION);
    mockCreateSession.mockResolvedValue({ id: "new-temp", title: "New Chat" });
    mockUpdateSession.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // ── No sessionId → welcome screen ─────────────────────────────────────

  it("renders welcome screen with New Chat buttons when no sessionId in URL", async () => {
    mockSessionId = undefined;
    renderChatPage();
    await settle();

    expect(screen.getByText("Welcome to PH Agent Hub")).toBeInTheDocument();
    expect(screen.getByText("Select a conversation from the sidebar or start a new one")).toBeInTheDocument();

    const newChatBtn = screen.getByRole("button", { name: /new chat/i });
    expect(newChatBtn).toBeInTheDocument();

    const newTempChatBtn = screen.getByRole("button", { name: /new temporary chat/i });
    expect(newTempChatBtn).toBeInTheDocument();
  });

  // ── New Chat button creates UUID and navigates ─────────────────────────

  it("generates a UUID and navigates on New Chat button click", async () => {
    const randomUUID = vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-0000-0000-000000000000");
    mockSessionId = undefined;
    renderChatPage();
    await settle();

    const user = userEvent.setup();
    const newChatBtn = screen.getByRole("button", { name: /new chat/i });
    await user.click(newChatBtn);

    expect(randomUUID).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith("/chat/00000000-0000-0000-0000-000000000000");

    randomUUID.mockRestore();
  });

  // ── Temporary Chat button creates session and navigates ────────────────

  it("calls createSession and navigates on New Temporary Chat click", async () => {
    mockSessionId = undefined;
    renderChatPage();
    await settle();

    const user = userEvent.setup();
    const newTempChatBtn = screen.getByRole("button", { name: /new temporary chat/i });
    await user.click(newTempChatBtn);

    expect(mockCreateSession).toHaveBeenCalledWith({
      title: "New Chat",
      is_temporary: true,
    });
    expect(mockNavigate).toHaveBeenCalledWith("/chat/new-temp");
  });

  // ── sessionId with loaded session → ChatWindow with props ──────────────

  it("renders ChatWindow with correct props when session is loaded", async () => {
    mockSessionId = "session-1";
    renderChatPage();
    await settle();

    const chatWindow = screen.getByTestId("chat-window");
    expect(chatWindow).toBeInTheDocument();
    expect(chatWindow).toHaveAttribute("data-session-id", "session-1");
    expect(chatWindow).toHaveAttribute("data-is-pending", "false");
    expect(chatWindow).toHaveAttribute("data-is-temporary", "false");
  });

  // ── sessionId with loading → Spin ──────────────────────────────────────

  it("shows loading spinner while session is being fetched", async () => {
    mockSessionId = "session-1";
    // Don't resolve the query
    mockGetSession.mockImplementation(() => new Promise(() => {}));

    renderChatPage();
    await settle();

    expect(document.querySelector(".ant-spin")).toBeInTheDocument();
  });

  // ── sessionId with 404 → ChatWindow with isPending ─────────────────────

  it("renders ChatWindow with isPending when session is not found (404)", async () => {
    mockSessionId = "nonexistent-id";
    mockGetSession.mockRejectedValue(new Error("Not found"));

    renderChatPage();
    await settle();

    const chatWindow = screen.getByTestId("chat-window");
    expect(chatWindow).toBeInTheDocument();
    expect(chatWindow).toHaveAttribute("data-session-id", "nonexistent-id");
    expect(chatWindow).toHaveAttribute("data-is-pending", "true");
  });

  // ── SessionSidebar always rendered ─────────────────────────────────────

  it("always renders the SessionSidebar", async () => {
    mockSessionId = undefined;
    renderChatPage();
    await settle();

    expect(screen.getByTestId("session-sidebar")).toBeInTheDocument();
  });
});
