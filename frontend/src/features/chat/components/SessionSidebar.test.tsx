// =============================================================================
// SessionSidebar — Unit Tests
// =============================================================================
// Tests cover: session list sorting (pinned first, by updated_at), new chat
// creation flows, pin/rename/delete actions, active highlighting, mobile
// drawer vs desktop sider, and error state.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act, within, fireEvent } from "@testing-library/react";
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
  mockDeleteSessions,
  mockUpdateSession,
  mockImportSession,
  mockGetSession,
  mockListFolders,
  mockCreateFolder,
  mockUpdateFolder,
  mockDeleteFolder,
} = vi.hoisted(() => ({
  mockListSessions: vi.fn(),
  mockCreateSession: vi.fn(),
  mockDeleteSession: vi.fn(),
  mockDeleteSessions: vi.fn(),
  mockUpdateSession: vi.fn(),
  mockImportSession: vi.fn(),
  mockGetSession: vi.fn(),
  mockListFolders: vi.fn(),
  mockCreateFolder: vi.fn(),
  mockUpdateFolder: vi.fn(),
  mockDeleteFolder: vi.fn(),
}));

vi.mock("../services/chat", () => ({
  listSessions: mockListSessions,
  createSession: mockCreateSession,
  deleteSession: mockDeleteSession,
  deleteSessions: mockDeleteSessions,
  updateSession: mockUpdateSession,
  getSession: mockGetSession,
  exportSession: vi.fn(),
  importSession: mockImportSession,
  addTagToSession: vi.fn(),
  removeTagFromSession: vi.fn(),
  listFolders: mockListFolders,
  createFolder: mockCreateFolder,
  updateFolder: mockUpdateFolder,
  deleteFolder: mockDeleteFolder,
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

// Track onSelect calls for SessionSearch tests
let sessionSearchOnSelect: ((session: any) => void) | null = null;

// Mock SessionSearch directly (Sidebar imports from ./SessionSearch)
vi.mock("./SessionSearch", () => ({
  SessionSearch: ({ onSelect }: { onSelect?: (session: any) => void }) => {
    sessionSearchOnSelect = onSelect ?? null;
    return <div data-testid="session-search" />;
  },
  default: ({ onSelect }: { onSelect?: (session: any) => void }) => {
    sessionSearchOnSelect = onSelect ?? null;
    return <div data-testid="session-search" />;
  },
}));

// Mock child components from barrel export
vi.mock("./", () => ({
  ContextIndicator: () => <div data-testid="context-indicator" />,
  MemoryManager: ({ open }: { open: boolean }) =>
    open ? <div data-testid="memory-manager" /> : null,
  SessionSearch: ({ onSelect }: { onSelect?: (session: any) => void }) => {
    sessionSearchOnSelect = onSelect ?? null;
    return <div data-testid="session-search" />;
  },
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

// Mock react-virtuoso: render all items (not just visible ones) for tests.
// Uses a plain function component since forwardRef/useImperativeHandle
// can't be used inside vi.mock factories without breaking tsc.
vi.mock("react-virtuoso", () => ({
  VirtuosoHandle: class {},
  Virtuoso: ({ data, itemContent, style }: any) => (
    <div style={style} data-testid="virtuoso-list">
      {data.map((item: any, index: number) => (
        <div key={index} data-testid="virtuoso-item">
          {itemContent(index, item)}
        </div>
      ))}
    </div>
  ),
}));

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
    mockListFolders.mockResolvedValue([]);
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

  // ── Scroll to active session via Virtuoso scrollToIndex ──────────────

  it("scrolls to active session without crashing (virtuosoRef may be null in test)", async () => {
    // The auto-scroll effect calls virtuosoRef.current?.scrollToIndex(...).
    // In tests the ref is null (vi.mock can't forward refs without tsc
    // errors), but the optional chaining ensures no crash.  The important
    // behavior — session highlighting — is verified by the
    // "highlights the active session" test above.
    renderSidebar();
    await settle();

    // Session highlighting still works
    const activeItem = document.querySelector(
      '[data-session-id="session-1"]',
    );
    expect(activeItem).toBeInTheDocument();
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

  // ── Batch delete: selection mode toggle ───────────────────────────────

  it("toggles selection mode when select button is clicked", async () => {
    renderSidebar();
    await settle();

    // Find the select button (CheckSquareOutlined icon)
    const selectBtn = document.querySelector(".anticon-check-square");
    expect(selectBtn).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(selectBtn!.closest("button")!);

    // Checkboxes should now appear on session items
    const checkboxes = document.querySelectorAll(".ant-checkbox");
    expect(checkboxes.length).toBeGreaterThan(0);

    // Batch action bar should appear with "0 selected"
    expect(screen.getByText("0 selected")).toBeInTheDocument();

    // Click the select button again to exit selection mode
    const closeBtn = document.querySelector(".anticon-close");
    await user.click(closeBtn!.closest("button")!);

    // Checkboxes should disappear
    const checkboxesAfter = document.querySelectorAll(".ant-checkbox");
    expect(checkboxesAfter.length).toBe(0);
  });

  // ── Batch delete: select and delete sessions ──────────────────────────

  it("selects sessions and calls deleteSessions on confirm", async () => {
    mockDeleteSessions.mockResolvedValue({
      deleted: 2,
      skipped: [],
      errors: [],
    });

    renderSidebar();
    await settle();

    const user = userEvent.setup();

    // Enter selection mode
    const selectBtn = document.querySelector(".anticon-check-square");
    await user.click(selectBtn!.closest("button")!);

    // Click two session items to select them in selection mode
    const sessionItems = document.querySelectorAll("[data-session-id]");
    await user.click(sessionItems[0]); // session-pinned-1
    await user.click(sessionItems[2]); // session-1 (active)

    // The action bar should show "2 selected"
    expect(screen.getByText("2 selected")).toBeInTheDocument();

    // Click "Delete Selected" button
    const deleteSelectedBtn = screen.getByRole("button", { name: /delete selected/i });
    await user.click(deleteSelectedBtn);

    // Confirmation modal should appear — check that modal exists
    expect(document.querySelector(".ant-modal-confirm-title")).toBeInTheDocument();

    // Click the OK button in the modal
    const confirmBtn = screen.getByRole("button", { name: "Delete" });
    await user.click(confirmBtn);

    // Wait for the mutation to resolve
    await settle();

    expect(mockDeleteSessions).toHaveBeenCalledWith([
      "session-pinned-1",
      "session-1",
    ]);

    // Since session-1 was the active session, should navigate away
    expect(mockNavigate).toHaveBeenCalledWith("/chat");
  });

  // ── Batch delete: disabled for streaming sessions ─────────────────────

  it("disables the select button when sessions list is empty", async () => {
    mockListSessions.mockResolvedValue([]);
    renderSidebar();
    await settle();

    // Select button should not be rendered when no sessions
    const selectBtn = document.querySelector(".anticon-check-square");
    expect(selectBtn).not.toBeInTheDocument();
  });

  // ── SessionSearch onSelect prepends session to cache ──────────────────

  it("prepends searched session to sidebar query cache when onSelect is called", async () => {
    // Create a query client we control so we can inspect cache state
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    // Pre-populate with the fixture sessions
    queryClient.setQueryData(["sessions"], SESSIONS);

    render(
      <QueryClientProvider client={queryClient}>
        <SessionSidebar />
      </QueryClientProvider>,
    );
    await settle();

    // Open the search drawer so SessionSearch renders and captures onSelect
    const searchBtn = document.querySelector<HTMLElement>("[data-testid='session-search-btn']");
    expect(searchBtn).toBeTruthy();
    await act(async () => {
      searchBtn!.click();
    });
    await settle();

    expect(sessionSearchOnSelect).toBeTruthy();

    const newSession = {
      id: "session-searched",
      title: "Searched Session",
      is_pinned: false,
      is_temporary: false,
      updated_at: NOW,
      tags: [],
    };

    // Call the SessionSearch onSelect callback
    await act(async () => {
      sessionSearchOnSelect?.(newSession);
      // Advance microtasks so React Query subscribers fire synchronously
      await new Promise((r) => setTimeout(r, 0));
    });
    await settle();

    // Verify the cache was updated by reading from the same queryClient
    const sessionIds = queryClient
      .getQueryData<typeof SESSIONS>(["sessions"])
      ?.map((s) => s.id) ?? [];
    expect(sessionIds[0]).toBe("session-searched");
  });
});

// =============================================================================
// Issue #526 — folders in the chat sidebar
// =============================================================================

const FOLDERS = [
  { id: "folder-work", name: "Work", color: "#1677ff", sort_order: 0 },
  { id: "folder-home", name: "Personal", color: null, sort_order: 1 },
];

const FOLDER_SESSIONS = [
  {
    id: "session-w",
    title: "Work chat",
    is_pinned: false,
    is_temporary: false,
    folder_id: "folder-work",
    updated_at: NOW,
    tags: [],
  },
  {
    id: "session-h",
    title: "Home chat",
    is_pinned: false,
    is_temporary: false,
    folder_id: "folder-home",
    updated_at: EARLIER,
    tags: [],
  },
  {
    id: "session-1",
    title: "Unfiled chat",
    is_pinned: false,
    is_temporary: false,
    folder_id: null,
    updated_at: EARLIEST,
    tags: [],
  },
];

const COLLAPSE_KEY = "ph.sidebar.collapsedFolders";

/** Minimal DataTransfer stand-in — jsdom does not implement one. */
function makeDataTransfer() {
  const store: Record<string, string> = {};
  return {
    setData: (type: string, value: string) => {
      store[type] = value;
    },
    getData: (type: string) => store[type] ?? "",
    effectAllowed: "",
    dropEffect: "",
  };
}

function rowFor(sessionId: string): HTMLElement {
  const el = document.querySelector(`[data-session-id="${sessionId}"]`);
  if (!el) throw new Error(`No row rendered for session ${sessionId}`);
  return el as HTMLElement;
}

async function openFolderMenu(
  user: ReturnType<typeof userEvent.setup>,
  folderName: string,
) {
  const trigger = document.querySelector(
    `[aria-label="Folder actions for ${folderName}"]`,
  );
  expect(trigger).toBeTruthy();
  await user.click(trigger as HTMLElement);
  return within(await screen.findByRole("menu"));
}

describe("SessionSidebar — folders (Issue #526)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // antd's imperative Modal.confirm renders into its own root, which
    // RTL's cleanup() does not unmount — drop anything left by earlier tests.
    document
      .querySelectorAll(".ant-modal-root")
      .forEach((node) => node.remove());
    mockListSessions.mockResolvedValue(FOLDER_SESSIONS);
    mockListFolders.mockResolvedValue(FOLDERS);
    mockCreateFolder.mockResolvedValue({
      id: "folder-new",
      name: "Research",
      color: null,
      sort_order: 2,
    });
    mockUpdateFolder.mockResolvedValue(FOLDERS[0]);
    mockDeleteFolder.mockResolvedValue(undefined);
    mockUpdateSession.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  // ── Grouping ──────────────────────────────────────────────────────────

  it("groups sessions under their folders, with Unfiled last", async () => {
    renderSidebar();
    await settle();

    const headers = Array.from(
      document.querySelectorAll('[aria-label^="Collapse "], [aria-label^="Expand "]'),
    );
    expect(headers).toHaveLength(3); // Work, Personal, Unfiled

    // Each session renders under its own folder.
    expect(rowFor("session-w")).toBeInTheDocument();
    expect(rowFor("session-h")).toBeInTheDocument();
    expect(rowFor("session-1")).toBeInTheDocument();

    // Headers carry their session count.
    expect(screen.getByText("Work").closest("div")).toHaveTextContent("1");
    expect(screen.getByText("Personal").closest("div")).toHaveTextContent("1");
    expect(screen.getByText("Unfiled").closest("div")).toHaveTextContent("1");
  });

  it("shows a placeholder for an empty folder", async () => {
    mockListFolders.mockResolvedValue([
      { id: "folder-empty", name: "Empty", color: null, sort_order: 0 },
    ]);

    renderSidebar();
    await settle();

    expect(screen.getByText("Empty")).toBeInTheDocument();
    expect(screen.getByText("No chats yet")).toBeInTheDocument();
  });

  // ── Collapse ──────────────────────────────────────────────────────────

  it("collapses a folder on click and remembers it in localStorage", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    expect(rowFor("session-w")).toBeInTheDocument();

    await user.click(
      document.querySelector('[aria-label="Collapse Work"]') as HTMLElement,
    );
    await settle();

    expect(document.querySelector('[data-session-id="session-w"]')).toBeNull();
    // Other folders are unaffected.
    expect(rowFor("session-h")).toBeInTheDocument();

    expect(
      JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]"),
    ).toContain("folder-work");
  });

  it("restores the collapsed state from localStorage", async () => {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(["folder-work"]));

    renderSidebar();
    await settle();

    expect(document.querySelector('[data-session-id="session-w"]')).toBeNull();
    expect(
      document.querySelector('[aria-label="Expand Work"]'),
    ).toBeInTheDocument();
  });

  it("auto-expands the folder holding the active session", async () => {
    // The mocked useParams() reports sessionId "session-1"; put it in a
    // folder that starts collapsed.
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(["folder-work"]));
    mockListSessions.mockResolvedValue([
      {
        id: "session-1",
        title: "Active in Work",
        is_pinned: false,
        is_temporary: false,
        folder_id: "folder-work",
        updated_at: NOW,
        tags: [],
      },
    ]);

    renderSidebar();
    await settle();

    expect(rowFor("session-1")).toBeInTheDocument();
  });

  // ── Moving sessions ───────────────────────────────────────────────────

  it("moves a session from the row's move menu", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    const row = rowFor("session-1");
    await user.click(
      row.querySelector('[aria-label="Move session to folder"]') as HTMLElement,
    );

    const menu = within(await screen.findByRole("menu"));
    await user.click(menu.getByText("Personal"));

    expect(mockUpdateSession).toHaveBeenCalledWith("session-1", {
      folder_id: "folder-home",
    });
  });

  it("disables the move target the session is already in", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    await user.click(
      rowFor("session-w").querySelector(
        '[aria-label="Move session to folder"]',
      ) as HTMLElement,
    );

    const menu = within(await screen.findByRole("menu"));
    const workItem = menu.getByText("Work").closest("li");
    expect(workItem).toHaveClass("ant-dropdown-menu-item-disabled");
  });

  it("moves a session when it is dropped on a folder header", async () => {
    renderSidebar();
    await settle();

    const row = rowFor("session-1");
    const header = screen.getByText("Personal").closest("div") as HTMLElement;
    const dataTransfer = makeDataTransfer();

    await act(async () => {
      fireEvent.dragStart(row, { dataTransfer });
      fireEvent.dragOver(header, { dataTransfer });
      fireEvent.drop(header, { dataTransfer });
    });

    expect(mockUpdateSession).toHaveBeenCalledWith("session-1", {
      folder_id: "folder-home",
    });
  });

  it("ignores a drop on the folder the session already lives in", async () => {
    renderSidebar();
    await settle();

    const row = rowFor("session-w");
    const header = screen.getByText("Work").closest("div") as HTMLElement;
    const dataTransfer = makeDataTransfer();

    await act(async () => {
      fireEvent.dragStart(row, { dataTransfer });
      fireEvent.drop(header, { dataTransfer });
    });

    expect(mockUpdateSession).not.toHaveBeenCalled();
  });

  it("files a session into Unfiled when dropped there", async () => {
    renderSidebar();
    await settle();

    const row = rowFor("session-w");
    const header = screen.getByText("Unfiled").closest("div") as HTMLElement;
    const dataTransfer = makeDataTransfer();

    await act(async () => {
      fireEvent.dragStart(row, { dataTransfer });
      fireEvent.drop(header, { dataTransfer });
    });

    expect(mockUpdateSession).toHaveBeenCalledWith("session-w", {
      folder_id: null,
    });
  });

  // ── Folder CRUD ───────────────────────────────────────────────────────

  it("creates a folder from the New Chat menu", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    await user.click(
      document.querySelector(".ant-dropdown-trigger") as HTMLElement,
    );
    const menu = within(await screen.findByRole("menu"));
    await user.click(menu.getByText("New Folder"));

    const nameInput = await screen.findByPlaceholderText("Folder name");
    await user.type(nameInput, "Research");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(mockCreateFolder).toHaveBeenCalledWith({
      name: "Research",
      color: null,
    });
  });

  it("deletes a folder after confirmation, keeping its chats", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    const menu = await openFolderMenu(user, "Work");
    await user.click(menu.getByText("Delete folder"));
    await settle();

    // The confirmation dialog states that chats move to Unfiled.
    expect(
      await screen.findByText(/will move to Unfiled/),
    ).toBeInTheDocument();

    // Scope to the confirmation dialog itself (rows also have delete buttons).
    const dialogs = document.querySelectorAll(".ant-modal-confirm");
    const dialog = dialogs[dialogs.length - 1] as HTMLElement;
    await user.click(
      within(dialog).getByRole("button", { name: "Delete" }),
    );

    expect(mockDeleteFolder).toHaveBeenCalledWith("folder-work");
  });

  it("sends folder_id when the folder changes in the edit dialog", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    const row = rowFor("session-1");
    await user.click(
      row.querySelector(".anticon-edit")!.closest("button") as HTMLElement,
    );
    await screen.findByPlaceholderText("Chat title");

    // Open the folder Select and choose "Personal".
    await user.click(
      document.querySelector(".ant-select-selector") as HTMLElement,
    );
    const options = Array.from(
      document.querySelectorAll(".ant-select-item-option"),
    );
    const personal = options.find((o) => o.textContent === "Personal");
    expect(personal).toBeTruthy();
    await user.click(personal as HTMLElement);

    await user.click(screen.getByRole("button", { name: /^ok$/i }));

    expect(mockUpdateSession).toHaveBeenCalledWith("session-1", {
      folder_id: "folder-home",
    });
  });

  it("shows the folder dialog with colours when editing a folder", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await settle();

    const menu = await openFolderMenu(user, "Work");
    await user.click(menu.getByText("Rename"));

    expect(await screen.findByText("Edit Folder")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Folder name")).toHaveValue("Work");
  });
});
