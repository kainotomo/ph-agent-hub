// =============================================================================
// PH Agent Hub — Login Page
// =============================================================================
// Ant Design Form with username+password; calls auth.ts login();
// on success redirects to /chat; no localStorage usage.
// Shows "Try It Now" button when demo mode is enabled.
// =============================================================================

import React, { useEffect, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Button, Divider, Form, Input, Typography, Alert, Card, Space } from "antd";
import { UserOutlined, LockOutlined, RocketOutlined } from "@ant-design/icons";
import { useAuth } from "../../providers/AuthProvider";
import { Logo } from "../../shared/components/Logo";
import { getDemoStatus } from "../../features/chat/services/demo";

const { Title } = Typography;

interface LoginFormValues {
  username: string;
  password: string;
}

export function LoginPage() {
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [demoEnabled, setDemoEnabled] = useState(false);

  // Check if demo mode is available
  useEffect(() => {
    getDemoStatus().then((status) => {
      setDemoEnabled(status.enabled);
    });
  }, []);

  // If already authenticated, redirect
  React.useEffect(() => {
    if (user) {
      const from = (location.state as { from?: { pathname: string } })?.from
        ?.pathname;
      navigate(from || "/chat", { replace: true });
    }
  }, [user, navigate, location.state]);

  const onFinish = async (values: LoginFormValues) => {
    setError(null);
    setLoading(true);
    try {
      await login(values.username, values.password);
      const from = (location.state as { from?: { pathname: string } })?.from
        ?.pathname;
      navigate(from || "/chat", { replace: true });
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Login failed. Please try again.";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        padding: 16,
      }}
    >
      <Card
        style={{ width: "100%", maxWidth: 400 }}
        styles={{ body: { padding: 32 } }}
      >
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <div style={{ textAlign: "center" }}>
            <div style={{ marginBottom: 12, display: "flex", justifyContent: "center" }}>
              <Logo size={64} />
            </div>
            <Title level={3} style={{ margin: 0 }}>
              PH Agent Hub
            </Title>
            <Typography.Text type="secondary">
              Sign in to your account
            </Typography.Text>
          </div>

          {error && (
            <Alert
              message={error}
              type="error"
              showIcon
              closable
              onClose={() => setError(null)}
            />
          )}

          <Form<LoginFormValues>
            name="login"
            onFinish={onFinish}
            layout="vertical"
            size="large"
            autoComplete="off"
          >
            <Form.Item
              name="username"
              rules={[{ required: true, message: "Please enter your email" }]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="Email"
                autoFocus
                autoComplete="username"
              />
            </Form.Item>

            <Form.Item
              name="password"
              rules={[{ required: true, message: "Please enter your password" }]}
            >
              <Input.Password prefix={<LockOutlined />} placeholder="Password" autoComplete="current-password" />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={loading}
                block
              >
                Sign In
              </Button>
            </Form.Item>
          </Form>

          {demoEnabled && (
            <>
              <Divider plain>or</Divider>
              <Button
                type="default"
                block
                icon={<RocketOutlined />}
                onClick={() => navigate("/demo")}
                size="large"
              >
                Try It Now
              </Button>
              <Typography.Text
                type="secondary"
                style={{ display: "block", textAlign: "center", fontSize: 12 }}
              >
                No account needed. Demo sessions expire after 1 hour.
              </Typography.Text>
            </>
          )}
        </Space>
      </Card>
    </div>
  );
}

export default LoginPage;
