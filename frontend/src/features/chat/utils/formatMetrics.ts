/**
 * DSH formatting helpers for chat metrics.
 * Pure functions — no React, no imports.
 */

const EM_DASH = "\u2014";

export function formatTokensCompact(n: number | null | undefined): string {
  if (n == null) return EM_DASH;
  if (n >= 1e6) {
    const v = (n / 1e6).toFixed(1);
    return v.endsWith(".0") ? v.slice(0, -2) + "M" : v + "M";
  }
  if (n >= 1e3) {
    const v = (n / 1e3).toFixed(1);
    return v.endsWith(".0") ? v.slice(0, -2) + "K" : v + "K";
  }
  return String(n);
}

export function formatTokensExact(n: number | null | undefined): string {
  if (n == null) return EM_DASH;
  return n.toLocaleString("en-US");
}

export function formatTokensApprox(n: number | null | undefined): string {
  if (n == null) return EM_DASH;
  return "~" + formatTokensCompact(n);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return EM_DASH;
  if (ms < 60_000) {
    return (ms / 1000).toFixed(1) + "s";
  }
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}m${String(s).padStart(2, "0")}s`;
}

export function formatTps(n: number | null | undefined): string {
  if (n == null) return EM_DASH;
  return Math.round(n) + " tok/s";
}

export function formatPercent(n: number | null | undefined): string {
  if (n == null) return EM_DASH;
  return Math.round(n) + "%";
}
