// =============================================================================
// SessionSidebar — Unit Tests
// =============================================================================
// Tests cover: session list sorting (pinned first, by updated_at), new chat
// creation flows, pin/rename/delete actions, active highlighting, mobile
// drawer vs desktop sider, and error state.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionSidebar } from "./SessionSidebar";

// jsdom does not implement Element.scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

// ---------------------------------------------------------------------------
// Mock external dependencies
// ---------------------------------------------------------------------------

const {
  mockListSessions,
  mockCreateSession,
  mockDeleteSession,
  mockUpdateSession,
  mockImportSession,
} = vi.hoisted(() => ({
  mockListSessions: vi.fn(),
  mockCreateSession: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockUpdateSession: vi.fn(),
  mockImportSession: vi.fn(),
}));

vi.mock("../services/chat", () => ({
  listSessions: mockListSessions,
  createSession: mockCreateSession,
  deleteSession: mockDeleteSession,
  updateSession: mockUpdateSession,
  exportSession: vi.fn(),
  importSession: mockImportSession,
  addTagToSession: vi.fn(),
  removeTagFromSession: vi.fn(),
}));

// Mock AuthProvider
const mockLogout = vi.fn();
vi.mock("../../../providers/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: "u1",
      email: "admin@test.com",
      display_name: "Admin User",
      role: "admin",
      tenant_id: "t1",
      is_active: true,
      default_model_id: null,
      created_at: "2024-01-01T00:00:00Z",
    },
    logout: mockLogout,
  }),
}));

// Mock react-router-dom
const mockNavigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useParams: () => ({ sessionId: "session-1" }),
  useNavigate: () => mockNavigate,
  useLocation: () => ({ pathname: "/chat/session-1" }),
}));

// Mock child components from barrel export
vi.mock("./", () => ({
  ContextIndicator: () => <div data-testid="context-indicator" />,
  MemoryManager: ({ open }: { open: boolean }) =>
    open ? <div data-testid="memory-manager" /> : null,
  SessionSearch: () => (
    <div data-testid="session-search" />
  ),
}));

// Mock shared Logo component
vi.mock("../../../shared/components/Logo", () => ({
  Logo: ({ size, showText }: { size: number; showText?: boolean }) => (
    <div data-testid="logo" data-size={size} data-show-text={String(!!showText)} />
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

const NOW = "2024-06-15T12:00:00Z";
const EARLIER = "2024-06-14T10:00:00Z";
const EARLIEST = "2024-06-13T08:00:00Z";

const SESSIONS = [
  {
    id: "session-pinned-1",
    title: "Pinned Session A",
    is_pinned: true,
    is_temporary: false,
    updated_at: NOW,
    tags: [],
  },
  {
    id: "session-pinned-2",
    title: "Pinned Session B",
    is_pinned: true,
    is_temporary: false,
    updated_at: EARLIER,
    tags: [],
  },
  {
    id: "session-1",
    title: "Active Session",
    is_pinned: false,
    is_temporary: false,
    updated_at: NOW,
    tags: [],
  },
  {
    id: "session-old",
    title: "Old Session",
    is_pinned: false,
    is_temporary: false,
    updated_at: EARLIEST,
    tags: [],
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderSidebar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SessionSidebar />
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

describe("SessionSidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListSessions.mockResolvedValue(SESSIONS);
    mockCreateSession.mockResolvedValue({ id: "new-session", title: "New Chat" });
    mockDeleteSession.mockResolvedValue(undefined);
    mockUpdateSession.mockResolvedValue(undefined);
    mockImportSession.mockResolvedValue({ session_id: "imported-session", message_count: 5 });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // ── Session list rendering (pinned first, sorted by updated_at) ────────

  it("renders sessions sorted with pinned first, then by updated_at descending", async () => {
    renderSidebar();
    await settle();

    // The rendered list items have data-session-id attributes
    const items = document.querySelectorAll("[data-session-id]");
    const ids = Array.from(items).map((el) => el.getAttribute("data-session-id"));

    // Expected order: pinned-1 (pinned, newest), pinned-2 (pinned, older),
    // session-1 (unpinned, newest), session-old (unpinned, oldest)
    expect(ids).toEqual([
      "session-pinned-1",
      "session-pinned-2",
      "session-1",
      "session-old",
    ]);
  });

  // ── Active session highlighting ───────────────────────────────────────

  it("highlights the active session", async () => {
    renderSidebar();
    await settle();

    const activeItem = document.querySelector(
      '[data-session-id="session-1"]',
    );
    expect(activeItem).toBeInTheDocument();
    // Active session gets a highlighted background
    expect(activeItem).toHaveStyle({ background: "#e6f4ff" });
  });

  // ── New Chat (lazy UUID) ──────────────────────────────────────────────

  it("creates a lazy UUID and navigates on New Chat click", async () => {
    const randomUUID = vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-0000-0000-000000000000");
    renderSidebar();
    await settle();

    const user = userEvent.setup();

    // Click the "New Chat" button
    const newChatBtn = screen.getByRole("button", { name: /new chat/i });
    await user.click(newChatBtn);

    expect(randomUUID).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith("/chat/00000000-0000-0000-0000-000000000000");

    randomUUID.mockRestore();
  });

  // ── Temporary Chat ────────────────────────────────────────────────────

  it("calls createSession and navigates on Temporary Chat", async () => {
    // Mock the Dropdown menu click
    // The "New Chat" button + dropdown are in a Space.Compact.
    renderSidebar();
    await settle();

    const user = userEvent.setup();

    // Find the dropdown trigger (the button with the DownOutlined icon)
    const dropdownBtn = document.querySelector(
      ".ant-dropdown-trigger",
    );
    expect(dropdownBtn).toBeInTheDocument();

    // Click the dropdown trigger to open the menu
    await user.click(dropdownBtn!);

    // Click "Temporary Chat" in the dropdown menu
    const tempChatOption = screen.getByText("Temporary Chat");
    await user.click(tempChatOption);

    expect(mockCreateSession).toHaveBeenCalledWith(expect.objectContaining({
      title: "New Chat",
      is_temporary: true,
      auto_route_enabled: true,
    }));
    expect(mockNavigate).toHaveBeenCalledWith("/chat/new-session");
  });

  // ── Pin action ────────────────────────────────────────────────────────

  it("toggles pin when pin button is clicked", async () => {
    renderSidebar();
    await settle();

    const user = userEvent.setup();

    // Find the pin button for "Old Session" (which is not pinned)
    const sessionItem = document.querySelector(
      '[data-session-id="session-old"]',
    );
    expect(sessionItem).toBeInTheDocument();

    // The pin button (PushpinOutlined) is in the actions
    const pinBtn = sessionItem!.querySelector(".anticon-pushpin");
    expect(pinBtn).toBeInTheDocument();
    await user.click(pinBtn!.closest("button")!);

    expect(mockUpdateSession).toHaveBeenCalledWith("session-old", {
      is_pinned: true,
    });
  });

  // ── Delete action ─────────────────────────────────────────────────────

  it("deletes a session and navigates away if it is the active session", async () => {
    renderSidebar();
    await settle();

    const user = userEvent.setup();

    // Find the delete button for "session-1" (the active session)
    const sessionItem = document.querySelector(
      '[data-session-id="session-1"]',
    );
    const deleteBtn = sessionItem!.querySelector(".anticon-delete");
    expect(deleteBtn).toBeInTheDocument();

    // Click the delete button to trigger Popconfirm
    await user.click(deleteBtn!.closest("button")!);

    // The Popconfirm OK button has text "Delete" — use exact match to avoid
    // colliding with the delete icon button's aria-label ("delete").
    const confirmBtn = screen.getByRole("button", { name: "Delete" });
    await user.click(confirmBtn);

    expect(mockDeleteSession).toHaveBeenCalledWith("session-1");
    // Since session-1 is the active session, navigate away
    expect(mockNavigate).toHaveBeenCalledWith("/chat");
  });

  // ── Rename action ─────────────────────────────────────────────────────

  it("opens edit modal, updates title, and calls updateSession on save", async () => {
    renderSidebar();
    await settle();

    const user = userEvent.setup();

    // Find the edit button for "Old Session"
    const sessionItem = document.querySelector(
      '[data-session-id="session-old"]',
    );
    const editBtn = sessionItem!.querySelector(".anticon-edit");
    expect(editBtn).toBeInTheDocument();

    // Click the edit button
    await user.click(editBtn!.closest("button")!);

    // Modal should appear with the session title
    const titleInput = screen.getByPlaceholderText("Chat title");
    expect(titleInput).toBeInTheDocument();
    expect(titleInput).toHaveValue("Old Session");

    // Change the title
    await user.clear(titleInput);
    await user.type(titleInput, "Renamed Session");

    // Click OK button on the modal
    const okBtn = screen.getByRole("button", { name: /ok/i });
    await user.click(okBtn);

    expect(mockUpdateSession).toHaveBeenCalledWith("session-old", {
      title: "Renamed Session",
    });
  });

  // ── Error state ───────────────────────────────────────────────────────

  it("shows error alert when listSessions fails", async () => {
    mockListSessions.mockRejectedValue(new Error("Network error"));

    renderSidebar();
    await settle();

    expect(screen.getByText("Failed to load sessions")).toBeInTheDocument();
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  // ── Loading state ─────────────────────────────────────────────────────

  it("shows loading indicator while sessions are fetching", async () => {
    // Never resolve so it stays in loading
    mockListSessions.mockImplementation(() => new Promise(() => {}));

    renderSidebar();
    await settle();

    // Ant Design List shows a Spin when loading
    const spinner = document.querySelector(".ant-spin");
    expect(spinner).toBeInTheDocument();
  });
});
