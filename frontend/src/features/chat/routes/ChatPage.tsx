// =============================================================================
// PH Agent Hub — ChatPage
// =============================================================================
// Main chat layout: SessionSidebar + ChatWindow + input area.
// =============================================================================

import { useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { Layout, Button, Typography, message, Space, Grid, Tooltip } from "antd";
import { PlusOutlined, ThunderboltOutlined, FolderOpenOutlined, ClockCircleOutlined, SettingOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SessionSidebar } from "../components/SessionSidebar";
import { ChatWindow } from "../components/ChatWindow";
import { getSession, createSession, updateSession } from "../services/chat";
import { NotificationBell } from "../../../shared/components/NotificationBell";

const { Content } = Layout;
const { Title, Text } = Typography;
const { useBreakpoint } = Grid;

export function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const screens = useBreakpoint();
  const isMobile = !screens.md;

  // Issue #573 — the mobile "Options" trigger lives in this header row, so the
  // chat-options drawer state is owned here and passed down to ChatWindow.
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Issue #526 — starting a chat from a folder's "+" button passes the target
  // folder through navigation state.  It only matters while the session is
  // still pending (lazy creation): the folder id rides along with the first
  // message's session_data so the backend creates the session in place.
  const pendingFolderId = (location.state as { folderId?: string } | null)
    ?.folderId;

  const { data: session } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId!),
    enabled: !!sessionId,
    retry: false,
    // No staleTime — we rely on manual invalidation via onStreamStart /
    // onMessageComplete to pull fresh data when the session is created.
    // The backend now returns is_pending:true instead of 404 for
    // lazy-created sessions, so no console error is logged.
  });

  // A session is "pending" when the backend hasn't persisted it yet
  // (lazy creation).  The ChatWindow uses this flag to read/write
  // drafts from localStorage instead of API calls.
  const isPending = session?.is_pending ?? true;

  const handleSessionUpdate = async (data: Record<string, unknown>) => {
    if (!sessionId) return;
    // Skip if the session is still pending (lazy, not yet created on backend).
    // updateSession() would 404, and the pending settings are already submitted
    // via session_data in the first SSE message.
    if (isPending) return;
    try {
      await updateSession(sessionId, data as Record<string, string | null>);
    } catch (err) {
      // Silently ignore 404 — the session may not be persisted yet (race with
      // lazy creation). Other errors are unexpected; log but don't alert the user
      // since the session is still functional.
      if (err && typeof err === "object" && "status" in err && (err as any).status !== 404) {
        message.error("Failed to update session settings");
      }
    } finally {
      queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
    }
  };

  const handleNewChat = () => {
    // Lazy persistence (Phase 2): client-side UUID, no API call
    const uuid = crypto.randomUUID();
    navigate(`/chat/${uuid}`);
  };

  const handleNewTemporaryChat = async () => {
    try {
      const session = await createSession({
        title: "New Chat",
        is_temporary: true,
      });
      navigate(`/chat/${session.id}`);
    } catch {
      // Error creating session
    }
  };

  return (
    <Layout style={{ height: "100dvh", overflow: "hidden" }}>
      <SessionSidebar />
      <Content style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Header bar with notification bell and navigation (Issue #449) */}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            padding: "6px 16px",
            borderBottom: "1px solid #f0f0f0",
            background: "#fff",
          }}
        >
          <Space size={isMobile ? 2 : 4}>
            {isMobile ? (
              <>
                <Tooltip title="Tasks">
                  <Button
                    type="text"
                    icon={<FolderOpenOutlined />}
                    aria-label="Tasks"
                    onClick={() => navigate("/background-tasks")}
                  />
                </Tooltip>
                <Tooltip title="Scheduled">
                  <Button
                    type="text"
                    icon={<ClockCircleOutlined />}
                    aria-label="Scheduled"
                    onClick={() => navigate("/scheduled-tasks")}
                  />
                </Tooltip>
                {/* Only meaningful once a session (even a pending one) exists —
                    the drawer lives in ChatWindow. */}
                {sessionId && (
                  <Tooltip title="Options">
                    <Button
                      type="text"
                      icon={<SettingOutlined />}
                      aria-label="Options"
                      data-testid="chat-options-button"
                      onClick={() => setSettingsOpen(true)}
                    />
                  </Tooltip>
                )}
                <NotificationBell />
              </>
            ) : (
              <>
                <Button
                  type="text"
                  icon={<FolderOpenOutlined />}
                  onClick={() => navigate("/background-tasks")}
                >
                  Tasks
                </Button>
                <Button
                  type="text"
                  icon={<ClockCircleOutlined />}
                  onClick={() => navigate("/scheduled-tasks")}
                >
                  Scheduled
                </Button>
                <NotificationBell />
              </>
            )}
          </Space>
        </div>
        {!sessionId ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              flex: 1,
              gap: 16,
            }}
          >
            <Title level={3} style={{ margin: 0 }}>
              Welcome to PH Agent Hub
            </Title>
            <Text type="secondary">
              Select a conversation from the sidebar or start a new one
            </Text>
            <div style={{ display: "flex", gap: 12 }}>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                size="large"
                onClick={handleNewChat}
              >
                New Chat
              </Button>
              <Button
                icon={<ThunderboltOutlined />}
                size="large"
                onClick={handleNewTemporaryChat}
              >
                New Temporary Chat
              </Button>
            </div>
          </div>
        ) : (
          <ChatWindow
            sessionId={sessionId!}
            isPending={isPending}
            folderId={isPending ? pendingFolderId : undefined}
            isTemporary={session?.is_temporary}
            selectedModelId={session?.selected_model_id ?? undefined}
            selectedTemplateId={session?.selected_template_id ?? undefined}
            selectedSkillId={session?.selected_skill_id ?? undefined}
            temperature={session?.temperature ?? null}
            thinkingEnabled={session?.thinking_enabled ?? null}
            reasoningEffort={session?.reasoning_effort ?? null}
            crossSessionMemoryEnabled={session?.cross_session_retrieval_enabled ?? null}
            autoRouteEnabled={session?.auto_route_enabled ?? false}
            autoSelectTools={session?.auto_select_tools ?? true}
            onSessionUpdate={handleSessionUpdate}
            settingsOpen={settingsOpen}
            onSettingsOpenChange={setSettingsOpen}
          />
        )}
      </Content>
    </Layout>
  );
}

export default ChatPage;
