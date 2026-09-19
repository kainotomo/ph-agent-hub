// =============================================================================
// useSessionSearch — Unit Tests
// =============================================================================
// Covers: whitespace/#-only queries never call the API, searchSessions called
// with trimmed query + scope, scope change refetches, #work → listSessionsByTag
// with effectiveScope === "tag", clear() empties results, service rejection
// surfaces error.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useSessionSearch } from "./useSessionSearch";
import type { SearchScope } from "../services/chat";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const { mockSearchSessions, mockListSessionsByTag } = vi.hoisted(() => ({
  mockSearchSessions: vi.fn(),
  mockListSessionsByTag: vi.fn(),
}));

vi.mock("../services/chat", () => ({
  searchSessions: (...args: unknown[]) => mockSearchSessions(...args),
  listSessionsByTag: (...args: unknown[]) => mockListSessionsByTag(...args),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderSearch(options?: {
  debounceMs?: number;
  defaultScope?: SearchScope;
}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderHook(() => useSessionSearch(options), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useSessionSearch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSearchSessions.mockResolvedValue([]);
    mockListSessionsByTag.mockResolvedValue([]);
  });

  it("whitespace-only query never calls the API", async () => {
    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("   ");
    });

    expect(mockSearchSessions).not.toHaveBeenCalled();
    expect(mockListSessionsByTag).not.toHaveBeenCalled();
    expect(result.current.active).toBe(false);
  });

  it("'-only query never calls the API", async () => {
    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("#");
    });

    expect(mockSearchSessions).not.toHaveBeenCalled();
    expect(mockListSessionsByTag).not.toHaveBeenCalled();
    expect(result.current.active).toBe(false);
  });

  it("calls searchSessions with trimmed query and scope", async () => {
    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("  hello world  ");
    });

    await waitFor(() => {
      expect(mockSearchSessions).toHaveBeenCalledWith("hello world", "all");
    });
    expect(result.current.active).toBe(true);
  });

  it("scope change refetches with the new scope", async () => {
    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("test");
    });

    await waitFor(() => {
      expect(mockSearchSessions).toHaveBeenCalledWith("test", "all");
    });

    await act(async () => {
      result.current.setScope("title");
    });

    await waitFor(() => {
      expect(mockSearchSessions).toHaveBeenCalledWith("test", "title");
    });
  });

  it("#work → listSessionsByTag with effectiveScope 'tag'", async () => {
    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("#work");
    });

    await waitFor(() => {
      expect(mockListSessionsByTag).toHaveBeenCalledWith("work");
    });
    expect(result.current.tagMode).toBe(true);
    expect(result.current.effectiveScope).toBe("tag");
  });

  it("clear() empties results and deactivates", async () => {
    mockSearchSessions.mockResolvedValue([{ id: "s1" }]);
    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("test");
    });

    await waitFor(() => {
      expect(result.current.results).toHaveLength(1);
    });

    await act(async () => {
      result.current.clear();
    });

    // `active` follows the debounced value.
    await waitFor(() => {
      expect(result.current.active).toBe(false);
    });
    expect(result.current.results).toEqual([]);
  });

  it("service rejection surfaces error", async () => {
    mockSearchSessions.mockRejectedValue(new Error("Network error"));

    const { result } = renderSearch({ debounceMs: 0 });
    await act(async () => {
      result.current.setQuery("test");
    });

    await waitFor(() => {
      expect(result.current.error).toBeTruthy();
    });
    expect(result.current.results).toEqual([]);
  });
});
