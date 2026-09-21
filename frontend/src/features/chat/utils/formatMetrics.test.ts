import { describe, it, expect } from "vitest";
import {
  formatTokensCompact,
  formatTokensExact,
  formatTokensApprox,
  formatDuration,
  formatTps,
  formatPercent,
} from "./formatMetrics";

describe("formatTokensCompact", () => {
  it("1798557 -> 1.8M", () => expect(formatTokensCompact(1798557)).toBe("1.8M"));
  it("1000000 -> 1M", () => expect(formatTokensCompact(1000000)).toBe("1M"));
  it("85400 -> 85.4K", () => expect(formatTokensCompact(85400)).toBe("85.4K"));
  it("7300 -> 7.3K", () => expect(formatTokensCompact(7300)).toBe("7.3K"));
  it("2300 -> 2.3K", () => expect(formatTokensCompact(2300)).toBe("2.3K"));
  it("950 -> 950", () => expect(formatTokensCompact(950)).toBe("950"));
  it("null -> —", () => expect(formatTokensCompact(null)).toBe("\u2014"));
  it("undefined -> —", () => expect(formatTokensCompact(undefined)).toBe("\u2014"));
});

describe("formatTokensExact", () => {
  it("1798557 -> 1,798,557", () => expect(formatTokensExact(1798557)).toBe("1,798,557"));
  it("42837 -> 42,837", () => expect(formatTokensExact(42837)).toBe("42,837"));
  it("null -> —", () => expect(formatTokensExact(null)).toBe("\u2014"));
});

describe("formatTokensApprox", () => {
  it("85400 -> ~85.4K", () => expect(formatTokensApprox(85400)).toBe("~85.4K"));
  it("null -> —", () => expect(formatTokensApprox(null)).toBe("\u2014"));
});

describe("formatDuration", () => {
  it("198000 -> 3m18s", () => expect(formatDuration(198000)).toBe("3m18s"));
  it("275000 -> 4m35s", () => expect(formatDuration(275000)).toBe("4m35s"));
  it("1200 -> 1.2s", () => expect(formatDuration(1200)).toBe("1.2s"));
  it("450 -> 0.5s", () => expect(formatDuration(450)).toBe("0.5s"));
  it("65000 -> 1m05s", () => expect(formatDuration(65000)).toBe("1m05s"));
  it("null -> —", () => expect(formatDuration(null)).toBe("\u2014"));
});

describe("formatTps", () => {
  it("243.4 -> 243 tok/s", () => expect(formatTps(243.4)).toBe("243 tok/s"));
  it("null -> —", () => expect(formatTps(null)).toBe("\u2014"));
});

describe("formatPercent", () => {
  it("97.6 -> 98%", () => expect(formatPercent(97.6)).toBe("98%"));
  it("9.0 -> 9%", () => expect(formatPercent(9.0)).toBe("9%"));
  it("null -> —", () => expect(formatPercent(null)).toBe("\u2014"));
});
