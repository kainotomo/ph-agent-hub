// =============================================================================
// MemoryManager — Unit Tests
// =============================================================================
// Verifies that the Add-entry mutation never sends session_id (global-only),
// that scope tags render correctly, and that pagination calls listMemory
// with the right page parameter.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";

// ---------------------------------------------------------------------------
// Mock the chat service so only MemoryManager orchestration logic runs.
// ---------------------------------------------------------------------------

const mockListMemory = vi.fn();
const mockCreateMemory = vi.fn();
const mockDeleteMemory = vi.fn();
const mockUpdateMemory = vi.fn();

vi.mock("../services/chat", () => ({
  listMemory: (...args: any[]) => mockListMemory(...args),
  createMemory: (...args: any[]) => mockCreateMemory(...args),
  deleteMemory: (...args: any[]) => mockDeleteMemory(...args),
  updateMemory: (...args: any[]) => mockUpdateMemory(...args),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const GLOBAL_ENTRY = {
  id: "mem-1",
  tenant_id: "tenant-1",
  user_id: "user-1",
  session_id: null,
  key: "user_preference",
  value: "dark mode enabled",
  source: "manual",
  created_at: "2025-01-01T00:00:00Z",
  updated_at: null,
};

const SESSION_ENTRY = {
  id: "mem-2",
  tenant_id: "tenant-1",
  user_id: "user-1",
  session_id: "session-abc",
  key: "last_query",
  value: "how to configure tools",
  source: "automatic",
  created_at: "2025-01-01T00:01:00Z",
  updated_at: "2025-01-01T00:02:00Z",
};

function makeEnvelope(entries: any[], total: number, page: number, page_size: number) {
  return {
    items: entries,
    total,
    page,
    page_size,
    total_pages: Math.ceil(total / page_size),
  };
}

// ---------------------------------------------------------------------------
// Render helper — mirrors ChatWindow.test.tsx style
// ---------------------------------------------------------------------------

import { MemoryManager } from "./MemoryManager";

function renderMemoryManager(props: { open?: boolean; sessionId?: string } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryManager
          open={props.open ?? true}
          onClose={() => {}}
          sessionId={props.sessionId}
        />
      </QueryClientProvider>,
    ),
    queryClient,
  };
}

async function settle() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 100));
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("MemoryManager — Add entry never sends session_id", () => {
  beforeEach(() => {
    mockListMemory.mockResolvedValue(
      makeEnvelope([GLOBAL_ENTRY], 1, 1, 20),
    );
    mockCreateMemory.mockResolvedValue(GLOBAL_ENTRY);
    mockDeleteMemory.mockResolvedValue(undefined);
    mockUpdateMemory.mockResolvedValue(GLOBAL_ENTRY);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("calls createMemory without a session_id property", async () => {
    renderMemoryManager();
    await settle();

    const user = userEvent.setup();
    const addButton = screen.getByRole("button", { name: /add entry/i });
    await user.click(addButton);

    // Fill the form
    const keyInput = screen.getByLabelText(/key/i);
    const valueInput = screen.getByLabelText(/value/i);
    await user.type(keyInput, "test_key");
    await user.type(valueInput, "test value");

    // Submit
    await act(async () => {
      await user.click(screen.getByRole("button", { name: "OK" }));
    });
    await settle();

    // Assert the call was made with an object that does NOT have session_id
    expect(mockCreateMemory).toHaveBeenCalledTimes(1);
    const callArg = mockCreateMemory.mock.calls[0][0];
    expect(callArg).toHaveProperty("key", "test_key");
    expect(callArg).toHaveProperty("value", "test value");
    // The key point: session_id must NOT be present at all
    expect("session_id" in callArg).toBe(false);
  });
});

describe("MemoryManager — scope tags", () => {
  beforeEach(() => {
    mockListMemory.mockResolvedValue(
      makeEnvelope([GLOBAL_ENTRY, SESSION_ENTRY], 2, 1, 20),
    );
    mockCreateMemory.mockResolvedValue(GLOBAL_ENTRY);
    mockDeleteMemory.mockResolvedValue(undefined);
    mockUpdateMemory.mockResolvedValue(GLOBAL_ENTRY);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders 'Global' for entries with null session_id", async () => {
    renderMemoryManager();
    await settle();

    expect(screen.getByText("Global")).toBeInTheDocument();
  });

  it("renders 'This session' for entries with a non-null session_id", async () => {
    renderMemoryManager();
    await settle();

    expect(screen.getByText("This session")).toBeInTheDocument();
  });
});

describe("MemoryManager — pagination calls listMemory", () => {
  beforeEach(() => {
    mockListMemory.mockReset();
    // Return 60 entries so multiple pages exist
    const allEntries = Array.from({ length: 60 }, (_, i) => ({
      ...GLOBAL_ENTRY,
      id: `mem-${i}`,
      key: `key_${i}`,
    }));
    mockListMemory.mockImplementation(({ page, page_size }: { page?: number; page_size?: number }) => {
      const start = ((page ?? 1) - 1) * (page_size ?? 20);
      const pageItems = allEntries.slice(start, start + (page_size ?? 20));
      return Promise.resolve(
        makeEnvelope(pageItems, 60, page ?? 1, page_size ?? 20),
      );
    });
    mockCreateMemory.mockResolvedValue(GLOBAL_ENTRY);
    mockDeleteMemory.mockResolvedValue(undefined);
    mockUpdateMemory.mockResolvedValue(GLOBAL_ENTRY);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("calls listMemory with page 1 on initial mount", async () => {
    renderMemoryManager();
    await settle();

    expect(mockListMemory).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1 }),
    );
  });

  it("calls listMemory with the new page number when pagination changes", async () => {
    renderMemoryManager();
    await settle();

    // Clear previous calls from the initial mount
    mockListMemory.mockClear();

    const user = userEvent.setup();
    // Navigate to page 2 — Ant Design renders pagination items as <li> elements
    // with a `title` attribute matching the page number.
    await act(async () => {
      await user.click(screen.getByTitle("2"));
    });
    await settle();

    expect(mockListMemory).toHaveBeenCalledWith(
      expect.objectContaining({ page: 2 }),
    );
  });

  // Regression: the list is paginated by the server, so the component must not
  // slice the already-paginated page again — doing so rendered page 2 empty.
  it("renders the rows belonging to the selected page", async () => {
    renderMemoryManager();
    await settle();

    expect(screen.getByText("key_0")).toBeInTheDocument();

    const user = userEvent.setup();
    await act(async () => {
      await user.click(screen.getByTitle("2"));
    });
    await settle();

    expect(screen.getByText("key_20")).toBeInTheDocument();
    expect(screen.queryByText("key_0")).not.toBeInTheDocument();
  });
});
