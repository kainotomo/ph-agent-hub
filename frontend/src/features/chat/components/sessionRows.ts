// =============================================================================
// PH Agent Hub — Sidebar row model (Issue #526)
// =============================================================================
// Pure helpers that flatten folders + sessions into the single flat list
// Virtuoso renders.  Deliberately free of React and of any API call so the
// grouping, collapse and ordering rules can be unit-tested directly.
// =============================================================================

import type { FolderData, SessionData } from "../services/chat";

/** Collapse-state key for the always-present "Unfiled" group. */
export const UNFILED_ID = "__unfiled__";

export type SidebarRow =
  | {
      kind: "folder";
      id: string;
      name: string;
      color: string | null;
      count: number;
      collapsed: boolean;
      isUnfiled: boolean;
    }
  | { kind: "session"; session: SessionData; folderId: string | null }
  | { kind: "placeholder"; folderId: string | null };

export interface MoveTarget {
  /** Folder id, or null for "Unfiled". */
  id: string | null;
  name: string;
}

/**
 * Order sessions the way the sidebar has always done: pinned first, then by
 * most recently updated.
 */
export function compareSessions(a: SessionData, b: SessionData): number {
  if (a.is_pinned && !b.is_pinned) return -1;
  if (!a.is_pinned && b.is_pinned) return 1;
  return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
}

/** Folder a session claims to belong to, normalised to null for "Unfiled". */
export function folderIdOfSession(session: SessionData): string | null {
  return session.folder_id ?? null;
}

/**
 * Collapse-state key for a group.  `null` (Unfiled) maps to {@link UNFILED_ID}
 * because localStorage keys must be strings.
 */
export function groupKeyForFolderId(folderId: string | null): string {
  return folderId ?? UNFILED_ID;
}

/**
 * The group a session is actually rendered under.
 *
 * A session pointing at a folder we do not know about (deleted a moment ago,
 * or a folder beyond the session list's 2000-row cap) falls back to Unfiled
 * rather than vanishing from the sidebar.
 */
export function effectiveFolderId(
  session: SessionData,
  knownFolderIds: Set<string>,
): string | null {
  const claimed = folderIdOfSession(session);
  if (claimed === null) return null;
  return knownFolderIds.has(claimed) ? claimed : null;
}

/**
 * Flatten folders and sessions into the rows the sidebar renders.
 *
 * Folders appear in the order given (the API returns them by `sort_order`,
 * then name), followed by "Unfiled" which is always last and always present
 * so it remains a valid drop target.  A collapsed folder contributes only its
 * header; an expanded but empty group contributes a placeholder row.
 */
export function buildSidebarRows(
  sessions: SessionData[],
  folders: FolderData[],
  collapsed: Set<string>,
): SidebarRow[] {
  const rows: SidebarRow[] = [];
  const ordered = [...sessions].sort(compareSessions);
  const knownFolderIds = new Set(folders.map((folder) => folder.id));

  const pushGroup = (
    id: string,
    name: string,
    color: string | null,
    isUnfiled: boolean,
  ): void => {
    const members = ordered.filter((session) => {
      const groupId = effectiveFolderId(session, knownFolderIds);
      return isUnfiled ? groupId === null : groupId === id;
    });
    const isCollapsed = collapsed.has(id);

    rows.push({
      kind: "folder",
      id,
      name,
      color,
      count: members.length,
      collapsed: isCollapsed,
      isUnfiled,
    });

    if (isCollapsed) return;

    if (members.length === 0) {
      rows.push({ kind: "placeholder", folderId: isUnfiled ? null : id });
      return;
    }

    for (const session of members) {
      rows.push({
        kind: "session",
        session,
        folderId: isUnfiled ? null : id,
      });
    }
  };

  for (const folder of folders) {
    pushGroup(folder.id, folder.name, folder.color, false);
  }
  pushGroup(UNFILED_ID, "Unfiled", null, true);

  return rows;
}

/**
 * Index of the row rendering a session, or -1 when it is not currently
 * rendered (unknown session, or hidden inside a collapsed folder).
 */
export function findSessionRowIndex(
  rows: SidebarRow[],
  sessionId: string,
): number {
  return rows.findIndex(
    (row) => row.kind === "session" && row.session.id === sessionId,
  );
}

/** Options for the "Move to folder" menu: every folder, then Unfiled. */
export function moveTargets(folders: FolderData[]): MoveTarget[] {
  return [
    ...folders.map((folder) => ({ id: folder.id, name: folder.name })),
    { id: null, name: "Unfiled" },
  ];
}

/** Stable Virtuoso item keys. */
export function rowKey(_index: number, row: SidebarRow): string {
  if (row.kind === "folder") return `f:${row.id}`;
  if (row.kind === "session") return `s:${row.session.id}`;
  return `p:${row.folderId ?? UNFILED_ID}`;
}

/** Session id carried by an HTML5 drag, extracted defensively. */
export function readDraggedSessionId(
  dataTransfer: DataTransfer | null,
): string | null {
  const value = dataTransfer?.getData("text/plain");
  return value ? value : null;
}
