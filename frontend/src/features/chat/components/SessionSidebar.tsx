// =============================================================================
// PH Agent Hub — SessionSidebar
// =============================================================================
// Ant Design Layout.Sider (Drawer on mobile); session list (pinned first);
// instant new chat; edit button per session (rename, temp toggle);
// links to MemoryManager, SessionSearch, logout.
// =============================================================================

import React, { useState, useEffect, useRef } from "react";
import {
  Alert,
  Layout,
  List,
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
} from "antd";
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
  updateSession,
  SessionData,
  addTagToSession,
  removeTagFromSession,
  exportSession,
  importSession,
  getStreamStatus,
} from "../services/chat";
import { ContextIndicator } from "./ContextIndicator";
import { MemoryManager } from "./MemoryManager";
import { SessionSearch } from "./SessionSearch";

const { Sider } = Layout;
const { Text } = Typography;

export function SessionSidebar() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [editingSession, setEditingSession] = useState<SessionData | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { streamingSessionIds, removeStreamingSession } = useStreamingContext();
  const streamingSessionIdsRef = useRef(streamingSessionIds);
  streamingSessionIdsRef.current = streamingSessionIds;

  React.useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

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
    const interval = setInterval(check, 10000);
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
    mutationFn: () =>
      updateSession(editingSession!.id, {
        title: editTitle || editingSession!.title,
      }),
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

  // Sort: pinned first, then by updated_at.
  const sortedSessions = [...(sessions || [])]
    .sort((a, b) => {
    if (a.is_pinned && !b.is_pinned) return -1;
    if (!a.is_pinned && b.is_pinned) return 1;
    return (
      new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    );
  });

  // Auto-scroll to active session when it changes or data loads.
  useEffect(() => {
    if (!sessionId) return;
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-session-id="${sessionId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [sessionId, sessions]);

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
          <Logo size={28} textColor="#141414" />
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

      {/* Session List */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {isError ? (
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
          <List
            loading={isLoading}
            dataSource={sortedSessions}
            locale={{ emptyText: "No sessions" }}
            renderItem={(item) => (
              <List.Item
                data-session-id={item.id}
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
                  navigate(sessionId === item.id ? "/chat" : `/chat/${item.id}`);
                  if (isMobile) setMobileOpen(false);
                }}
                style={{
                  cursor: "pointer",
                  padding: "8px 12px",
                  background:
                    sessionId === item.id && !selectMode ? "#e6f4ff" : "transparent",
                  borderLeft:
                    sessionId === item.id && !selectMode
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
                        checked={selectedIds.has(item.id)}
                        disabled={streamingSessionIds.has(item.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => {
                          setSelectedIds((prev) => {
                            const next = new Set(prev);
                            if (next.has(item.id)) {
                              next.delete(item.id);
                            } else {
                              next.add(item.id);
                            }
                            return next;
                          });
                        }}
                      />
                    )}
                    {streamingSessionIds.has(item.id) && (
                      <Tooltip title="Agent is running...">
                        <Spin size="small" style={{ flexShrink: 0 }} />
                      </Tooltip>
                    )}
                    <Tooltip title={item.title || "New Chat"}>
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
                            <Button
                              type="text"
                              size="small"
                              aria-label="Edit session title"
                              icon={<EditOutlined />}
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditingSession(item);
                                setEditTitle(item.title);
                              }}
                            />
                          </Tooltip>
                          <Tooltip title={item.is_pinned ? "Unpin" : "Pin"}>
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
                                pinMutation.mutate({
                                  id: item.id,
                                  is_pinned: !item.is_pinned,
                                });
                              }}
                            />
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
                            onConfirm={() => deleteMutation.mutate(item.id)}
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
              </List.Item>
            )}
          />
        )}

        {selectMode && (
          <div
            style={{
              position: "sticky",
              bottom: 0,
              background: "#fff",
              borderTop: "1px solid #f0f0f0",
              padding: "8px 12px",
              display: "flex",
              alignItems: "center",
              gap: 8,
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
              Delete Selected
            </Button>
          </div>
        )}
      </div>

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
        <SessionSearch onClose={() => setSearchOpen(false)} />
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
}

export default SessionSidebar;
