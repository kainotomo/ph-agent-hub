// =============================================================================
// PH Agent Hub — WidgetPage (Embedded Chat)
// =============================================================================
// Lightweight chat page loaded inside the embed iframe.  No sidebar,
// no auth flow — uses a guest JWT obtained from the widget config
// endpoint.
// =============================================================================

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ConfigProvider, Layout, Spin, Typography, theme as antTheme } from "antd";
import { ChatWindow } from "../components/ChatWindow";
import { setToken } from "../../../services/api";

const { Content } = Layout;
const { Text } = Typography;

interface WidgetConfig {
  guest_token: string;
  session_id: string;
  theme: Record<string, unknown>;
  feature_flags: Record<string, unknown>;
  default_model_id: string | null;
  default_skill_id: string | null;
  default_template_id: string | null;
}

export function WidgetPage() {
  const [searchParams] = useSearchParams();
  const rawToken = searchParams.get("token");

  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!rawToken) {
      setError("Missing token parameter");
      setLoading(false);
      return;
    }

    const baseUrl = import.meta.env.VITE_API_URL || "/api";

    fetch(`${baseUrl}/widget/config/${encodeURIComponent(rawToken)}`)
      .then((res) => {
        if (!res.ok) return res.json().then((d) => Promise.reject(d.detail || "Failed to load widget config"));
        return res.json();
      })
      .then((data: WidgetConfig) => {
        setToken(data.guest_token);
        setConfig(data);

        // Notify parent frame of the session ID and ready state
        window.parent.postMessage(
          { type: "widget:ready", sessionId: data.session_id },
          "*",
        );

        // Resize observer: send height changes to parent
        const observer = new ResizeObserver((entries) => {
          for (const entry of entries) {
            window.parent.postMessage(
              { type: "widget:resize", height: entry.contentRect.height },
              "*",
            );
          }
        });
        observer.observe(document.body);
        setLoading(false);
      })
      .catch((err) => {
        setError(typeof err === "string" ? err : "Failed to load widget");
        setLoading(false);
      });
  }, [rawToken]);

  // Derive Ant Design theme from config
  const primaryColor = (config?.theme?.primary_color as string) || "#1677ff";
  const themeConfig = {
    algorithm: antTheme.defaultAlgorithm,
    token: {
      colorPrimary: primaryColor,
      borderRadius: 8,
    },
  };

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100vh",
          padding: 24,
        }}
      >
        <Text type="danger">{error}</Text>
      </div>
    );
  }

  if (!config) return null;

  return (
    <ConfigProvider theme={themeConfig}>
      <Layout style={{ height: "100vh", overflow: "hidden", background: "#fff" }}>
        <Content style={{ padding: 0, overflow: "hidden" }}>
          <ChatWindow
            sessionId={config.session_id}
            isTemporary={true}
            embedded={true}
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
