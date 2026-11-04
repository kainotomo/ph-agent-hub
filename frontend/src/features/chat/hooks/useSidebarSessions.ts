// =============================================================================
// PH Agent Hub — useSidebarSessions
// =============================================================================
// Paged data source for the chat sidebar.  The first request returns a small
// first page for every folder group; "show more" requests further pages of a
// single group on demand.  All cache keys live under ["sessions", ...] so the
// existing session invalidations refetch the summary and every expanded page.
// =============================================================================

import { useCallback, useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  SIDEBAR_PAGE_SIZE,
  getSidebarGroupPage,
  getSidebarSummary,
  type SessionData,
  type SidebarGroupData,
} from "../services/chat";
import { UNFILED_ID } from "../components/sessionRows";

/** The API calls the synthetic group "unfiled"; the sidebar uses UNFILED_ID. */
const API_UNFILED_KEY = "unfiled";

export function groupKeyFromApiKey(key: string): string {
  return key === API_UNFILED_KEY ? UNFILED_ID : key;
}

export interface SidebarMoreState {
  /** A page for this group is currently in flight. */
  loading: boolean;
  /** The last requested page failed; clicking should retry it. */
  failed: boolean;
}

export interface SidebarSessionsState {
  /** Union of every loaded page, deduplicated by session id. */
  sessions: SessionData[];
  /** Server-side totals keyed by the sidebar's group key. */
  totals: Record<string, number>;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  /** Refetch the summary (still-active pages are refetched by invalidation). */
  refetch: () => void;
  moreState: Record<string, SidebarMoreState>;
  showMore: (groupKey: string) => void;
  retryMore: (groupKey: string) => void;
}

interface PageDescriptor {
  groupKey: string;
  pageIndex: number;
}

export function useSidebarSessions(
  perGroup: number = SIDEBAR_PAGE_SIZE,
): SidebarSessionsState {
  // Extra pages requested per group (page 1 comes from the summary).
  const [wantedPages, setWantedPages] = useState<Record<string, number>>({});

  const summaryQuery = useQuery({
    queryKey: ["sessions", "sidebar"],
    queryFn: () => getSidebarSummary(perGroup),
  });

  const groupsByKey = useMemo(() => {
    const map = new Map<string, SidebarGroupData>();
    for (const group of summaryQuery.data?.groups ?? []) {
      map.set(groupKeyFromApiKey(group.key), group);
    }
    return map;
  }, [summaryQuery.data]);

  const totals = useMemo(() => {
    const result: Record<string, number> = {};
    for (const [key, group] of groupsByKey) {
      result[key] = group.total;
    }
    return result;
  }, [groupsByKey]);

  // A requested page is only fetched while its group still exists in the
  // latest summary (a deleted folder must not keep an active page query).
  const pageDescriptors = useMemo(() => {
    const descriptors: PageDescriptor[] = [];
    for (const [groupKey, wanted] of Object.entries(wantedPages)) {
      if (!groupsByKey.has(groupKey)) continue;
      for (let pageIndex = 0; pageIndex < wanted; pageIndex += 1) {
        descriptors.push({ groupKey, pageIndex });
      }
    }
    return descriptors;
  }, [wantedPages, groupsByKey]);

  const pageQueries = useQueries({
    queries: pageDescriptors.map(({ groupKey, pageIndex }) => ({
      queryKey: ["sessions", "sidebar-page", groupKey, pageIndex],
      queryFn: () =>
        getSidebarGroupPage(
          groupKey,
          perGroup + pageIndex * perGroup,
          perGroup,
        ),
    })),
  });

  const sessions = useMemo(() => {
    // Summary pages first, then successful extra pages; first occurrence wins.
    const byId = new Map<string, SessionData>();
    for (const group of groupsByKey.values()) {
      for (const session of group.sessions) {
        byId.set(session.id, session);
      }
    }
    pageDescriptors.forEach((_descriptor, index) => {
      const page = pageQueries[index]?.data;
      if (!page) return;
      for (const session of page.sessions) {
        if (!byId.has(session.id)) byId.set(session.id, session);
      }
    });
    return [...byId.values()];
  }, [groupsByKey, pageDescriptors, pageQueries]);

  const moreState = useMemo(() => {
    const state: Record<string, SidebarMoreState> = {};
    pageDescriptors.forEach((descriptor, index) => {
      const query = pageQueries[index];
      if (!query) return;
      const previous = state[descriptor.groupKey] ?? {
        loading: false,
        failed: false,
      };
      state[descriptor.groupKey] = {
        loading: previous.loading || query.isFetching,
        failed: previous.failed || query.isError,
      };
    });
    return state;
  }, [pageDescriptors, pageQueries]);

  // The first page of this group that ended in an error (if any).  Both
  // "show more" and the retry affordance refetch that page in place, so a
  // failure never creates a gap in the group.
  const failedPageIndex = useCallback(
    (groupKey: string): number => {
      for (let index = 0; index < pageDescriptors.length; index += 1) {
        if (
          pageDescriptors[index].groupKey === groupKey &&
          pageQueries[index]?.isError
        ) {
          return index;
        }
      }
      return -1;
    },
    [pageDescriptors, pageQueries],
  );

  const retryMore = useCallback(
    (groupKey: string) => {
      const index = failedPageIndex(groupKey);
      if (index < 0) return;
      void pageQueries[index]?.refetch();
    },
    [failedPageIndex, pageQueries],
  );

  const showMore = useCallback(
    (groupKey: string) => {
      const failedIndex = failedPageIndex(groupKey);
      if (failedIndex >= 0) {
        void pageQueries[failedIndex].refetch();
        return;
      }
      setWantedPages((previous) => ({
        ...previous,
        [groupKey]: (previous[groupKey] ?? 0) + 1,
      }));
    },
    [failedPageIndex, pageQueries],
  );

  const refetch = useCallback(() => {
    void summaryQuery.refetch();
  }, [summaryQuery]);

  return {
    sessions,
    totals,
    isLoading: summaryQuery.isLoading,
    isError: summaryQuery.isError,
    error: (summaryQuery.error as Error | null) ?? null,
    refetch,
    moreState,
    showMore,
    retryMore,
  };
}
