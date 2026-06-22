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
            ]}
          />
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, curr) => prev.auth_scheme !== curr.auth_scheme}
        >
          {({ getFieldValue }) =>
            getFieldValue("auth_scheme") &&
            getFieldValue("auth_scheme") !== "none" ? (
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
