// =============================================================================
// services/api — Issue #610: single-flight token refresh
// =============================================================================
// Two tabs/windows share one refresh token.  Concurrent 401s must share one
// in-flight refresh request (the token is single-use, so a second refresh
// would invalidate the first).  The in-flight promise is cleared only after
// the refresh settles, so callers arriving while it runs join it.
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mockFetch = vi.hoisted(() => vi.fn());
vi.stubGlobal("fetch", mockFetch);

describe("refreshAccessToken (Issue #610)", () => {
  beforeEach(() => {
    vi.resetModules();
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shares one refresh request across concurrent callers", async () => {
    let release!: (value: unknown) => void;
    mockFetch.mockImplementationOnce(
      () => new Promise((resolve) => (release = resolve)),
    );

    const api = await import("./api");
    const first = api.refreshAccessToken();
    const second = api.refreshAccessToken();

    // Both callers joined the same in-flight refresh.
    expect(mockFetch).toHaveBeenCalledTimes(1);

    release({
      ok: true,
      json: async () => ({ access_token: "fresh-token" }),
    });

    await expect(first).resolves.toBe("fresh-token");
    await expect(second).resolves.toBe("fresh-token");
    expect(api.getToken()).toBe("fresh-token");
  });

  it("allows a new refresh after the in-flight one settles", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ access_token: "first-token" }),
    });

    const api = await import("./api");
    await api.refreshAccessToken();
    await api.refreshAccessToken();

    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("propagates refresh failure to every caller and clears the flight", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });

    const api = await import("./api");
    const first = api.refreshAccessToken();
    const second = api.refreshAccessToken();

    await expect(first).resolves.toBeNull();
    await expect(second).resolves.toBeNull();
    expect(mockFetch).toHaveBeenCalledTimes(1);

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ access_token: "later-token" }),
    });
    await expect(api.refreshAccessToken()).resolves.toBe("later-token");
  });
});
