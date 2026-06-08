// =============================================================================
// PH Agent Hub — Admin ToolForm
// =============================================================================
// Ant Design Create/Edit Modal+Form; dynamic fields per tool type.
// Includes a CodeMirror-based Python editor for the "custom" type.
// =============================================================================

import React from "react";
import {
  Modal,
  Form,
  Input,
  Select,
  Switch,
  message,
  Typography,
} from "antd";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import { createTool, updateTool, listTenants, ToolData, TenantData } from "../../services/admin";
import { useAuth } from "../../../../providers/AuthProvider";
import { CodeEditor } from "../../../../shared/components/CodeEditor";

const { TextArea } = Input;
const { Text } = Typography;

interface ToolFormProps {
  open: boolean;
  tool: ToolData | null;
  duplicateFrom?: ToolData | null;
  onClose: () => void;
}

const DEFAULT_CODE_TEMPLATE = `async def execute(**kwargs) -> dict:
    """Custom tool logic.

    Args:
        **kwargs: Arguments passed by the LLM based on your function signature.

    Returns:
        A dict with the result.
    """
    # Your code here — use httpx, json, datetime, etc.
    return {"result": "Hello from custom tool!", "input": kwargs}
`;

export function ToolForm({ open, tool, duplicateFrom, onClose }: ToolFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!tool && !duplicateFrom;
  const [toolType, setToolType] = React.useState(tool?.type ?? duplicateFrom?.type ?? "custom");
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => listTenants(),
    select: (data) => data?.items || [],
    enabled: open && isAdmin,
  });

  React.useEffect(() => {
    if (open) {
      if (duplicateFrom) {
        const fields: Record<string, unknown> = {
          tenant_id: duplicateFrom.tenant_id,
          name: duplicateFrom.name,
          type: duplicateFrom.type,
          enabled: duplicateFrom.enabled,
          is_public: duplicateFrom.is_public,
        };
        if (duplicateFrom.type === "erpnext" && duplicateFrom.config) {
          fields.erpnext_base_url = duplicateFrom.config.base_url || "";
          fields.erpnext_api_key = duplicateFrom.config.api_key || "";
          fields.erpnext_api_secret = duplicateFrom.config.api_secret || "";
        }
        if (duplicateFrom.type === "sql_query" && duplicateFrom.config) {
          fields.sql_connection_string = duplicateFrom.config.connection_string || "";
          fields.sql_row_limit = duplicateFrom.config.row_limit || 1000;
        }
        if (duplicateFrom.type === "document_generation" && duplicateFrom.config) {
          fields.doc_company_logo_url = duplicateFrom.config.company_logo_url || "";
        }
        if (duplicateFrom.type === "custom") {
          fields.config_json = duplicateFrom.config
            ? JSON.stringify(duplicateFrom.config, null, 2)
            : "";
          fields.code = duplicateFrom.code || "";
        }
        form.setFieldsValue(fields);
        setToolType(duplicateFrom.type);
      } else if (tool) {
        const fields: Record<string, unknown> = {
          tenant_id: tool.tenant_id,
          name: tool.name,
          type: tool.type,
          enabled: tool.enabled,
          is_public: tool.is_public,
        };
        // Populate structured ERPNext fields from config
        if (tool.type === "erpnext" && tool.config) {
          fields.erpnext_base_url = tool.config.base_url || "";
          fields.erpnext_api_key = tool.config.api_key || "";
          fields.erpnext_api_secret = tool.config.api_secret || "";
        }
        // Populate sql_query fields from config
        if (tool.type === "sql_query" && tool.config) {
          fields.sql_connection_string = tool.config.connection_string || "";
          fields.sql_row_limit = tool.config.row_limit || 1000;
        }
        // Populate document_generation fields from config
        if (tool.type === "document_generation" && tool.config) {
          fields.doc_company_logo_url = tool.config.company_logo_url || "";
        }
        // Populate config_json and code for custom type
        if (tool.type === "custom") {
          fields.config_json = tool.config
            ? JSON.stringify(tool.config, null, 2)
            : "";
          fields.code = tool.code || "";
        }
        form.setFieldsValue(fields);
        setToolType(tool.type);
      } else {
        form.resetFields();
        form.setFieldsValue({
          type: "custom",
          enabled: true,
          is_public: false,
          code: DEFAULT_CODE_TEMPLATE,
        });
        setToolType("custom");
      }
    }
  }, [open, tool, duplicateFrom, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => {
      // Assemble config from structured fields for erpnext
      if (data.type === "erpnext") {
        data.config = {
          base_url: data.erpnext_base_url || "",
          api_key: data.erpnext_api_key || "",
          api_secret: data.erpnext_api_secret || "",
        };
        delete data.erpnext_base_url;
        delete data.erpnext_api_key;
        delete data.erpnext_api_secret;
      } else if (data.type === "sql_query") {
        data.config = {
          connection_string: data.sql_connection_string || "",
          row_limit: data.sql_row_limit ? parseInt(data.sql_row_limit as string, 10) : 1000,
        };
        delete data.sql_connection_string;
        delete data.sql_row_limit;
      } else if (data.type === "document_generation") {
        data.config = {
          company_logo_url: data.doc_company_logo_url || "",
        };
        delete data.doc_company_logo_url;
      } else if (typeof data.config_json === "string" && data.config_json.trim()) {
        try {
          data.config = JSON.parse(data.config_json as string);
        } catch {
          message.error("Invalid JSON in config");
          throw new Error("Invalid JSON");
        }
      }
      delete data.config_json;
      return createTool(data as unknown as Partial<ToolData>);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tools"] });
      message.success("Tool created");
      onClose();
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to create tool");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Record<string, unknown>;
    }) => {
      // Assemble config from structured fields for erpnext
      if (data.type === "erpnext") {
        data.config = {
          base_url: data.erpnext_base_url || "",
          api_key: data.erpnext_api_key || "",
          api_secret: data.erpnext_api_secret || "",
        };
        delete data.erpnext_base_url;
        delete data.erpnext_api_key;
        delete data.erpnext_api_secret;
      } else if (data.type === "sql_query") {
        data.config = {
          connection_string: data.sql_connection_string || "",
          row_limit: data.sql_row_limit ? parseInt(data.sql_row_limit as string, 10) : 1000,
        };
        delete data.sql_connection_string;
        delete data.sql_row_limit;
      } else if (data.type === "document_generation") {
        data.config = {
          company_logo_url: data.doc_company_logo_url || "",
        };
        delete data.doc_company_logo_url;
      } else if (typeof data.config_json === "string" && data.config_json.trim()) {
        try {
          data.config = JSON.parse(data.config_json as string);
        } catch {
          message.error("Invalid JSON in config");
          throw new Error("Invalid JSON");
        }
      }
      delete data.config_json;
      return updateTool(id, data as unknown as Partial<ToolData>);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tools"] });
      message.success("Tool updated");
      onClose();
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to update tool");
    },
  });

  return (
    <Modal
      title={duplicateFrom ? "Duplicate Tool" : isEdit ? "Edit Tool" : "Create Tool"}
      open={open}
      onOk={async () => {
        const values = await form.validateFields();
        if (isEdit) {
          await updateMutation.mutateAsync({
            id: tool!.id,
            data: values,
          });
        } else {
          await createMutation.mutateAsync(values);
        }
      }}
      onCancel={onClose}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
      width={720}
    >
      <Form form={form} layout="vertical">
        {isAdmin && (
          <Form.Item
            name="tenant_id"
            label="Tenant"
            extra="Leave empty to create in your own tenant"
          >
            <Select
              allowClear
              placeholder="Select tenant (optional)"
              options={(tenants || []).map((t: TenantData) => ({
                label: t.name,
                value: t.id,
              }))}
            />
          </Form.Item>
        )}
        <Form.Item name="name" label="Name" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item
          name="type"
          label="Type"
          rules={[{ required: true }]}
        >
          <Select
            options={[
              { label: "Browser", value: "browser" },
              { label: "Calculator", value: "calculator" },
              { label: "Calendar", value: "calendar" },
              { label: "Code Interpreter", value: "code_interpreter" },
              { label: "Currency Exchange", value: "currency_exchange" },
              { label: "Custom", value: "custom" },
              { label: "Datetime", value: "datetime" },
              { label: "Document Generation", value: "document_generation" },
              { label: "Email", value: "email" },
              { label: "ERPNext", value: "erpnext" },
              { label: "ETF Data", value: "etf_data" },
              { label: "Fetch URL", value: "fetch_url" },
              { label: "GitHub", value: "github" },
              { label: "Image Generation", value: "image_generation" },
              { label: "Market Overview", value: "market_overview" },
              { label: "Membrane", value: "membrane" },
              { label: "PDF Extractor", value: "pdf_extractor" },
              { label: "Portfolio", value: "portfolio" },
              { label: "RAG Search", value: "rag_search" },
              { label: "RSS Feed", value: "rss_feed" },
              { label: "SEC Filings", value: "sec_filings" },
              { label: "Slack", value: "slack" },
              { label: "SQL Query", value: "sql_query" },
              { label: "Stock Data", value: "stock_data" },
              { label: "Tasks", value: "tasks" },
              { label: "Weather", value: "weather" },
              { label: "Web Search", value: "web_search" },
              { label: "Wikipedia", value: "wikipedia" },
            ]}
            onChange={(value) => setToolType(value)}
          />
        </Form.Item>

        {/* ERPNext-specific structured fields */}
        {toolType === "erpnext" && (
          <>
            <Form.Item
              name="erpnext_base_url"
              label="Base URL"
              rules={[{ required: true, message: "ERPNext site URL is required" }]}
            >
              <Input placeholder="https://erp.example.com" />
            </Form.Item>
            <Form.Item
              name="erpnext_api_key"
              label="API Key"
              rules={[{ required: true, message: "API Key is required" }]}
            >
              <Input placeholder="Your ERPNext API key" />
            </Form.Item>
            <Form.Item
              name="erpnext_api_secret"
              label="API Secret"
              rules={[{ required: true, message: "API Secret is required" }]}
            >
              <Input.Password placeholder="Your ERPNext API secret" />
            </Form.Item>
          </>
        )}

        {/* SQL Query-specific structured fields */}
        {toolType === "sql_query" && (
          <>
            <Form.Item
              name="sql_connection_string"
              label="Connection String"
              rules={[{ required: true, message: "Database connection string is required" }]}
              extra="Supports postgresql://, mysql://, or mariadb:// schemes"
            >
              <Input placeholder="mysql://user:pass@host:3306/dbname" />
            </Form.Item>
            <Form.Item
              name="sql_row_limit"
              label="Row Limit"
              extra="Maximum number of rows returned per query (default 1000)"
            >
              <Input type="number" placeholder="1000" />
            </Form.Item>
          </>
        )}

        {/* Document Generation-specific structured fields */}
        {toolType === "document_generation" && (
          <>
            <Form.Item
              name="doc_company_logo_url"
              label="Company Logo URL"
              extra="Optional URL to a logo image for PDF headers"
            >
              <Input placeholder="https://example.com/logo.png" />
            </Form.Item>
          </>
        )}

        {/* Generic config JSON for tools without structured fields */}
        {!["erpnext", "sql_query", "document_generation", "custom"].includes(toolType) && (
          <Form.Item
            name="config_json"
            label="Configuration (JSON)"
            extra="Optional JSON configuration for this tool (e.g., API keys, endpoints, options)"
          >
            <TextArea
              rows={6}
              placeholder='{"api_key": "...", "provider": "openai"}'
            />
          </Form.Item>
        )}

        {/* Custom type: code editor + config JSON */}
        {toolType === "custom" && (
          <>
            <Form.Item
              name="code"
              label="Python Code"
              rules={[{ required: true, message: "Code is required for custom tools" }]}
              extra={
                <Text type="secondary">
                  Define an <Text code>async def execute(**kwargs) -&gt; dict</Text> function.
                  Available modules: httpx, json, datetime, re, math, hashlib, base64, uuid,
                  urllib.parse, asyncio, collections, itertools, textwrap, html, csv, io,
                  typing, enum, random, statistics.
                </Text>
              }
              getValueFromEvent={(val: string) => val}
            >
              <CodeEditor height="300px" />
            </Form.Item>
            <Form.Item name="config_json" label="Config (JSON)">
              <TextArea
                rows={4}
                placeholder='{"key": "value"}'
              />
            </Form.Item>
          </>
        )}

        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item
          name="is_public"
          label="Public"
          valuePropName="checked"
          tooltip="When enabled, all tenant users can use this tool regardless of group membership"
        >
          <Switch />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default ToolForm;
