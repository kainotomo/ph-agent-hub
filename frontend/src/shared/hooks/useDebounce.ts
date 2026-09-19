// =============================================================================
// PH Agent Hub — useDebounce Hook
// =============================================================================
// Delays updating a value until after a specified delay.  Used to avoid
// firing API calls on every keystroke in search inputs.
// =============================================================================

import { useState, useEffect } from "react";

export function useDebounce<T>(value: T, delay: number = 300): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debouncedValue;
}
