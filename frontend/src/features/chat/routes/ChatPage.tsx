// =============================================================================
// PH Agent Hub — ChatPage
// =============================================================================
// Main chat layout: SessionSidebar + ChatWindow + input area.
// =============================================================================

import { useParams, useNavigate } from "react-router-dom";
import { Layout, Button, Typography, message, Space } from "antd";
import { PlusOutlined, ThunderboltOutlined, FolderOpenOutlined, ClockCircleOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SessionSidebar } from "../components/SessionSidebar";
import { ChatWindow } from "../components/ChatWindow";
import { getSession, createSession, updateSession } from "../services/chat";
import { NotificationBell } from "../../../shared/components/NotificationBell";

const { Content } = Layout;
const { Title, Text } = Typography;

export function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

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
          <Space size={4}>
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
            isTemporary={session?.is_temporary}
            selectedModelId={session?.selected_model_id ?? undefined}
            selectedTemplateId={session?.selected_template_id ?? undefined}
            selectedSkillId={session?.selected_skill_id ?? undefined}
            temperature={session?.temperature ?? null}
            crossSessionMemoryEnabled={session?.cross_session_retrieval_enabled ?? null}
            autoRouteEnabled={session?.auto_route_enabled ?? false}
            autoSelectTools={session?.auto_select_tools ?? true}
            onSessionUpdate={handleSessionUpdate}
          />
        )}
      </Content>
    </Layout>
  );
}

export default ChatPage;
