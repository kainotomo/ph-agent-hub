// =============================================================================
// PH Agent Hub — Admin EmbedConfigForm
// =============================================================================
// Modal form for creating / editing an embed widget configuration.
// Shows the generated guest token on create.
// =============================================================================

import { useState, useEffect } from "react";
import {
  Modal,
  Form,
  Input,
  Switch,
  Select,
  Divider,
  Typography,
  message,
  Space,
} from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  createEmbedConfig,
  updateEmbedConfig,
  EmbedConfigData,
  EmbedConfigCreate,
  listModels,
  listSkills,
  listTemplates,
  listTenants,
} from "../../services/admin";

const { Text } = Typography;

interface EmbedConfigFormProps {
  open: boolean;
  config: EmbedConfigData | null;
  onClose: () => void;
  onSuccess: () => void;
}

export function EmbedConfigForm({ open, config, onClose, onSuccess }: EmbedConfigFormProps) {
  const [form] = Form.useForm();
  const [newToken, setNewToken] = useState<string | null>(null);
  const isEditing = !!config;

  // Fetch available models, skills, templates for selectors
  const { data: models } = useQuery({
    queryKey: ["admin-models-selector"],
    queryFn: () => listModels({ page_size: 200 }),
    enabled: open,
  });
  const { data: skills } = useQuery({
    queryKey: ["admin-skills-selector"],
    queryFn: () => listSkills({ page_size: 200 }),
    enabled: open,
  });
  const { data: templates } = useQuery({
    queryKey: ["admin-templates-selector"],
    queryFn: () => listTemplates({ page_size: 200 }),
    enabled: open,
  });

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-embed-selector"],
    queryFn: () => listTenants(),
    enabled: open,
  });

  const tenantNameById = new Map((tenants?.items || []).map((t: { id: string; name: string }) => [t.id, t.name]));

  // Reset form when opening/closing
  useEffect(() => {
    if (open) {
      if (config) {
        form.setFieldsValue({
          name: config.name,
          allowed_origins: config.allowed_origins || "",
          is_active: config.is_active,
          default_model_id: config.default_model_id || undefined,
          default_skill_id: config.default_skill_id || undefined,
          default_template_id: config.default_template_id || undefined,
          primary_color: config.theme?.primary_color || "#1677ff",
          logo_url: config.theme?.logo_url || "",
          greeting_text: config.theme?.greeting_text || "",
          position: config.theme?.position || "bubble",
          file_upload: config.feature_flags?.file_upload ?? false,
          follow_up_questions: config.feature_flags?.follow_up_questions ?? true,
        });
      } else {
        form.resetFields();
        form.setFieldsValue({
          is_active: true,
          allowed_origins: "",
          primary_color: "#1677ff",
          logo_url: "",
          greeting_text: "Hi! How can I help?",
          position: "bubble",
          file_upload: false,
          follow_up_questions: true,
        });
      }
      setNewToken(null);
    }
  }, [open, config, form]);

  const createMutation = useMutation({
    mutationFn: (data: EmbedConfigCreate) => createEmbedConfig(data),
    onSuccess: (result) => {
      setNewToken(result.guest_token || null);
      message.success("Embed config created! Copy the snippet below.");
      onSuccess();
    },
    onError: (err: Error) => message.error(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<EmbedConfigCreate> }) =>
      updateEmbedConfig(id, data),
    onSuccess: () => {
      message.success("Embed config updated");
      onSuccess();
    },
    onError: (err: Error) => message.error(err.message),
  });

  const handleSubmit = async () => {
    const values = await form.validateFields();

    const theme = {
      primary_color: values.primary_color || "#1677ff",
      logo_url: values.logo_url || "",
      greeting_text: values.greeting_text || "",
      position: values.position || "bubble",
    };

    const feature_flags = {
      file_upload: !!values.file_upload,
      follow_up_questions: !!values.follow_up_questions,
    };

    const payload = {
      name: values.name,
      allowed_origins: values.allowed_origins || null,
      theme,
      feature_flags,
      default_model_id: values.default_model_id || null,
      default_skill_id: values.default_skill_id || null,
      default_template_id: values.default_template_id || null,
    };

    if (isEditing && config) {
      updateMutation.mutate({ id: config.id, data: payload });
    } else {
      createMutation.mutate(payload);
    }
  };

  // Generate the embed snippet
  const snippetToken = newToken || config?.guest_token || "";
  const embedSnippet = snippetToken
    ? `<script src="/embed.js" data-ph-token="${snippetToken}"></script>`
    : null;

  return (
    <Modal
      title={isEditing ? "Edit Embed Config" : "New Embed Config"}
      open={open}
      onOk={handleSubmit}
      onCancel={onClose}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
      width={640}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item name="name" label="Name" rules={[{ required: true, message: "Name is required" }]}>
          <Input placeholder="e.g. Support Widget for example.com" />
        </Form.Item>

        <Form.Item name="allowed_origins" label="Allowed Origins (comma-separated)">
          <Input placeholder="https://example.com,https://app.example.com" />
        </Form.Item>

        <Form.Item name="is_active" label="Active" valuePropName="checked">
          <Switch />
        </Form.Item>

        <Divider orientation="left" plain>Theme</Divider>

        <Space size="large" wrap>
          <Form.Item name="primary_color" label="Primary Color">
            <Input placeholder="#1677ff" style={{ width: 150 }} />
          </Form.Item>
          <Form.Item name="position" label="Widget Position">
            <Select style={{ width: 150 }}>
              <Select.Option value="bubble">Bubble (floating)</Select.Option>
              <Select.Option value="inline">Inline</Select.Option>
            </Select>
          </Form.Item>
        </Space>

        <Form.Item name="logo_url" label="Logo URL">
          <Input placeholder="https://example.com/logo.svg" />
        </Form.Item>

        <Form.Item name="greeting_text" label="Greeting Text">
          <Input placeholder="Hi! How can I help?" />
        </Form.Item>

        <Divider orientation="left" plain>Default Selections</Divider>

        <Space size="large" wrap>
          <Form.Item name="default_model_id" label="Default Model">
            <Select allowClear style={{ width: 240 }} placeholder="Tenant default">
              {(models?.items || []).map((m) => (
                <Select.Option key={m.id} value={m.id}>
                  {m.name}
                  {tenantNameById.has(m.tenant_id)
                    ? ` (${tenantNameById.get(m.tenant_id)})`
                    : ""}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="default_skill_id" label="Default Skill">
            <Select allowClear style={{ width: 200 }} placeholder="None">
              {(skills?.items || []).map((s) => (
                <Select.Option key={s.id} value={s.id}>
                  {s.title}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="default_template_id" label="Default Template">
            <Select allowClear style={{ width: 200 }} placeholder="None">
              {(templates?.items || []).map((t) => (
                <Select.Option key={t.id} value={t.id}>
                  {t.title}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
        </Space>

        <Divider orientation="left" plain>Features</Divider>

        <Space size="large" wrap>
          <Form.Item name="file_upload" label="File Upload" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="follow_up_questions" label="Follow-up Questions" valuePropName="checked">
            <Switch defaultChecked />
          </Form.Item>
        </Space>

        {/* Show embed snippet after creation */}
        {embedSnippet && (
          <>
            <Divider orientation="left" plain>Embed Snippet</Divider>
            <div
              style={{
                background: "#f5f5f5",
                padding: 12,
                borderRadius: 6,
                border: "1px solid #d9d9d9",
              }}
            >
              <Text code copyable style={{ fontSize: 12, wordBreak: "break-all" }}>
                {embedSnippet}
              </Text>
              <div style={{ marginTop: 8 }}>
                <Text type="warning" style={{ fontSize: 12 }}>
                  ⚠️ Copy this token now — it won't be shown again after closing.
                </Text>
              </div>
            </div>
          </>
        )}
      </Form>
    </Modal>
  );
}

export default EmbedConfigForm;
