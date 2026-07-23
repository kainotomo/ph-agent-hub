// =============================================================================
// PH Agent Hub — ChatPage
// =============================================================================
// Main chat layout: SessionSidebar + ChatWindow + input area.
// =============================================================================

import { useParams, useNavigate } from "react-router-dom";
import { Layout, Button, Typography, message } from "antd";
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

  const { data: session } = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => getSession(sessionId!),
    enabled: !!sessionId,
    retry: false,
    // No staleTime — we rely on manual invalidation via onStreamStart /
    // onMessageComplete to pull fresh data when the session is created.
    // The initial 404 for lazy sessions is expected and harmless (one
    // single error, not a flood, since invalidations are now controlled).
  });

  const handleSessionUpdate = async (data: Record<string, unknown>) => {
    if (!sessionId) return;
    try {
      await updateSession(sessionId, data as Record<string, string | null>);
    } catch {
      message.error("Failed to update session settings");
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
        ) : (
          <ChatWindow
            sessionId={sessionId!}
            isPending={!session}
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
