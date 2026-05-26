// =============================================================================
// PH Agent Hub — DemoPage (Anonymous "Try It Now" Experience)
// =============================================================================
// Public page that loads a demo chat session without authentication.
// Shows a "Sign up to save" banner and reuses ChatWindow in embedded mode.
// =============================================================================

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, ConfigProvider, Layout, Spin, Typography, theme as antTheme } from "antd";
import { ChatWindow } from "../components/ChatWindow";
import { setToken } from "../../../services/api";
import { createDemoSession, type DemoConfig } from "../services/demo";
import { LoginOutlined } from "@ant-design/icons";

const { Content } = Layout;
const { Text } = Typography;

export function DemoPage() {
  const navigate = useNavigate();

  const [config, setConfig] = useState<DemoConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionExpired, setSessionExpired] = useState(false);

  const initSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSessionExpired(false);

    try {
      const data = await createDemoSession();
      setToken(data.guest_token);
      setConfig(data);
    } catch (err: unknown) {
      const detail =
        err && typeof err === "object" && "detail" in err
          ? (err as { detail: string }).detail
          : "Demo is not available right now. Try again later.";
      setError(detail);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    initSession();
  }, [initSession]);

  // Derive Ant Design theme (minimal — no tenant theme for demo)
  const themeConfig = {
    algorithm: antTheme.defaultAlgorithm,
    token: {
      colorPrimary: "#1677ff",
      borderRadius: 8,
    },
  };

  const handleSignUp = () => {
    navigate("/login");
  };

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
          gap: 16,
        }}
      >
        <Spin size="large" />
        <Text type="secondary">Setting up your demo session…</Text>
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
          padding: 32,
          gap: 16,
          textAlign: "center",
        }}
      >
        <Text type="danger" style={{ fontSize: 16 }}>
          {error}
        </Text>
        <Button type="primary" onClick={initSession}>
          Try Again
        </Button>
        <Button type="link" onClick={handleSignUp}>
          Sign up for a full account
        </Button>
      </div>
    );
  }

  if (sessionExpired) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
          padding: 32,
          gap: 16,
          textAlign: "center",
        }}
      >
        <Text style={{ fontSize: 16 }}>Your demo session has expired.</Text>
        <Button type="primary" onClick={initSession}>
          Start a New Demo
        </Button>
        <Button type="link" onClick={handleSignUp}>
          Sign up for a full account
        </Button>
      </div>
    );
  }

  if (!config) return null;

  return (
    <ConfigProvider theme={themeConfig}>
      <Layout style={{ height: "100vh", overflow: "hidden", background: "#fff" }}>
        {/* Demo banner */}
        <div
          style={{
            background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
            padding: "8px 16px",
            textAlign: "center",
            color: "#fff",
            fontSize: 14,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            flexShrink: 0,
          }}
        >
          <span>
            🚀 You&apos;re trying the demo.{" "}
            <strong>Your conversations are temporary and expire after 1 hour.</strong>
          </span>
          <Button
            type="default"
            size="small"
            icon={<LoginOutlined />}
            onClick={handleSignUp}
            style={{
              background: "rgba(255,255,255,0.2)",
              borderColor: "rgba(255,255,255,0.4)",
              color: "#fff",
            }}
          >
            Sign Up Free
          </Button>
        </div>

        {/* Chat area */}
        <Content style={{ padding: 0, overflow: "hidden", flex: 1 }}>
          <ChatWindow
            sessionId={config.session_id}
            isTemporary={true}
            embedded={true}
            demo={true}
            featureFlags={config.feature_flags as Record<string, boolean> | undefined}
            selectedModelId={config.default_model_id ?? undefined}
            selectedSkillId={config.default_skill_id ?? undefined}
            selectedTemplateId={config.default_template_id ?? undefined}
            temperature={null}
            crossSessionMemoryEnabled={null}
            onSessionUpdate={async () => {}}
          />
        </Content>
      </Layout>
    </ConfigProvider>
  );
}
