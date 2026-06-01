// =============================================================================
// PH Agent Hub — Admin MCP Server Form
// =============================================================================
// Ant Design Create/Edit Modal+Form; dynamic fields per transport type.
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createMcpServer,
  updateMcpServer,
  listTools,
  McpServerData,
  ToolData,
} from "../../services/admin";

interface McpServerFormProps {
  open: boolean;
  server: McpServerData | null;
  onClose: () => void;
}

export function McpServerForm({ open, server, onClose }: McpServerFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!server;

  React.useEffect(() => {
    if (open) {
      if (server) {
        form.setFieldsValue({
          name: server.name,
          transport: server.transport,
          url: server.url,
          command: server.command,
          args: server.args?.join("\n") || "",
          env_vars: server.env_vars
            ? Object.entries(server.env_vars)
                .map(([k, v]) => `${k}=${v}`)
                .join("\n")
            : "",
          headers: server.headers
            ? Object.entries(server.headers)
                .map(([k, v]) => `${k}=${v}`)
                .join("\n")
            : "",
          allowed_tools: server.allowed_tools || [],
          enabled: server.enabled,
        });
      } else {
        form.resetFields();
      }
    }
  }, [open, server, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => createMcpServer(data as any),
    onSuccess: () => {
      message.success("MCP server created");
      queryClient.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
      onClose();
    },
    onError: () => message.error("Failed to create MCP server"),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, unknown> }) =>
      updateMcpServer(id, data as any),
    onSuccess: () => {
      message.success("MCP server updated");
      queryClient.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
      onClose();
    },
    onError: () => message.error("Failed to update MCP server"),
  });

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      // Parse key=value fields
      const envVars = parseKeyValueText(values.env_vars);
      const headers = parseKeyValueText(values.headers);
      const args = values.args
        ? values.args.split("\n").filter((l: string) => l.trim())
        : [];

      const payload = {
        name: values.name,
        transport: values.transport,
        url: values.url || null,
        command: values.command || null,
        args: args.length > 0 ? args : null,
        env_vars: envVars,
        headers: headers,
        allowed_tools: values.allowed_tools || null,
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

  // Fetch synced MCP tools for this server to show as allowed_tools options
  const { data: mcpToolsData } = useQuery({
    queryKey: ["admin-tools", "mcp", server?.id],
    queryFn: () => listTools({ type: "mcp", page_size: 100 }),
    enabled: isEdit && !!server?.id,
  });

  const mcpToolOptions = React.useMemo(() => {
    if (!mcpToolsData?.items || !server?.id) return [];
    return mcpToolsData.items
      .filter((t) => t.config?.mcp_server_id === server.id)
      .map((t) => ({
        value: t.config?.tool_name as string,
        label: t.config?.tool_name as string,
      }))
      .filter((o) => o.value);
  }, [mcpToolsData, server?.id]);

  const transport = Form.useWatch("transport", form);

  return (
    <Modal
      title={isEdit ? "Edit MCP Server" : "Add MCP Server"}
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
        initialValues={{ enabled: true }}
      >
        <Form.Item
          name="name"
          label="Server Name"
          rules={[{ required: true, message: "Please enter a server name" }]}
        >
          <Input placeholder="e.g., GitHub MCP" />
        </Form.Item>

        <Form.Item
          name="transport"
          label="Transport"
          rules={[{ required: true, message: "Please select a transport" }]}
        >
          <Select
            placeholder="Select transport type"
            options={[
              { value: "streamable_http", label: "Streamable HTTP" },
              { value: "stdio", label: "Stdio (subprocess)" },
              { value: "websocket", label: "WebSocket" },
            ]}
          />
        </Form.Item>

        {(transport === "streamable_http" || transport === "websocket") && (
          <Form.Item
            name="url"
            label="Server URL"
            rules={
              transport
                ? [{ required: true, message: "URL is required for this transport" }]
                : []
            }
          >
            <Input placeholder="https://example.com/mcp" />
          </Form.Item>
        )}

        {transport === "stdio" && (
          <>
            <Form.Item
              name="command"
              label="Command"
              rules={[{ required: true, message: "Command is required for stdio transport" }]}
            >
              <Input placeholder="e.g., npx, uvx, python" />
            </Form.Item>

            <Form.Item
              name="args"
              label="Arguments (one per line)"
            >
              <Input.TextArea
                rows={3}
                placeholder={`-y\n@modelcontextprotocol/server-github`}
              />
            </Form.Item>
          </>
        )}

        <Form.Item
          name="env_vars"
          label="Environment Variables"
          tooltip="One KEY=VALUE or KEY: VALUE per line"
        >
          <Input.TextArea
            rows={3}
            placeholder={`GITHUB_TOKEN=ghp_xxx\nNODE_ENV=production`}
          />
        </Form.Item>

        <Form.Item
          name="headers"
          label="HTTP Headers"
          tooltip="One KEY=VALUE or KEY: VALUE per line (for HTTP auth)"
        >
          <Input.TextArea
            rows={3}
            placeholder={`Authorization=Bearer sk-xxx\nX-API-Key=abc123`}
          />
        </Form.Item>

        <Form.Item
          name="allowed_tools"
          label="Allowed Tools"
          tooltip="Leave empty to allow all tools from this server. Select or type tool names to restrict."
        >
          <Select
            mode="tags"
            placeholder="Select or type tool names"
            options={mcpToolOptions}
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

    // Try = separator first, then : separator (supports both formats)
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
