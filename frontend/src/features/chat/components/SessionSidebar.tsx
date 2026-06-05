// =============================================================================
// PH Agent Hub — SessionSidebar
// =============================================================================
// Ant Design Layout.Sider (Drawer on mobile); session list (pinned first);
// instant new chat; edit button per session (rename, temp toggle);
// links to MemoryManager, SessionSearch, logout.
// =============================================================================

import React, { useState, useEffect } from "react";
import {
  Alert,
  Layout,
  List,
  Button,
  Typography,
  Space,
  Tooltip,
  Drawer,
  Modal,
  Input,
  Dropdown,
  message,
  Tag,
  Popconfirm,
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
  EditOutlined,
  DownOutlined,
  ThunderboltOutlined,
  MenuOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { Logo } from "../../../shared/components/Logo";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../../providers/AuthProvider";
import {
  listSessions,
  createSession,
  deleteSession,
  updateSession,
  SessionData,
  addTagToSession,
  removeTagFromSession,
} from "../services/chat";
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
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);

  React.useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const { data: sessions, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });

  const createMutation = useMutation({
    mutationFn: ({ is_temporary }: { is_temporary?: boolean }) =>
      createSession({
        title: "New Chat",
        is_temporary: is_temporary ?? false,
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
    createMutation.mutate({ is_temporary: temporary });
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
        <Space
          style={{
            width: "100%",
            justifyContent: "space-between",
          }}
        >
          <Logo size={28} showText={!collapsed} textColor="#141414" />
          {!collapsed && (
            <Space size={4}>
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
        </Space>
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
                  navigate(sessionId === item.id ? "/chat" : `/chat/${item.id}`);
                  if (isMobile) setMobileOpen(false);
                }}
                style={{
                  cursor: "pointer",
                  padding: "8px 12px",
                  background:
                    sessionId === item.id ? "#e6f4ff" : "transparent",
                  borderLeft:
                    sessionId === item.id
                      ? "3px solid #1677ff"
                      : "3px solid transparent",
                }}
                actions={[
                  <Tooltip title="Edit" key="edit">
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingSession(item);
                        setEditTitle(item.title);
                      }}
                    />
                  </Tooltip>,
                  <Tooltip
                    title={item.is_pinned ? "Unpin" : "Pin"}
                    key="pin"
                  >
                    <Button
                      type="text"
                      size="small"
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
                  </Tooltip>,
                  <Popconfirm
                    key="delete"
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
                        icon={<DeleteOutlined />}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Tooltip>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Text
                      ellipsis
                      style={{
                        maxWidth: collapsed ? 0 : 160,
                        display: "inline-block",
                      }}
                    >
                      {item.is_temporary && "⚡ "}
                      {item.title}
                    </Text>
                  }
                  description={
                    !collapsed ? (
                      <div>
                        <Text
                          type="secondary"
                          style={{ fontSize: 11 }}
                        >
                          {new Date(item.updated_at).toLocaleDateString()}
                        </Text>
                        {(item.tags || []).length > 0 && (
                          <div style={{ marginTop: 2 }}>
                            {(item.tags || []).slice(0, 3).map((t) => (
                              <Tag
                                key={t.id}
                                style={{ fontSize: 10, lineHeight: "14px", marginBottom: 2 }}
                                color={t.color || "default"}
                              >
                                {t.name}
                              </Tag>
                            ))}
                          </div>
                        )}
                      </div>
                    ) : null
                  }
                />
              </List.Item>
            )}
          />
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
          width={280}
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
      width={280}
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
