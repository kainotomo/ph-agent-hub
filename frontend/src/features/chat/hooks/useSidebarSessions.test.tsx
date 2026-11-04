// =============================================================================
// useSidebarSessions — Unit Tests
// =============================================================================
// Covers the grouped first page, on-demand "show more" pages, deduplication,
// failure/retry handling, and refetch-on-invalidation.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { mockGetSummary, mockGetPage } = vi.hoisted(() => ({
  mockGetSummary: vi.fn(),
  mockGetPage: vi.fn(),
}));

vi.mock("../services/chat", () => ({
  SIDEBAR_PAGE_SIZE: 5,
  getSidebarSummary: mockGetSummary,
  getSidebarGroupPage: mockGetPage,
}));

import { useSidebarSessions } from "./useSidebarSessions";
import { UNFILED_ID } from "../components/sessionRows";
import type {
  SessionData,
  SidebarGroupPageData,
  SidebarSummaryData,
} from "../services/chat";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSession(id: string, folderId: string | null = null): SessionData {
  return {
    id,
    tenant_id: "t1",
    user_id: "u1",
    title: id,
    is_temporary: false,
    is_pinned: false,
    selected_skill_id: null,
    selected_model_id: null,
    folder_id: folderId,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const UNFILED_SESSIONS = ["u1", "u2", "u3", "u4", "u5"].map((id) => makeSession(id));
const FOLDER_SESSIONS = ["f1", "f2"].map((id) => makeSession(id, "folder-1"));

function makeSummary(
  overrides: Partial<SidebarSummaryData> = {},
): SidebarSummaryData {
  return {
    per_group: 5,
    groups: [
      {
        // The API calls it "unfiled"; the hook maps it to UNFILED_ID.
        key: "unfiled",
        folder_id: null,
        total: 8,
        sessions: UNFILED_SESSIONS,
      },
      {
        key: "folder-1",
        folder_id: "folder-1",
        total: 2,
        sessions: FOLDER_SESSIONS,
      },
    ],
    ...overrides,
  };
}

function makePage(sessions: SessionData[]): SidebarGroupPageData {
  return {
    key: "unfiled",
    folder_id: null,
    total: 8,
    offset: 5,
    limit: 5,
    sessions,
  };
}

function renderSidebarHook() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return {
    queryClient,
    ...renderHook(() => useSidebarSessions(), { wrapper }),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useSidebarSessions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetSummary.mockResolvedValue(makeSummary());
    mockGetPage.mockResolvedValue(makePage(["u6", "u7", "u8"].map((id) => makeSession(id))));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the grouped first page without requesting extra pages", async () => {
    const { result } = renderSidebarHook();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(mockGetSummary).toHaveBeenCalledTimes(1);
    expect(mockGetPage).not.toHaveBeenCalled();
    expect(result.current.sessions.map((s) => s.id)).toEqual([
      "u1", "u2", "u3", "u4", "u5", "f1", "f2",
    ]);
    expect(result.current.totals).toEqual({ [UNFILED_ID]: 8, "folder-1": 2 });
  });

  it("requests the next page at offset 5 and appends its sessions", async () => {
    const { result } = renderSidebarHook();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.showMore(UNFILED_ID));

    await waitFor(() => expect(result.current.sessions).toHaveLength(10));
    expect(mockGetPage).toHaveBeenCalledWith(UNFILED_ID, 5, 5);
    expect(mockGetPage).toHaveBeenCalledTimes(1);
    expect(result.current.moreState[UNFILED_ID]?.loading).toBe(false);
    expect(result.current.moreState[UNFILED_ID]?.failed).toBe(false);
  });

  it("deduplicates sessions that appear in both the summary and a page", async () => {
    mockGetPage.mockResolvedValue(
      makePage([makeSession("u4"), makeSession("u9")]),
    );

    const { result } = renderSidebarHook();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.showMore(UNFILED_ID));
    await waitFor(() => expect(result.current.sessions).toHaveLength(8));

    const ids = result.current.sessions.map((s) => s.id);
    expect(ids.filter((id) => id === "u4")).toHaveLength(1);
    expect(ids).toContain("u9");
  });

  it("marks a failed page and retries it in place", async () => {
    mockGetPage.mockRejectedValueOnce(new Error("boom"));

    const { result } = renderSidebarHook();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.showMore(UNFILED_ID));
    await waitFor(() =>
      expect(result.current.moreState[UNFILED_ID]?.failed).toBe(true),
    );
    expect(result.current.sessions).toHaveLength(7);

    act(() => result.current.retryMore(UNFILED_ID));
    await waitFor(() => expect(result.current.sessions).toHaveLength(10));
    expect(result.current.moreState[UNFILED_ID]?.failed).toBe(false);
    // Retry must not create a second, differently-offset request.
    expect(mockGetPage).toHaveBeenCalledTimes(2);
  });

  it("refetches the summary and expanded pages when sessions are invalidated", async () => {
    const { result, queryClient } = renderSidebarHook();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.showMore(UNFILED_ID));
    await waitFor(() => expect(result.current.sessions).toHaveLength(10));
    expect(mockGetSummary).toHaveBeenCalledTimes(1);
    expect(mockGetPage).toHaveBeenCalledTimes(1);

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    });

    expect(mockGetSummary).toHaveBeenCalledTimes(2);
    expect(mockGetPage).toHaveBeenCalledTimes(2);
    expect(result.current.sessions).toHaveLength(10);
  });

  it("exposes totals under the sidebar's internal group key", async () => {
    const { result } = renderSidebarHook();
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.totals[UNFILED_ID]).toBe(8);
    expect(result.current.totals["folder-1"]).toBe(2);
  });
});
