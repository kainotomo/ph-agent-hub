// =============================================================================
// PH Agent Hub — useSessionSearch Hook
// =============================================================================
// Encapsulates search state + fetch so the sidebar stays thin and the logic
// is unit-testable.  Uses react-query with placeholderData to avoid empty
// flashes during live typing.
// =============================================================================

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "../../../shared/hooks/useDebounce";
import {
  searchSessions,
  listSessionsByTag,
  SearchScope,
} from "../services/chat";

export interface UseSessionSearchOptions {
  debounceMs?: number;
  defaultScope?: SearchScope;
}

export function useSessionSearch(options: UseSessionSearchOptions = {}) {
  const { debounceMs = 300, defaultScope = "all" } = options;

  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<SearchScope>(defaultScope);

  const debounced = useDebounce(query, debounceMs);
  const trimmed = debounced.trim();

  // Tag mode: query starts with "#" and has a non-empty tag name.
  // A bare "#" (empty tag name) is treated as inactive to avoid a flash of
  // "no results" while the user is still typing the tag name.
  const tagMode = trimmed.startsWith("#") && trimmed.length > 1;
  const tagName = tagMode ? trimmed.slice(1) : "";
  const effectiveScope: SearchScope = tagMode ? "tag" : scope;
  const active = tagMode || (trimmed.length > 0 && !trimmed.startsWith("#"));

  const {
    data: results,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery({
    queryKey: ["session-search", trimmed, effectiveScope],
    queryFn: tagMode
      ? () => listSessionsByTag(tagName)
      : () => searchSessions(trimmed, effectiveScope),
    enabled: active,
    placeholderData: (prev) => prev,
    staleTime: 30_000,
    retry: false,
  });

  const clear = () => setQuery("");

  return {
    query,
    setQuery,
    scope,
    setScope,
    effectiveScope,
    tagMode,
    active,
    results: active ? results ?? [] : [],
    isLoading,
    isFetching,
    error,
    refetch,
    clear,
  };
}
