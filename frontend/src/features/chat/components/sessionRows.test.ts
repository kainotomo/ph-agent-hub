// =============================================================================
// sessionRows — Unit Tests (Issue #526)
// =============================================================================
// Covers the pure grouping/ordering rules behind the folded sidebar:
// folder order, Unfiled fallback, collapse behaviour, counts, row lookup.
// =============================================================================

import { describe, it, expect } from "vitest";
import type { FolderData, SessionData } from "../services/chat";
import {
  UNFILED_ID,
  buildSidebarRows,
  compareSessions,
  effectiveFolderId,
  findSessionRowIndex,
  groupKeyForFolderId,
  moveTargets,
  readDraggedSessionId,
  rowKey,
  type SidebarRow,
} from "./sessionRows";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSession(over: Partial<SessionData> = {}): SessionData {
  return {
    id: "s1",
    tenant_id: "t1",
    user_id: "u1",
    title: "Chat",
    is_temporary: false,
    is_pinned: false,
    selected_skill_id: null,
    selected_model_id: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...over,
  };
}

function makeFolder(over: Partial<FolderData> = {}): FolderData {
  return {
    id: "f1",
    name: "Work",
    color: null,
    sort_order: 0,
    ...over,
  };
}

const NO_COLLAPSE = new Set<string>();

function sessionIds(rows: SidebarRow[]): string[] {
  return rows
    .filter((r): r is Extract<SidebarRow, { kind: "session" }> => r.kind === "session")
    .map((r) => r.session.id);
}

function folderHeaders(rows: SidebarRow[]) {
  return rows.filter(
    (r): r is Extract<SidebarRow, { kind: "folder" }> => r.kind === "folder",
  );
}

// ---------------------------------------------------------------------------
// Ordering
// ---------------------------------------------------------------------------

describe("compareSessions", () => {
  it("puts pinned sessions before unpinned ones", () => {
    const pinned = makeSession({ id: "p", is_pinned: true, updated_at: "2026-01-01T00:00:00Z" });
    const normal = makeSession({ id: "n", is_pinned: false, updated_at: "2026-09-01T00:00:00Z" });
    expect(compareSessions(pinned, normal)).toBeLessThan(0);
    expect(compareSessions(normal, pinned)).toBeGreaterThan(0);
  });

  it("orders unpinned sessions by updated_at descending", () => {
    const older = makeSession({ id: "old", updated_at: "2026-01-01T00:00:00Z" });
    const newer = makeSession({ id: "new", updated_at: "2026-09-01T00:00:00Z" });
    expect(compareSessions(newer, older)).toBeLessThan(0);
  });
});

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

describe("buildSidebarRows", () => {
  it("groups sessions under their folder and lists Unfiled last", () => {
    const folders = [makeFolder({ id: "f1", name: "Work" })];
    const sessions = [
      makeSession({ id: "a", folder_id: "f1" }),
      makeSession({ id: "b", folder_id: null }),
    ];

    const rows = buildSidebarRows(sessions, folders, NO_COLLAPSE);

    expect(folderHeaders(rows).map((f) => f.name)).toEqual(["Work", "Unfiled"]);
    expect(sessionIds(rows)).toEqual(["a", "b"]);
  });

  it("always renders the Unfiled header, even when empty", () => {
    const rows = buildSidebarRows([], [], NO_COLLAPSE);

    const headers = folderHeaders(rows);
    expect(headers).toHaveLength(1);
    expect(headers[0].isUnfiled).toBe(true);
    expect(headers[0].id).toBe(UNFILED_ID);
    expect(rows.filter((r) => r.kind === "placeholder")).toHaveLength(1);
  });

  it("counts only the sessions belonging to each group", () => {
    const folders = [makeFolder({ id: "f1" }), makeFolder({ id: "f2" })];
    const sessions = [
      makeSession({ id: "a", folder_id: "f1" }),
      makeSession({ id: "b", folder_id: "f1" }),
      makeSession({ id: "c", folder_id: null }),
    ];

    const rows = buildSidebarRows(sessions, folders, NO_COLLAPSE);
    const counts = Object.fromEntries(folderHeaders(rows).map((f) => [f.id, f.count]));

    expect(counts).toEqual({ f1: 2, f2: 0, [UNFILED_ID]: 1 });
  });

  it("keeps folder order as given (API sort_order order)", () => {
    const folders = [
      makeFolder({ id: "f2", name: "Beta", sort_order: 1 }),
      makeFolder({ id: "f1", name: "Alpha", sort_order: 0 }),
    ];
    const rows = buildSidebarRows([], folders, NO_COLLAPSE);
    expect(folderHeaders(rows).map((f) => f.name)).toEqual(["Beta", "Alpha", "Unfiled"]);
  });

  it("sorts pinned sessions first inside their group", () => {
    const folders = [makeFolder({ id: "f1" })];
    const sessions = [
      makeSession({ id: "recent", folder_id: "f1", updated_at: "2026-09-10T00:00:00Z" }),
      makeSession({
        id: "pinned",
        folder_id: "f1",
        is_pinned: true,
        updated_at: "2026-01-01T00:00:00Z",
      }),
    ];

    const rows = buildSidebarRows(sessions, folders, NO_COLLAPSE);
    expect(sessionIds(rows)).toEqual(["pinned", "recent"]);
  });
});

// ---------------------------------------------------------------------------
// Collapse
// ---------------------------------------------------------------------------

describe("buildSidebarRows collapse behaviour", () => {
  it("emits only the header for a collapsed folder", () => {
    const folders = [makeFolder({ id: "f1", name: "Work" })];
    const sessions = [makeSession({ id: "a", folder_id: "f1" })];

    const rows = buildSidebarRows(sessions, folders, new Set(["f1"]));

    expect(sessionIds(rows)).toEqual([]);
    expect(folderHeaders(rows)[0].collapsed).toBe(true);
    // A collapsed folder must not also emit the empty-group placeholder for
    // itself (Unfiled is expanded here, so its own placeholder is expected).
    const collapsedPlaceholders = rows.filter(
      (r) => r.kind === "placeholder" && r.folderId === "f1",
    );
    expect(collapsedPlaceholders).toHaveLength(0);
  });

  it("collapses Unfiled independently", () => {
    const sessions = [
      makeSession({ id: "a", folder_id: null }),
      makeSession({ id: "b", folder_id: "f1" }),
    ];
    const folders = [makeFolder({ id: "f1" })];
    const rows = buildSidebarRows(sessions, folders, new Set([UNFILED_ID]));

    expect(sessionIds(rows)).toEqual(["b"]);
  });

  it("emits a placeholder for an expanded but empty folder", () => {
    const folders = [makeFolder({ id: "f1", name: "Empty" })];
    const rows = buildSidebarRows([], folders, NO_COLLAPSE);

    const placeholders = rows.filter((r) => r.kind === "placeholder");
    expect(placeholders).toHaveLength(2); // Empty folder + Unfiled
  });
});

// ---------------------------------------------------------------------------
// Defensive fallbacks
// ---------------------------------------------------------------------------

describe("effectiveFolderId", () => {
  it("returns the folder id when the folder is known", () => {
    const s = makeSession({ folder_id: "f1" });
    expect(effectiveFolderId(s, new Set(["f1"]))).toBe("f1");
  });

  it("falls back to Unfiled when the folder is unknown", () => {
    const s = makeSession({ folder_id: "ghost" });
    expect(effectiveFolderId(s, new Set(["f1"]))).toBeNull();
  });

  it("treats undefined folder_id as Unfiled", () => {
    expect(effectiveFolderId(makeSession({}), new Set())).toBeNull();
    expect(effectiveFolderId(makeSession({ folder_id: null }), new Set())).toBeNull();
  });
});

describe("buildSidebarRows with a stale folder_id", () => {
  it("shows the session under Unfiled instead of hiding it", () => {
    const sessions = [makeSession({ id: "orphan", folder_id: "deleted-folder" })];
    const rows = buildSidebarRows(sessions, [], NO_COLLAPSE);

    expect(sessionIds(rows)).toEqual(["orphan"]);
    const sessionRow = rows.find((r) => r.kind === "session");
    expect(sessionRow && sessionRow.kind === "session" && sessionRow.folderId).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Row lookup / keys / helpers
// ---------------------------------------------------------------------------

describe("findSessionRowIndex", () => {
  it("finds the flattened index of a rendered session", () => {
    const folders = [makeFolder({ id: "f1" })];
    const sessions = [
      makeSession({ id: "a", folder_id: "f1" }),
      makeSession({ id: "b", folder_id: null }),
    ];
    const rows = buildSidebarRows(sessions, folders, NO_COLLAPSE);

    // rows: [Work header, session a, Unfiled header, session b]
    expect(findSessionRowIndex(rows, "a")).toBe(1);
    expect(findSessionRowIndex(rows, "b")).toBe(3);
  });

  it("returns -1 when the session is hidden by a collapsed folder", () => {
    const folders = [makeFolder({ id: "f1" })];
    const sessions = [makeSession({ id: "a", folder_id: "f1" })];
    const rows = buildSidebarRows(sessions, folders, new Set(["f1"]));

    expect(findSessionRowIndex(rows, "a")).toBe(-1);
  });

  it("returns -1 for an unknown session id", () => {
    expect(findSessionRowIndex(buildSidebarRows([], [], NO_COLLAPSE), "nope")).toBe(-1);
  });
});

describe("rowKey", () => {
  it("produces a distinct stable key per row", () => {
    const folders = [makeFolder({ id: "f1" })];
    const rows = buildSidebarRows(
      [makeSession({ id: "a", folder_id: "f1" })],
      folders,
      NO_COLLAPSE,
    );
    const keys = rows.map((row, i) => rowKey(i, row));

    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual(["f:f1", "s:a", `f:${UNFILED_ID}`, `p:${UNFILED_ID}`]);
    // Stable across calls (Virtuoso relies on this for scroll anchoring).
    expect(rows.map((row, i) => rowKey(i, row))).toEqual(keys);
  });
});

describe("groupKeyForFolderId", () => {
  it("maps null to the Unfiled sentinel", () => {
    expect(groupKeyForFolderId(null)).toBe(UNFILED_ID);
    expect(groupKeyForFolderId("f1")).toBe("f1");
  });
});

describe("moveTargets", () => {
  it("lists every folder then Unfiled", () => {
    const targets = moveTargets([
      makeFolder({ id: "f1", name: "Work" }),
      makeFolder({ id: "f2", name: "Personal" }),
    ]);
    expect(targets).toEqual([
      { id: "f1", name: "Work" },
      { id: "f2", name: "Personal" },
      { id: null, name: "Unfiled" },
    ]);
  });
});

describe("readDraggedSessionId", () => {
  it("returns the dragged session id", () => {
    const dt = { getData: () => "session-9" } as unknown as DataTransfer;
    expect(readDraggedSessionId(dt)).toBe("session-9");
  });

  it("returns null for an empty or missing payload", () => {
    const empty = { getData: () => "" } as unknown as DataTransfer;
    expect(readDraggedSessionId(empty)).toBeNull();
    expect(readDraggedSessionId(null)).toBeNull();
  });
});
