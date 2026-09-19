// =============================================================================
// PH Agent Hub — SessionSidebar
// =============================================================================
// Ant Design Layout.Sider (Drawer on mobile); session list (pinned first);
// instant new chat; edit button per session (rename, temp toggle);
// links to MemoryManager, SessionSearch, logout.
// =============================================================================

import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  Alert,
  Layout,
  Button,
  Typography,
  Space,
  Spin,
  Tooltip,
  Drawer,
  Modal,
  Input,
  Dropdown,
  message,
  Tag,
  Popconfirm,
  Checkbox,
  Select,
} from "antd";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";
import type { MenuProps } from "antd";
import {
  PlusOutlined,
  SearchOutlined,
  DatabaseOutlined,
  LogoutOutlined,
  PushpinOutlined,
  PushpinFilled,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  DownOutlined,
  ThunderboltOutlined,
  MenuOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ReloadOutlined,
  SettingOutlined,
  UploadOutlined,
  FileTextOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  FolderAddOutlined,
  ClockCircleOutlined,
  CheckSquareOutlined,
  CloseOutlined,
} from "@ant-design/icons";
import { Logo } from "../../../shared/components/Logo";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../../providers/AuthProvider";
import { useStreamingContext } from "../../../providers/StreamingProvider";
import {
  listSessions,
  createSession,
  deleteSession,
  deleteSessions,
  moveSessions,
  updateSession,
  SessionData,
  FolderData,
  listFolders,
  createFolder,
  updateFolder,
  deleteFolder,
  addTagToSession,
  removeTagFromSession,
  exportSession,
  importSession,
  getStreamStatus,
} from "../services/chat";
import { ContextIndicator } from "./ContextIndicator";
import { MemoryManager } from "./MemoryManager";
import { SessionSearch } from "./SessionSearch";
import { SessionFolderHeader } from "./SessionFolderHeader";
import {
  UNFILED_ID,
  buildSidebarRows,
  findSessionRowIndex,
  groupKeyForFolderId,
  moveTargets,
  readDraggedSessionId,
  rowKey,
  type SidebarRow,
} from "./sessionRows";

const { Sider } = Layout;
const { Text } = Typography;

/** localStorage key for the collapsed-folder set (Issue #526). */
const FOLDER_COLLAPSE_KEY = "ph.sidebar.collapsedFolders";

/** Preset folder tints offered in the create/edit folder dialog. */
const FOLDER_COLORS = [
  "#1677ff",
  "#52c41a",
  "#faad14",
  "#eb2f96",
  "#722ed1",
  "#13c2c2",
  "#fa541c",
  "#8c8c8c",
];

// =============================================================================
// SessionListItem — individual session row (React.memo-wrapped for perf)
// =============================================================================

interface SessionListItemProps {
  item: SessionData;
  isActive: boolean;
  isMobile: boolean;
  collapsed: boolean;
  selectMode: boolean;
  isSelected: boolean;
  isStreaming: boolean;
  onNavigate: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onEdit: (session: SessionData) => void;
  onPin: (id: string, is_pinned: boolean) => void;
  onDelete: (id: string) => void;
  setSelectedIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  setMobileOpen: React.Dispatch<React.SetStateAction<boolean>>;
  /** Issue #526 — folders available as move targets. */
  folders: FolderData[];
  isDragging: boolean;
  onMove: (sessionId: string, folderId: string | null) => void;
  onNewFolder: (sessionId: string) => void;
  onDragStart: (event: React.DragEvent<HTMLDivElement>, id: string) => void;
  onDragEnd: () => void;
}

const SessionListItem = React.memo(function SessionListItem({
  item,
  isActive,
  isMobile,
  collapsed,
  selectMode,
  isSelected,
  isStreaming,
  onNavigate,
  onToggleSelect,
  onEdit,
  onPin,
  onDelete,
  setSelectedIds,
  setMobileOpen,
  folders,
  isDragging,
  onMove,
  onNewFolder,
  onDragStart,
  onDragEnd,
}: SessionListItemProps) {
  return (
    <div
      data-session-id={item.id}
      draggable={!selectMode}
      onDragStart={(e) => onDragStart(e, item.id)}
      onDragEnd={onDragEnd}
      onClick={() => {
        if (selectMode) {
          setSelectedIds((prev) => {
            const next = new Set(prev);
            if (next.has(item.id)) {
              next.delete(item.id);
            } else {
              next.add(item.id);
            }
            return next;
          });
          return;
        }
        onNavigate(isActive ? "/chat" : `/chat/${item.id}`);
        if (isMobile) setMobileOpen(false);
      }}
      style={{
        cursor: "pointer",
        padding: "8px 12px",
        opacity: isDragging ? 0.4 : 1,
        background:
          isActive && !selectMode ? "#e6f4ff" : "transparent",
        borderLeft:
          isActive && !selectMode
            ? "3px solid #1677ff"
            : "3px solid transparent",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          gap: 2,
        }}
      >
        {/* Row 1: Title + Checkbox + streaming spinner */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, minWidth: 0 }}>
          {selectMode && (
            <Checkbox
              checked={isSelected}
              disabled={isStreaming}
              onClick={(e) => e.stopPropagation()}
              onChange={() => onToggleSelect(item.id)}
            />
          )}
          {isStreaming && (
            <Tooltip title="Agent is running...">
              <span><Spin size="small" style={{ flexShrink: 0 }} /></span>
            </Tooltip>
          )}
          <Tooltip title={item.title || "New Chat"}>
            <span>
              <Text
                ellipsis
                style={{
                  maxWidth: collapsed ? 0 : 180,
                  display: "inline-block",
                  fontSize: 13,
                  lineHeight: "18px",
                }}
              >
                {item.is_temporary && "⚡ "}
                {item.title || "New Chat"}
              </Text>
            </span>
          </Tooltip>
        </div>
        {!collapsed && (
          <>
            {/* Row 2: Date (no wrap) */}
            <Text
              type="secondary"
              style={{ fontSize: 11, whiteSpace: "nowrap", lineHeight: "16px" }}
            >
              {new Date(item.updated_at).toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
              })}
            </Text>
            {/* Row 3: Tags (side by side) */}
            {(item.tags || []).length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
                {(item.tags || []).slice(0, 3).map((t) => (
                  <Tag
                    key={t.id}
                    style={{ fontSize: 10, lineHeight: "14px" }}
                    color={t.color || "default"}
                  >
                    {t.name}
                  </Tag>
                ))}
              </div>
            )}
            {/* Row 4: Action buttons at bottom */}
            {!selectMode && (
              <div style={{ display: "flex", gap: 2, marginTop: 4 }}>
                <Tooltip title="Edit">
                  <span>
                    <Button
                      type="text"
                      size="small"
                      aria-label="Edit session title"
                      icon={<EditOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        onEdit(item);
                      }}
                    />
                  </span>
                </Tooltip>
                {/* Issue #526 — move this session to a folder (accessible and
                    mobile-friendly alternative to dragging). */}
                <Dropdown
                  menu={{
                    items: [
                      ...moveTargets(folders).map((target) => ({
                        key: target.id ?? UNFILED_ID,
                        label: target.name,
                        disabled: (item.folder_id ?? null) === target.id,
                        onClick: (e: { domEvent: { stopPropagation: () => void } }) => {
                          e.domEvent.stopPropagation();
                          onMove(item.id, target.id);
                        },
                      })),
                      { type: "divider" as const },
                      {
                        key: "new-folder",
                        icon: <FolderAddOutlined />,
                        label: "New folder…",
                        onClick: (e: { domEvent: { stopPropagation: () => void } }) => {
                          e.domEvent.stopPropagation();
                          onNewFolder(item.id);
                        },
                      },
                    ] as MenuProps["items"],
                  }}
                  trigger={["click"]}
                >
                  <Tooltip title="Move to folder">
                    <Button
                      type="text"
                      size="small"
                      aria-label="Move session to folder"
                      icon={<FolderOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Tooltip>
                </Dropdown>
                <Tooltip title={item.is_pinned ? "Unpin" : "Pin"}>
                  <span>
                    <Button
                      type="text"
                      size="small"
                      aria-label={item.is_pinned ? "Unpin session" : "Pin session"}
                      icon={
                        item.is_pinned ? (
                          <PushpinFilled />
                        ) : (
                          <PushpinOutlined />
                        )
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        onPin(item.id, !item.is_pinned);
                      }}
                    />
                  </span>
                </Tooltip>
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: "json",
                        icon: <FileTextOutlined />,
                        label: "Download as JSON",
                        onClick: (e) => {
                          e.domEvent.stopPropagation();
                          exportSession(item.id, "json");
                        },
                      },
                      {
                        key: "txt",
                        icon: <FileOutlined />,
                        label: "Download as Text",
                        onClick: (e) => {
                          e.domEvent.stopPropagation();
                          exportSession(item.id, "txt");
                        },
                      },
                    ],
                  }}
                  trigger={["click"]}
                >
                  <Tooltip title="Export">
                    <Button
                      type="text"
                      size="small"
                      aria-label="Export session"
                      icon={<DownloadOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Tooltip>
                </Dropdown>
                <Popconfirm
                  title="Delete this session?"
                  description="This will permanently delete the session and all its messages."
                  onConfirm={() => onDelete(item.id)}
                  okText="Delete"
                  cancelText="Cancel"
                  okButtonProps={{ danger: true }}
                >
                  <Tooltip title="Delete">
                    <Button
                      type="text"
                      size="small"
                      danger
                      aria-label="Delete session"
                      icon={<DeleteOutlined />}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </Tooltip>
                </Popconfirm>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
});

export const SessionSidebar = React.memo(function SessionSidebar() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [editingSession, setEditingSession] = useState<SessionData | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editFolderId, setEditFolderId] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // ---- Issue #526: folders -------------------------------------------------
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(FOLDER_COLLAPSE_KEY);
      return raw ? new Set(JSON.parse(raw) as string[]) : new Set<string>();
    } catch {
      return new Set<string>();
    }
  });
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const [folderModal, setFolderModal] = useState<{
    mode: "create" | "edit";
    folder: FolderData | null;
  } | null>(null);
  const [folderName, setFolderName] = useState("");
  const [folderColor, setFolderColor] = useState<string | null>(null);
  /** Session IDs waiting to be filed into a folder that is being created. */
  const [pendingMoveSessionIds, setPendingMoveSessionIds] = useState<
    string[]
  >([]);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { streamingSessionIds, removeStreamingSession } = useStreamingContext();
  const streamingSessionIdsRef = useRef(streamingSessionIds);
  streamingSessionIdsRef.current = streamingSessionIds;

  // Use matchMedia instead of resize listener — only fires when crossing
  // the 768px boundary, not on every pixel resize (better performance).
  React.useEffect(() => {
    const mql = window.matchMedia("(max-width: 767px)");
    const handleChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    // Set initial value
    setIsMobile(mql.matches);
    mql.addEventListener("change", handleChange);
    return () => mql.removeEventListener("change", handleChange);
  }, []);

  // Issue #526 — remember which folders are collapsed across reloads.
  useEffect(() => {
    try {
      localStorage.setItem(
        FOLDER_COLLAPSE_KEY,
        JSON.stringify([...collapsedFolders]),
      );
    } catch {
      // Storage can be unavailable (private mode); collapsing still works
      // for the current session.
    }
  }, [collapsedFolders]);

  // ---- Issue #455: Poll streaming session status -------------------------
  // Poll ALL active sessions every 10 seconds.  When an agent finishes
  // (Redis stream:active: flag cleared), the session is removed from the
  // set and the sidebar spinner disappears for that session.
  //
  // The first check is delayed by 3 seconds to give the backend time to
  // set the Redis stream:active: flag after spawning the agent.
  useEffect(() => {
    if (streamingSessionIds.size === 0) return;

    let cancelled = false;

    const check = async () => {
      const ids = streamingSessionIdsRef.current;
      if (ids.size === 0) return;

      for (const sid of ids) {
        try {
          const status = await getStreamStatus(sid);
          if (cancelled) return;
          if (!status.active) {
            removeStreamingSession(sid);
          }
        } catch {
          // Session may no longer exist — remove from active set
          if (!cancelled) {
            removeStreamingSession(sid);
          }
        }
      }
    };

    // Delay first check by 3s so the backend has time to set the Redis flag.
    const timer = setTimeout(() => {
      check();
    }, 3000);
    const interval = setInterval(check, 30000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [streamingSessionIds, removeStreamingSession]);

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const result = await importSession(file);
      message.success(
        `Imported "${file.name}" with ${result.message_count} messages`,
      );
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      navigate(`/chat/${result.session_id}`);
    } catch (err) {
      message.error(
        `Import failed: ${err instanceof Error ? err.message : "Unknown error"}`,
      );
    } finally {
      // Reset so the same file can be re-imported
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const { data: sessions, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });

  // Issue #526 — folders are a separate, small collection; session counts are
  // derived client-side from `sessions` so they can never drift.
  const { data: folders } = useQuery({
    queryKey: ["folders"],
    queryFn: listFolders,
  });

  // Only show context indicator when the session actually exists (avoids 404
  // for lazy-created sessions that haven't been persisted yet).
  const sessionExists = sessions?.some(s => s.id === sessionId) ?? false;

  const createMutation = useMutation({
    mutationFn: () =>
      createSession({
        title: "New Chat",
        is_temporary: true,
        auto_route_enabled: true,
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["sessions"], (old: SessionData[] | undefined) =>
        [data, ...(old || []).filter(s => s.id !== data.id)]
      );
      navigate(`/chat/${data.id}`);
    },
    onError: (err) => message.error(`Failed to create session: ${err.message}`),
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      // Issue #526 — send only what actually changed.  `folder_id: null`
      // explicitly moves the session back to Unfiled.
      const payload: { title?: string; folder_id?: string | null } = {};
      const nextTitle = editTitle || editingSession!.title;
      if (nextTitle !== editingSession!.title) {
        payload.title = nextTitle;
      }
      if ((editFolderId ?? null) !== (editingSession!.folder_id ?? null)) {
        payload.folder_id = editFolderId ?? null;
      }
      if (Object.keys(payload).length === 0) {
        payload.title = nextTitle;
      }
      return updateSession(editingSession!.id, payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      queryClient.invalidateQueries({ queryKey: ["session", editingSession?.id] });
      setEditingSession(null);
    },
    onError: () => message.error("Failed to update session"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (sessionId === id) {
        navigate("/chat");
      }
    },
    onError: () => message.error("Failed to delete session"),
  });

  const pinMutation = useMutation({
    mutationFn: ({ id, is_pinned }: { id: string; is_pinned: boolean }) =>
      updateSession(id, { is_pinned }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  // ---- Issue #526: folder mutations ---------------------------------------
  const moveSessionMutation = useMutation({
    mutationFn: ({ id, folderId }: { id: string; folderId: string | null }) =>
      updateSession(id, { folder_id: folderId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (err: Error) =>
      message.error(`Failed to move session: ${err.message}`),
  });

  const createFolderMutation = useMutation({
    mutationFn: (data: { name: string; color: string | null }) =>
      createFolder(data),
    onSuccess: (folder) => {
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      const ids = pendingMoveSessionIds;
      closeFolderModal();
      // A "New folder…" started from a session's move menu files that
      // session into the folder that was just created.
      if (ids.length === 1) {
        moveSessionMutation.mutate({
          id: ids[0],
          folderId: folder.id,
        });
      } else if (ids.length > 1) {
        batchMoveMutation.mutate({
          ids,
          folderId: folder.id,
          folderName: folder.name,
        });
      }
    },
    onError: (err: Error) =>
      message.error(`Failed to create folder: ${err.message}`),
  });

  const saveFolderMutation = useMutation({
    mutationFn: (data: { id: string; name: string; color: string | null }) =>
      updateFolder(data.id, { name: data.name, color: data.color }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      closeFolderModal();
    },
    onError: (err: Error) =>
      message.error(`Failed to save folder: ${err.message}`),
  });

  const deleteFolderMutation = useMutation({
    mutationFn: (id: string) => deleteFolder(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders"] });
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (err: Error) =>
      message.error(`Failed to delete folder: ${err.message}`),
  });

  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => deleteSessions(ids),
    onSuccess: (data, ids) => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (data.deleted > 0) {
        message.success(`Deleted ${data.deleted} session${data.deleted !== 1 ? "s" : ""}`);
      }
      if (data.skipped.length > 0) {
        message.warning(`${data.skipped.length} session${data.skipped.length !== 1 ? "s" : ""} skipped (not owned or not found)`);
      }
      if (data.errors.length > 0) {
        message.error(`${data.errors.length} error${data.errors.length !== 1 ? "s" : ""} during deletion`);
      }
      setSelectMode(false);
      setSelectedIds(new Set());
      if (sessionId && ids.includes(sessionId)) {
        navigate("/chat");
      }
    },
    onError: (err: Error) => {
      message.error(`Failed to delete sessions: ${err.message}`);
      setSelectMode(false);
      setSelectedIds(new Set());
    },
  });

  const batchMoveMutation = useMutation({
    mutationFn: ({
      ids,
      folderId,
    }: {
      ids: string[];
      folderId: string | null;
      folderName: string;
    }) => moveSessions(ids, folderId),
    onSuccess: (data, { folderName }) => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      if (data.moved > 0) {
        message.success(
          `Moved ${data.moved} chat${data.moved !== 1 ? "s" : ""} to "${folderName}"`,
        );
      }
      if (data.skipped.length > 0) {
        const reasons = Array.from(
          new Set(data.skipped.map((s) => s.reason)),
        );
        message.warning(
          `${data.skipped.length} chat${data.skipped.length !== 1 ? "s" : ""} skipped (${reasons.join("; ")})`,
        );
      }
      setSelectMode(false);
      setSelectedIds(new Set());
    },
    onError: (err: Error) => {
      message.error(`Failed to move chats: ${err.message}`);
      // Keep selection so the user can retry
    },
  });

  // Sort: pinned first, then by updated_at.
  const sortedSessions = useMemo(
    () =>
      [...(sessions || [])].sort((a, b) => {
        if (a.is_pinned && !b.is_pinned) return -1;
        if (!a.is_pinned && b.is_pinned) return 1;
        return (
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        );
      }),
    [sessions],
  );

  // Stable callbacks for SessionListItem (avoids re-render when React.memo compares).
  const handleNavigate = useCallback(
    (path: string) => navigate(path),
    [navigate],
  );
  const handleEditSession = useCallback(
    (session: SessionData) => {
      setEditingSession(session);
      setEditTitle(session.title);
      setEditFolderId(session.folder_id ?? null);
    },
    [],
  );
  const handlePinSession = useCallback(
    (id: string, is_pinned: boolean) => {
      pinMutation.mutate({ id, is_pinned });
    },
    [pinMutation],
  );
  const handleDeleteSession = useCallback(
    (id: string) => deleteMutation.mutate(id),
    [deleteMutation],
  );
  const handleToggleSelect = useCallback(
    (id: string) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      });
    },
    [],
  );

  // -------------------------------------------------------------------------
  // Issue #526 — folded sidebar: rows, folder CRUD, drag & drop
  // -------------------------------------------------------------------------

  // Flatten folders + sessions into the single list Virtuoso renders.
  const rows = useMemo(
    () => buildSidebarRows(sessions || [], folders || [], collapsedFolders),
    [sessions, folders, collapsedFolders],
  );

  function closeFolderModal() {
    setFolderModal(null);
    setFolderName("");
    setFolderColor(null);
    setPendingMoveSessionIds([]);
  }

  const toggleFolder = useCallback((groupId: string) => {
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) {
        next.delete(groupId);
      } else {
        next.add(groupId);
      }
      return next;
    });
  }, []);

  const handleMoveSession = useCallback(
    (id: string, folderId: string | null) => {
      moveSessionMutation.mutate({ id, folderId });
    },
    [moveSessionMutation],
  );

  const handleSessionDragStart = useCallback(
    (event: React.DragEvent<HTMLDivElement>, id: string) => {
      event.dataTransfer.setData("text/plain", id);
      event.dataTransfer.effectAllowed = "move";
      setDraggingId(id);
    },
    [],
  );

  const handleSessionDragEnd = useCallback(() => {
    setDraggingId(null);
    setDropTargetId(null);
  }, []);

  const handleFolderDragOver = useCallback(
    (event: React.DragEvent<HTMLDivElement>, groupId: string) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      setDropTargetId(groupId);
    },
    [],
  );

  const handleFolderDragLeave = useCallback(() => {
    setDropTargetId(null);
  }, []);

  const handleFolderDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>, groupId: string) => {
      event.preventDefault();
      setDropTargetId(null);
      setDraggingId(null);

      const draggedSessionId = readDraggedSessionId(event.dataTransfer);
      if (!draggedSessionId) return;

      const targetFolderId = groupId === UNFILED_ID ? null : groupId;
      const dragged = (sessions || []).find((s) => s.id === draggedSessionId);
      if (!dragged) return;
      // Dropping a session on the folder it already lives in is a no-op.
      if ((dragged.folder_id ?? null) === targetFolderId) return;

      handleMoveSession(draggedSessionId, targetFolderId);
    },
    [sessions, handleMoveSession],
  );

  const handleMoveSelected = useCallback(
    (target: { id: string | null; name: string }) => {
      const ids = [...selectedIds];
      if (ids.length === 0) return;
      if (ids.length > 100) {
        message.warning("Select at most 100 chats to move at once");
        return;
      }
      batchMoveMutation.mutate({
        ids,
        folderId: target.id,
        folderName: target.name,
      });
    },
    [selectedIds, batchMoveMutation],
  );

  const openCreateFolder = useCallback((sessionIds: string[] = []) => {
    setPendingMoveSessionIds(sessionIds);
    setFolderName("");
    setFolderColor(null);
    setFolderModal({ mode: "create", folder: null });
  }, []);

  const handleNewFolderForSession = useCallback(
    (id: string) => openCreateFolder([id]),
    [openCreateFolder],
  );

  const openEditFolder = useCallback((folder: FolderData) => {
    setPendingMoveSessionIds([]);
    setFolderName(folder.name);
    setFolderColor(folder.color);
    setFolderModal({ mode: "edit", folder });
  }, []);

  const submitFolderModal = useCallback(() => {
    if (!folderModal) return;
    const name = folderName.trim();
    if (!name) {
      message.warning("Folder name is required");
      return;
    }
    if (folderModal.mode === "create") {
      createFolderMutation.mutate({ name, color: folderColor });
    } else if (folderModal.folder) {
      saveFolderMutation.mutate({
        id: folderModal.folder.id,
        name,
        color: folderColor,
      });
    }
  }, [
    folderModal,
    folderName,
    folderColor,
    createFolderMutation,
    saveFolderMutation,
  ]);

  const confirmDeleteFolder = useCallback(
    (folder: FolderData) => {
      const count = (sessions || []).filter(
        (s) => (s.folder_id ?? null) === folder.id,
      ).length;
      Modal.confirm({
        title: `Delete "${folder.name}"?`,
        content:
          count > 0
            ? `${count} chat${count === 1 ? "" : "s"} will move to Unfiled. No chats are deleted.`
            : "This folder is empty. No chats are deleted.",
        okText: "Delete",
        okButtonProps: { danger: true },
        cancelText: "Cancel",
        onOk: () => deleteFolderMutation.mutate(folder.id),
      });
    },
    [sessions, deleteFolderMutation],
  );

  const handleNewChatInFolder = useCallback(
    (folderId: string) => {
      // Same lazy-persistence flow as the plain New Chat button; the folder
      // rides along in navigation state until the first message creates the
      // session (Issue #526).
      const uuid = crypto.randomUUID();
      navigate(`/chat/${uuid}`, { state: { folderId } });
      if (isMobile) setMobileOpen(false);
    },
    [navigate, isMobile],
  );

  const renderRow = useCallback(
    (row: SidebarRow) => {
      if (row.kind === "folder") {
        const folder = (folders || []).find((f) => f.id === row.id) ?? null;
        return (
          <SessionFolderHeader
            id={row.id}
            name={row.name}
            color={row.color}
            count={row.count}
            collapsed={row.collapsed}
            isUnfiled={row.isUnfiled}
            isDropTarget={dropTargetId === row.id}
            onToggle={() => toggleFolder(row.id)}
            onNewChatHere={() => handleNewChatInFolder(row.id)}
            onRename={() => folder && openEditFolder(folder)}
            onChangeColor={() => folder && openEditFolder(folder)}
            onDelete={() => folder && confirmDeleteFolder(folder)}
            onDragOver={handleFolderDragOver}
            onDragLeave={handleFolderDragLeave}
            onDrop={handleFolderDrop}
          />
        );
      }

      if (row.kind === "placeholder") {
        return (
          <div
            style={{
              padding: "6px 24px 10px",
              color: "#8c8c8c",
              fontSize: 12,
            }}
          >
            No chats yet
          </div>
        );
      }

      return (
        <SessionListItem
          item={row.session}
          isActive={sessionId === row.session.id}
          isMobile={isMobile}
          collapsed={collapsed}
          selectMode={selectMode}
          isSelected={selectedIds.has(row.session.id)}
          isStreaming={streamingSessionIds.has(row.session.id)}
          onNavigate={handleNavigate}
          onToggleSelect={handleToggleSelect}
          onEdit={handleEditSession}
          onPin={handlePinSession}
          onDelete={handleDeleteSession}
          setSelectedIds={setSelectedIds}
          setMobileOpen={setMobileOpen}
          folders={folders || []}
          isDragging={draggingId === row.session.id}
          onMove={handleMoveSession}
          onNewFolder={handleNewFolderForSession}
          onDragStart={handleSessionDragStart}
          onDragEnd={handleSessionDragEnd}
        />
      );
    },
    [
      folders,
      sessionId,
      isMobile,
      collapsed,
      selectMode,
      selectedIds,
      streamingSessionIds,
      handleNavigate,
      handleToggleSelect,
      handleEditSession,
      handlePinSession,
      handleDeleteSession,
      dropTargetId,
      draggingId,
      toggleFolder,
      handleNewChatInFolder,
      openEditFolder,
      confirmDeleteFolder,
      handleMoveSession,
      handleNewFolderForSession,
      handleSessionDragStart,
      handleSessionDragEnd,
      handleFolderDragOver,
      handleFolderDragLeave,
      handleFolderDrop,
    ],
  );

  // Auto-scroll to active session when it changes or data loads.
  // Uses Virtuoso's imperative scrollToIndex API instead of DOM querySelector
  // because Virtuoso only renders visible items (virtualization), so the DOM
  // element for off-screen sessions doesn't exist (Issue #501).
  //
  // Issue #526: the index is the session's position in the *flattened* row
  // list, and a session inside a collapsed folder is expanded first.
  useEffect(() => {
    if (!sessionId || !sessions) return;

    let target = sessions.find((s) => s.id === sessionId);

    if (!target) {
      // Session might not be in the sidebar list (e.g. beyond the 2000-recent
      // limit, or navigated from search).  Check if we have the session detail
      // in the query cache (from ChatPage's fetch) and prepend it.
      const cached = queryClient.getQueryData<SessionData>(["session", sessionId]);
      if (cached) {
        queryClient.setQueryData<SessionData[]>(["sessions"], (old) => {
          if (!old) return [cached];
          if (old.some((s) => s.id === sessionId)) return old;
          return [cached, ...old];
        });
        target = cached;
      }
      if (!target) return;
    }

    // A session hidden inside a collapsed folder is not rendered: expand its
    // group and let the rebuilt rows bring the effect back around.
    const groupKey = groupKeyForFolderId(target.folder_id ?? null);
    if (collapsedFolders.has(groupKey)) {
      setCollapsedFolders((prev) => {
        const next = new Set(prev);
        next.delete(groupKey);
        return next;
      });
      return;
    }

    const idx = findSessionRowIndex(rows, sessionId);
    if (idx === -1) return;

    const timer = setTimeout(() => {
      virtuosoRef.current?.scrollToIndex({
        index: idx,
        behavior: "smooth",
        align: "center",
      });
    }, 100);
    return () => clearTimeout(timer);
  }, [sessionId, sessions, rows, collapsedFolders, queryClient]);

  const handleNewChat = (temporary = false) => {
    if (temporary) {
      // Temporary chat: create Redis-backed session immediately
      createMutation.mutate();
    } else {
      // Lazy persistence (Phase 2): generate a client-side UUID and
      // navigate — the backend session is created on the first message.
      const uuid = crypto.randomUUID();
      queryClient.setQueryData(["sessions"], (old: SessionData[] | undefined) =>
        old || []
      );
      navigate(`/chat/${uuid}`);
    }
  };

  const newChatMenuItems: MenuProps["items"] = [
    {
      key: "temporary",
      label: "Temporary Chat",
      icon: <ThunderboltOutlined />,
      onClick: () => handleNewChat(true),
    },
    { type: "divider" },
    {
      key: "new-folder",
      label: "New Folder",
      icon: <FolderAddOutlined />,
      onClick: () => openCreateFolder(),
    },
  ];

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const sidebarContent = (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#fafafa",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid #f0f0f0",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            width: "100%",
            gap: 6,
          }}
        >
          <div
            style={{ cursor: "pointer", display: "flex" }}
            onClick={() => navigate("/")}
          >
            <Logo size={28} textColor="#141414" />
          </div>
          {!collapsed && (
            <Space size={4} style={{ marginLeft: "auto" }}>
              {sessionExists && <ContextIndicator sessionId={sessionId} />}
              {sessions && sessions.length > 0 && (
                <Tooltip title={selectMode ? "Cancel selection" : "Select sessions"}>
                  <Button
                    type="text"
                    icon={selectMode ? <CloseOutlined /> : <CheckSquareOutlined />}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelectMode(!selectMode);
                      if (selectMode) {
                        setSelectedIds(new Set());
                      }
                    }}
                  />
                </Tooltip>
              )}
              <Tooltip title="Search">
                <Button
                  type="text"
                  icon={<SearchOutlined />}
                  size="small"
                  data-testid="session-search-btn"
                  onClick={() => setSearchOpen(true)}
                />
              </Tooltip>
              <Tooltip title="Memory">
                <Button
                  type="text"
                  icon={<DatabaseOutlined />}
                  size="small"
                  onClick={() => setMemoryOpen(true)}
                />
              </Tooltip>
              <Tooltip title="Import">
                <Button
                  type="text"
                  icon={<UploadOutlined />}
                  size="small"
                  onClick={() => fileInputRef.current?.click()}
                />
              </Tooltip>
              <input
                ref={fileInputRef}
                type="file"
                accept=".json"
                style={{ display: "none" }}
                onChange={handleImport}
              />
              <Tooltip title="Tasks">
                <Button
                  type="text"
                  icon={<FolderOpenOutlined />}
                  size="small"
                  onClick={() => navigate("/background-tasks")}
                />
              </Tooltip>
              <Tooltip title="Scheduled">
                <Button
                  type="text"
                  icon={<ClockCircleOutlined />}
                  size="small"
                  onClick={() => navigate("/scheduled-tasks")}
                />
              </Tooltip>
              <Tooltip title="Refresh">
                <Button
                  type="text"
                  icon={<ReloadOutlined />}
                  size="small"
                  onClick={() => window.location.reload()}
                />
              </Tooltip>
              <Tooltip title="Collapse sidebar">
                <Button
                  type="text"
                  icon={<MenuFoldOutlined />}
                  size="small"
                  onClick={() => setCollapsed(true)}
                />
              </Tooltip>
            </Space>
          )}
        </div>
      </div>

      {/* New Chat Button */}
      <div style={{ padding: "8px 12px" }}>
        {collapsed ? (
          <Tooltip title="New Chat" placement="right">
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              loading={createMutation.isPending}
              block
              onClick={() => handleNewChat(false)}
            />
          </Tooltip>
        ) : (
          <div style={{ display: "flex", gap: 0 }}>
            <Button
              type="dashed"
              icon={<PlusOutlined />}
              loading={createMutation.isPending}
              style={{ flex: 1 }}
              onClick={() => handleNewChat(false)}
            >
              New Chat
            </Button>
            <Dropdown menu={{ items: newChatMenuItems }} trigger={["click"]}>
              <Button
                type="dashed"
                icon={<DownOutlined />}
                loading={createMutation.isPending}
                style={{ width: 32 }}
              />
            </Dropdown>
          </div>
        )}
      </div>

      {/* Session List — virtualized for performance with many sessions */}
      {isLoading ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin />
        </div>
      ) : isError ? (
        <div style={{ padding: 16 }}>
          <Alert
            type="error"
            message="Failed to load sessions"
            description={error?.message || "An error occurred"}
            action={<Button size="small" onClick={() => refetch()}>Retry</Button>}
            showIcon
          />
        </div>
      ) : (
        <Virtuoso
          ref={virtuosoRef}
          style={{ flex: 1, height: "100%" }}
          data={rows}
          computeItemKey={rowKey}
          itemContent={(_index, row) => renderRow(row)}
        />
      )}

      {/* Select-mode bar — rendered outside Virtuoso so it's always visible */}
      {selectMode && (
        <div
          style={{
            background: "#fff",
            borderTop: "1px solid #f0f0f0",
            borderBottom: "1px solid #f0f0f0",
            padding: "8px 12px",
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexShrink: 0,
          }}
        >
          <Text type="secondary" style={{ fontSize: 13, flexShrink: 0 }}>
            {selectedIds.size} selected
          </Text>
          <Button
            size="small"
            onClick={() => {
              const selectable = sortedSessions.filter(
                (s) => !streamingSessionIds.has(s.id)
              );
              if (selectable.length === selectedIds.size) {
                setSelectedIds(new Set());
              } else {
                setSelectedIds(new Set(selectable.map((s) => s.id)));
              }
            }}
          >
            {selectedIds.size ===
            sortedSessions.filter((s) => !streamingSessionIds.has(s.id)).length
              ? "Deselect All"
              : "Select All"}
          </Button>
          <div style={{ flex: 1 }} />
          <Dropdown
            disabled={selectedIds.size === 0 || batchMoveMutation.isPending}
            menu={{
              items: [
                ...moveTargets(folders || []).map((target) => ({
                  key: target.id ?? UNFILED_ID,
                  label: target.name,
                  onClick: () => handleMoveSelected(target),
                })),
                { type: "divider" as const },
                {
                  key: "new-folder",
                  icon: <FolderAddOutlined />,
                  label: "New folder…",
                  onClick: () => {
                    const ids = [...selectedIds];
                    if (ids.length > 100) {
                      message.warning("Select at most 100 chats to move at once");
                      return;
                    }
                    openCreateFolder(ids);
                  },
                },
              ],
            }}
            trigger={["click"]}
          >
            <Button
              size="small"
              icon={<FolderOutlined />}
              disabled={selectedIds.size === 0}
              loading={batchMoveMutation.isPending}
              aria-label="Move selected sessions to folder"
            >
              Move
            </Button>
          </Dropdown>
          <Button
            type="primary"
            danger
            size="small"
            disabled={selectedIds.size === 0}
            loading={batchDeleteMutation.isPending}
            onClick={() => {
              const ids = [...selectedIds];
              const isActiveSelected = sessionId && ids.includes(sessionId);
              Modal.confirm({
                title: `Delete ${ids.length} session${ids.length !== 1 ? "s" : ""}?`,
                content: (
                  <div>
                    <p>This will permanently delete {ids.length} session{ids.length !== 1 ? "s" : ""} and all their messages. This action cannot be undone.</p>
                    {isActiveSelected && (
                      <p style={{ color: "#ff4d4f", fontWeight: 500 }}>
                        Warning: Your current chat will also be deleted.
                      </p>
                    )}
                  </div>
                ),
                okText: "Delete",
                okButtonProps: { danger: true },
                cancelText: "Cancel",
                onOk: () => batchDeleteMutation.mutate(ids),
              });
            }}
          >
            Delete
          </Button>
        </div>
      )}

      {/* Footer */}
      <div
        style={{
          padding: "8px 12px",
          borderTop: "1px solid #f0f0f0",
        }}
      >
        <Space
          style={{ width: "100%", justifyContent: "space-between" }}
        >
          {user?.role !== "user" ? (
            <a
              onClick={() => navigate("/admin")}
              style={{ fontSize: 12, cursor: "pointer", color: "#1677ff" }}
            >
              {user?.display_name}
            </a>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {user?.display_name}
            </Text>
          )}
          <Tooltip title="Settings">
            <Button
              type="text"
              size="small"
              icon={<SettingOutlined />}
              onClick={() => navigate("/settings")}
            />
          </Tooltip>
          <Tooltip title="Logout">
            <Button
              type="text"
              size="small"
              icon={<LogoutOutlined />}
              onClick={handleLogout}
            />
          </Tooltip>
        </Space>
      </div>

      {/* Search Drawer */}
      <Drawer
        title="Search Sessions"
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
      >
        <SessionSearch
          onClose={() => setSearchOpen(false)}
          onSelect={(session) => {
            // If the selected session is not already in the sidebar list,
            // prepend it to the cache so it appears and can be highlighted.
            queryClient.setQueryData<SessionData[]>(["sessions"], (old) => {
              if (!old) return [session];
              if (old.some((s) => s.id === session.id)) return old;
              return [session, ...old];
            });
          }}
        />
      </Drawer>

      {/* Memory Manager */}
      <MemoryManager
        open={memoryOpen}
        onClose={() => setMemoryOpen(false)}
        sessionId={sessionId}
      />

      {/* Edit Chat Modal */}
      <Modal
        title="Edit Chat"
        open={editingSession !== null}
        onOk={() => updateMutation.mutate()}
        onCancel={() => setEditingSession(null)}
        confirmLoading={updateMutation.isPending}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input
            placeholder="Chat title"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
          />
          <div>
            <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: "block" }}>
              Folder
            </Text>
            <Select
              style={{ width: "100%" }}
              value={editFolderId}
              onChange={(value) => setEditFolderId(value ?? null)}
              disabled={editingSession?.is_temporary}
              placeholder="Unfiled"
              options={[
                { value: null, label: "Unfiled" },
                ...(folders || []).map((f) => ({
                  value: f.id,
                  label: f.name,
                })),
              ]}
            />
            {editingSession?.is_temporary && (
              <Text type="secondary" style={{ fontSize: 11 }}>
                Temporary chats cannot be filed.
              </Text>
            )}
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12, marginBottom: 4, display: "block" }}>
              Tags
            </Text>
            <Space wrap style={{ marginBottom: 8 }}>
              {(editingSession?.tags || []).map((t) => (
                <Tag
                  key={t.id}
                  closable
                  color={t.color || "default"}
                  onClose={() => {
                    if (!editingSession) return;
                    removeTagFromSession(editingSession.id, t.id).then(() => {
                      queryClient.invalidateQueries({ queryKey: ["sessions"] });
                      queryClient.invalidateQueries({ queryKey: ["session", editingSession.id] });
                      // Refresh the editing session
                      setEditingSession((prev) =>
                        prev
                          ? { ...prev, tags: (prev.tags || []).filter((x) => x.id !== t.id) }
                          : null,
                      );
                    }).catch(() => message.error("Failed to remove tag"));
                  }}
                >
                  {t.name}
                </Tag>
              ))}
            </Space>
            <Input.Search
              placeholder="Add tag..."
              enterButton="Add"
              size="small"
              onSearch={(val) => {
                if (!editingSession || !val.trim()) return;
                addTagToSession(editingSession.id, val.trim()).then((updated) => {
                  queryClient.invalidateQueries({ queryKey: ["sessions"] });
                  queryClient.invalidateQueries({ queryKey: ["session", editingSession.id] });
                  queryClient.invalidateQueries({ queryKey: ["tenant-tags"] });
                  setEditingSession(updated);
                }).catch(() => message.error("Failed to add tag"));
              }}
            />
          </div>
        </Space>
      </Modal>

      {/* Create / edit folder modal (Issue #526) */}
      <Modal
        title={folderModal?.mode === "create" ? "New Folder" : "Edit Folder"}
        open={folderModal !== null}
        onOk={submitFolderModal}
        onCancel={closeFolderModal}
        okText={folderModal?.mode === "create" ? "Create" : "Save"}
        confirmLoading={
          createFolderMutation.isPending || saveFolderMutation.isPending
        }
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input
            placeholder="Folder name"
            value={folderName}
            maxLength={100}
            onChange={(e) => setFolderName(e.target.value)}
            onPressEnter={submitFolderModal}
          />
          <div>
            <Text
              type="secondary"
              style={{ fontSize: 12, marginBottom: 4, display: "block" }}
            >
              Colour
            </Text>
            <Space wrap>
              {FOLDER_COLORS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  aria-label={`Folder colour ${preset}`}
                  onClick={() =>
                    setFolderColor(folderColor === preset ? null : preset)
                  }
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: "50%",
                    background: preset,
                    border:
                      folderColor === preset
                        ? "2px solid #141414"
                        : "1px solid #d9d9d9",
                    cursor: "pointer",
                  }}
                />
              ))}
              <Button
                size="small"
                type="link"
                onClick={() => setFolderColor(null)}
              >
                No colour
              </Button>
            </Space>
          </div>
        </Space>
      </Modal>
    </div>
  );

  // Mobile: use Drawer
  if (isMobile) {
    return (
      <>
        <Button
          type="text"
          icon={<MenuOutlined />}
          onClick={() => setMobileOpen(true)}
          style={{ position: "fixed", top: 8, left: 8, zIndex: 100 }}
        />
        <Drawer
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          placement="left"
          width={310}
          styles={{ body: { padding: 0 } }}
        >
          {sidebarContent}
        </Drawer>
      </>
    );
  }

  // Desktop: use Sider
  return (
    <Sider
      width={310}
      collapsible
      collapsed={collapsed}
      collapsedWidth={0}
      trigger={null}
      onCollapse={setCollapsed}
      theme="light"
      style={{
        borderRight: collapsed ? "none" : "1px solid #f0f0f0",
        overflow: collapsed ? "visible" : "hidden",
        position: "relative",
      }}
    >
      {!isMobile && collapsed && (
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 8,
            zIndex: 200,
            width: 32,
            display: "flex",
            justifyContent: "center",
          }}
        >
          <Tooltip title="Expand sidebar" placement="right">
            <Button
              type="text"
              icon={<MenuUnfoldOutlined />}
              onClick={() => setCollapsed(false)}
              style={{
                background: "#fff",
                border: "1px solid #d9d9d9",
                boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
              }}
            />
          </Tooltip>
        </div>
      )}
      {sidebarContent}
    </Sider>
  );
});

export default SessionSidebar;
