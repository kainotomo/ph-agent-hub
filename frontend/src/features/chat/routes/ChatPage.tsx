// =============================================================================
// PH Agent Hub — ChatPage
// =============================================================================
// Main chat layout: SessionSidebar + ChatWindow + input area.
// =============================================================================

import { useParams, useNavigate } from "react-router-dom";
import { Layout, Empty, Button, Typography, Spin } from "antd";
import { PlusOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SessionSidebar } from "../components/SessionSidebar";
import { ChatWindow } from "../components/ChatWindow";
import { getSession, createSession, updateSession } from "../services/chat";

const { Content } = Layout;
const { Title, Text } = Typography;

export function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: session, isLoading: loadingSession } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId!),
    enabled: !!sessionId,
  });

  const handleSessionUpdate = async (data: Record<string, unknown>) => {
    if (!sessionId) return;
    await updateSession(sessionId, data as Record<string, string | null>);
    queryClient.invalidateQueries({ queryKey: ["session", sessionId] });
  };

  const handleNewChat = async () => {
    try {
      const session = await createSession({ title: "New Chat" });
      navigate(`/chat/${session.id}`);
    } catch {
      // Error creating session
    }
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
    <Layout style={{ height: "100vh", overflow: "hidden" }}>
      <SessionSidebar />
      <Content>
        {!sessionId ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
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
        ) : loadingSession ? (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
            }}
          >
            <Spin size="large" />
          </div>
        ) : session ? (
          <ChatWindow
            sessionId={session.id}
            isTemporary={session.is_temporary}
            selectedModelId={session.selected_model_id ?? undefined}
            selectedTemplateId={session.selected_template_id ?? undefined}
            selectedSkillId={session.selected_skill_id ?? undefined}
            temperature={session.temperature ?? null}
            crossSessionMemoryEnabled={session.cross_session_retrieval_enabled ?? null}
            autoRouteEnabled={session.auto_route_enabled ?? false}
            onSessionUpdate={handleSessionUpdate}
          />
        ) : (
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
            }}
          >
            <Empty description="Session not found" />
          </div>
        )}
      </Content>
    </Layout>
  );
}

export default ChatPage;
