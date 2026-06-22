// =============================================================================
// PH Agent Hub — Admin A2A Server Form
// =============================================================================
// Ant Design Create/Edit Modal+Form; fields for A2A remote agent config.
// =============================================================================

import React from "react";
import {
  Modal,
  Form,
  Input,
  Select,
  Switch,
  message,
} from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createA2aServer,
  updateA2aServer,
  authorizeA2aServer,
  A2aServerData,
} from "../../services/admin";

interface A2aServerFormProps {
  open: boolean;
  server: A2aServerData | null;
  onClose: () => void;
}

export function A2aServerForm({ open, server, onClose }: A2aServerFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!server;

  React.useEffect(() => {
    if (open) {
      if (server) {
        form.setFieldsValue({
          name: server.name,
          url: server.url,
          agent_card_path: server.agent_card_path,
          protocol_binding: server.protocol_binding,
          auth_scheme: server.auth_scheme || "none",
          auth_token: "",
          headers: server.headers
            ? Object.entries(server.headers)
                .map(([k, v]) => `${k}=${v}`)
                .join("\n")
            : "",
          allowed_skills: server.allowed_skills || [],
          enabled: server.enabled,
          // Resilience config
          retry_max_attempts: server.retry_max_attempts,
          retry_backoff_base_seconds: server.retry_backoff_base_seconds,
          retry_backoff_max_seconds: server.retry_backoff_max_seconds,
          timeout_connect_seconds: server.timeout_connect_seconds,
          timeout_read_seconds: server.timeout_read_seconds,
          timeout_stream_seconds: server.timeout_stream_seconds,
          circuit_breaker_threshold: server.circuit_breaker_threshold,
          circuit_breaker_window_seconds: server.circuit_breaker_window_seconds,
          circuit_breaker_cooldown_seconds: server.circuit_breaker_cooldown_seconds,
          // OAuth2 config
          oauth2_client_id: server.oauth2_client_id,
          oauth2_client_secret: "",
          oauth2_authorize_url: server.oauth2_authorize_url,
          oauth2_token_url: server.oauth2_token_url,
          oauth2_scopes: server.oauth2_scopes,
        });
      } else {
        form.setFieldsValue({
          agent_card_path: "/.well-known/agent-card.json",
          protocol_binding: "rest",
          auth_scheme: "none",
          enabled: true,
        });
      }
    }
  }, [open, server, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => createA2aServer(data as any),
    onSuccess: () => {
      message.success("A2A server created");
      queryClient.invalidateQueries({ queryKey: ["admin-a2a-servers"] });
      onClose();
    },
    onError: () => message.error("Failed to create A2A server"),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, unknown> }) =>
      updateA2aServer(id, data as any),
    onSuccess: () => {
      message.success("A2A server updated");
      queryClient.invalidateQueries({ queryKey: ["admin-a2a-servers"] });
      onClose();
    },
    onError: () => message.error("Failed to update A2A server"),
  });

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      // Parse key=value fields
      const headers = parseKeyValueText(values.headers);

      const payload: Record<string, unknown> = {
        name: values.name,
        url: values.url || null,
        agent_card_path: values.agent_card_path || "/.well-known/agent-card.json",
        protocol_binding: values.protocol_binding || "rest",
        auth_scheme: values.auth_scheme || "none",
        auth_token: values.auth_token || null,
        headers: headers,
        allowed_skills: values.allowed_skills || null,
        enabled: values.enabled,
        // Resilience config — only include if non-empty
        ...(values.retry_max_attempts != null && { retry_max_attempts: Number(values.retry_max_attempts) }),
        ...(values.retry_backoff_base_seconds != null && { retry_backoff_base_seconds: Number(values.retry_backoff_base_seconds) }),
        ...(values.retry_backoff_max_seconds != null && { retry_backoff_max_seconds: Number(values.retry_backoff_max_seconds) }),
        ...(values.timeout_connect_seconds != null && { timeout_connect_seconds: Number(values.timeout_connect_seconds) }),
        ...(values.timeout_read_seconds != null && { timeout_read_seconds: Number(values.timeout_read_seconds) }),
        ...(values.timeout_stream_seconds != null && { timeout_stream_seconds: Number(values.timeout_stream_seconds) }),
        ...(values.circuit_breaker_threshold != null && { circuit_breaker_threshold: Number(values.circuit_breaker_threshold) }),
        ...(values.circuit_breaker_window_seconds != null && { circuit_breaker_window_seconds: Number(values.circuit_breaker_window_seconds) }),
        ...(values.circuit_breaker_cooldown_seconds != null && { circuit_breaker_cooldown_seconds: Number(values.circuit_breaker_cooldown_seconds) }),
        // OAuth2 config
        ...(values.auth_scheme === "oauth2" && {
          oauth2_client_id: values.oauth2_client_id || null,
          oauth2_client_secret: values.oauth2_client_secret || null,
          oauth2_authorize_url: values.oauth2_authorize_url || null,
          oauth2_token_url: values.oauth2_token_url || null,
          oauth2_scopes: values.oauth2_scopes || null,
        }),
      };

      if (isEdit) {
        updateMutation.mutate({ id: server!.id, data: payload });
      } else {
        createMutation.mutate(payload);
      }
    } catch {
      // validation failed
    }
  };

  return (
    <Modal
      title={isEdit ? "Edit A2A Server" : "Add A2A Server"}
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
      width={640}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          agent_card_path: "/.well-known/agent-card.json",
          protocol_binding: "rest",
          auth_scheme: "none",
          enabled: true,
        }}
      >
        <Form.Item
          name="name"
          label="Server Name"
          rules={[{ required: true, message: "Please enter a server name" }]}
        >
          <Input placeholder="e.g., Research Assistant Agent" />
        </Form.Item>

        <Form.Item
          name="url"
          label="Base URL"
          rules={[{ required: true, message: "Please enter the agent's base URL" }]}
        >
          <Input placeholder="https://agent.example.com" />
        </Form.Item>

        <Form.Item
          name="agent_card_path"
          label="Agent Card Path"
          tooltip="Path to the Agent Card JSON (default: A2A spec IANA-registered well-known URI)"
        >
          <Input placeholder="/.well-known/agent-card.json" />
        </Form.Item>

        <Form.Item
          name="protocol_binding"
          label="Protocol Binding"
          tooltip="A2A protocol binding used by the remote agent"
          rules={[{ required: true, message: "Please select a protocol binding" }]}
        >
          <Select
            placeholder="Select protocol binding"
            options={[
              { value: "rest", label: "HTTP+JSON/REST" },
              { value: "jsonrpc", label: "JSON-RPC 2.0" },
              { value: "grpc", label: "gRPC" },
            ]}
          />
        </Form.Item>

        <Form.Item
          name="auth_scheme"
          label="Authentication Scheme"
          tooltip="Authentication scheme used by the remote agent"
        >
          <Select
            options={[
              { value: "none", label: "None" },
              { value: "bearer", label: "Bearer Token" },
              { value: "api_key", label: "API Key" },
              { value: "oauth2", label: "OAuth2 (Authorization Code)" },
            ]}
          />
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, curr) => prev.auth_scheme !== curr.auth_scheme}
        >
          {({ getFieldValue }) =>
            getFieldValue("auth_scheme") &&
            getFieldValue("auth_scheme") !== "none" &&
            getFieldValue("auth_scheme") !== "oauth2" ? (
              <Form.Item
                name="auth_token"
                label="Auth Token"
                tooltip="Bearer token or API key for authenticating with the remote agent"
              >
                <Input.Password
                  placeholder="Enter auth token"
                  autoComplete="new-password"
                />
              </Form.Item>
            ) : null
          }
        </Form.Item>

        <Form.Item
          name="headers"
          label="Custom HTTP Headers"
          tooltip="One KEY=VALUE or KEY: VALUE per line"
        >
          <Input.TextArea
            rows={3}
            placeholder={`Authorization=Bearer sk-xxx\nX-API-Key=abc123`}
          />
        </Form.Item>

        <Form.Item
          name="allowed_skills"
          label="Allowed Skills"
          tooltip="Leave empty to allow all skills from this agent. Type skill IDs to restrict."
        >
          <Select
            mode="tags"
            placeholder="Select or type skill IDs"
          />
        </Form.Item>

        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>

        {/* ============================================================= */}
        {/* OAuth2 Configuration (Issue #418)                            */}
        {/* ============================================================= */}
        <Form.Item
          noStyle
          shouldUpdate={(prev, curr) => prev.auth_scheme !== curr.auth_scheme}
        >
          {({ getFieldValue }) =>
            getFieldValue("auth_scheme") === "oauth2" ? (
              <>
                <div style={{ marginTop: 16, marginBottom: 8 }}>
                  <strong>OAuth2 Configuration</strong>
                </div>

                <Form.Item
                  name="oauth2_client_id"
                  label="Client ID"
                  rules={[{ required: !isEdit, message: "Client ID is required for OAuth2" }]}
                >
                  <Input placeholder="your-client-id" />
                </Form.Item>

                <Form.Item
                  name="oauth2_client_secret"
                  label="Client Secret"
                  rules={[{ required: !isEdit, message: "Client secret is required for OAuth2" }]}
                >
                  <Input.Password
                    placeholder={isEdit ? "Leave blank to keep current secret" : "Enter client secret"}
                    autoComplete="new-password"
                  />
                </Form.Item>

                <Form.Item
                  name="oauth2_authorize_url"
                  label="Authorization URL"
                  rules={[{ required: true, message: "Authorization URL is required" }]}
                >
                  <Input placeholder="https://provider.example.com/oauth2/authorize" />
                </Form.Item>

                <Form.Item
                  name="oauth2_token_url"
                  label="Token URL"
                  rules={[{ required: true, message: "Token URL is required" }]}
                >
                  <Input placeholder="https://provider.example.com/oauth2/token" />
                </Form.Item>

                <Form.Item
                  name="oauth2_scopes"
                  label="Scopes"
                  tooltip="Space-separated list of OAuth2 scopes"
                >
                  <Input placeholder="openid profile email" />
                </Form.Item>

                {/* Token status + Authorize button (edit mode only) */}
                {isEdit && server && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ marginBottom: 8 }}>
                      <strong>Token Status: </strong>
                      {server.oauth2_tokens_status === "authorized" ? (
                        <span style={{ color: "#52c41a" }}>✅ Authorized</span>
                      ) : server.oauth2_tokens_status === "expired" ? (
                        <span style={{ color: "#faad14" }}>⚠️ Token expired — re-authorize</span>
                      ) : server.oauth2_tokens_status === "none" ? (
                        <span style={{ color: "#999" }}>Not authorized</span>
                      ) : null}
                    </div>

                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          const result = await authorizeA2aServer(server.id);
                          window.open(result.authorization_url, "_blank");
                        } catch {
                          // error handled by API interceptor
                        }
                      }}
                      style={{
                        padding: "4px 16px",
                        background: "#1677ff",
                        color: "#fff",
                        border: "none",
                        borderRadius: 4,
                        cursor: "pointer",
                        marginRight: 8,
                      }}
                    >
                      {server.oauth2_tokens_status === "expired"
                        ? "Re-authorize"
                        : "Authorize"}
                    </button>

                    {(server.oauth2_tokens_status === "authorized" ||
                      server.oauth2_tokens_status === "expired") && (
                      <button
                        type="button"
                        onClick={async () => {
                          try {
                            await updateA2aServer(server.id, {
                              oauth2_tokens: null as any,
                            } as any);
                            message.success("Tokens revoked");
                            onClose();
                          } catch {
                            // error handled by API interceptor
                          }
                        }}
                        style={{
                          padding: "4px 16px",
                          background: "#ff4d4f",
                          color: "#fff",
                          border: "none",
                          borderRadius: 4,
                          cursor: "pointer",
                        }}
                      >
                        Revoke
                      </button>
                    )}

                    <div style={{ fontSize: 12, color: "#888", marginTop: 8 }}>
                      Callback URL: <code>{window.location.origin}/api/a2a/oauth2/callback</code>
                    </div>
                  </div>
                )}
              </>
            ) : null
          }
        </Form.Item>

        {/* ============================================================= */}
        {/* Advanced / Resilience Configuration (Issue #409)              */}
        {/* ============================================================= */}
        <details style={{ marginTop: 16 }}>
          <summary style={{ cursor: "pointer", fontWeight: 600, color: "#666" }}>
            Advanced / Resilience
          </summary>
          <div style={{ padding: "12px 0" }}>
            <Form.Item
              name="retry_max_attempts"
              label="Max Retry Attempts"
              tooltip="Number of times to retry on transient errors (e.g., timeout, 5xx). Default: 3"
            >
              <Input type="number" min={0} max={20} placeholder="3" />
            </Form.Item>
            <Form.Item
              name="retry_backoff_base_seconds"
              label="Retry Backoff Base (s)"
              tooltip="Base seconds for exponential backoff. Formula: base * 2^attempt. Default: 1"
            >
              <Input type="number" min={0.1} step={0.1} placeholder="1.0" />
            </Form.Item>
            <Form.Item
              name="retry_backoff_max_seconds"
              label="Retry Backoff Max (s)"
              tooltip="Maximum seconds between retries. Default: 60"
            >
              <Input type="number" min={1} step={1} placeholder="60" />
            </Form.Item>

            <Form.Item
              name="timeout_connect_seconds"
              label="Connect Timeout (s)"
              tooltip="HTTP connection timeout in seconds. Default: 30"
            >
              <Input type="number" min={1} step={1} placeholder="30" />
            </Form.Item>
            <Form.Item
              name="timeout_read_seconds"
              label="Read Timeout (s)"
              tooltip="HTTP read timeout for non-streaming calls. Default: 300"
            >
              <Input type="number" min={1} step={5} placeholder="300" />
            </Form.Item>
            <Form.Item
              name="timeout_stream_seconds"
              label="Stream Timeout (s)"
              tooltip="HTTP read timeout for streaming calls. Default: 600"
            >
              <Input type="number" min={1} step={10} placeholder="600" />
            </Form.Item>

            <Form.Item
              name="circuit_breaker_threshold"
              label="Circuit Breaker Threshold"
              tooltip="Consecutive failures to trip the circuit breaker. Default: 5"
            >
              <Input type="number" min={1} max={100} placeholder="5" />
            </Form.Item>
            <Form.Item
              name="circuit_breaker_window_seconds"
              label="Circuit Breaker Window (s)"
              tooltip="Time window to reset the failure count. Default: 60"
            >
              <Input type="number" min={1} step={5} placeholder="60" />
            </Form.Item>
            <Form.Item
              name="circuit_breaker_cooldown_seconds"
              label="Circuit Breaker Cooldown (s)"
              tooltip="Cooldown period before a probe attempt is allowed. Default: 300"
            >
              <Input type="number" min={1} step={10} placeholder="300" />
            </Form.Item>
          </div>
        </details>
      </Form>
    </Modal>
  );
}

function parseKeyValueText(text: string | undefined): Record<string, string> | null {
  if (!text || !text.trim()) return null;
  const result: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // Try = separator first, then : separator
    let sepIndex = trimmed.indexOf("=");
    if (sepIndex <= 0) {
      sepIndex = trimmed.indexOf(":");
    }

    if (sepIndex > 0) {
      const key = trimmed.slice(0, sepIndex).trim();
      const value = trimmed.slice(sepIndex + 1).trim();
      if (key) result[key] = value;
    }
  }
  return Object.keys(result).length > 0 ? result : null;
}
