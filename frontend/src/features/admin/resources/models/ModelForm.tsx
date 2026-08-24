// =============================================================================
// PH Agent Hub — Admin ModelForm
// =============================================================================
// Ant Design Create/Edit Modal+Form; api_key input type=password.
// =============================================================================

import React from "react";
import {
  Modal,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  message,
  Spin,
} from "antd";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import {
  createModel,
  updateModel,
  listModelGroups,
  assignModelToGroup,
  removeModelFromGroup,
  listGroups,
  listTenants,
  ModelData,
  GroupData,
  TenantData,
} from "../../services/admin";
import { useAuth } from "../../../../providers/AuthProvider";

interface ModelFormProps {
  open: boolean;
  model: ModelData | null;
  duplicateFrom?: ModelData | null;
  onClose: () => void;
}

export function ModelForm({ open, model, duplicateFrom, onClose }: ModelFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!model && !duplicateFrom;
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  // Groups state
  const [initialGroupIds, setInitialGroupIds] = React.useState<string[]>([]);
  const [isPublic, setIsPublic] = React.useState(model?.is_public ?? false);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => listTenants(),
    select: (data) => data?.items || [],
    enabled: open && isAdmin,
  });

  const { data: allGroups } = useQuery({
    queryKey: ["admin-groups"],
    queryFn: () => listGroups().then(r => r.items),
    enabled: open,
  });

  const { data: modelGroups, isLoading: groupsLoading } = useQuery({
    queryKey: ["admin-model-groups", model?.id],
    queryFn: () => listModelGroups(model!.id),
    enabled: open && isEdit,
  });

  React.useEffect(() => {
    if (open && isEdit && modelGroups) {
      const ids = modelGroups.map((g: GroupData) => g.id);
      setInitialGroupIds(ids);
      form.setFieldsValue({
        groups: ids,
        is_public: model?.is_public ?? false,
      });
    }
  }, [open, isEdit, modelGroups, model, form]);

  const syncGroups = async (modelId: string) => {
    const currentGroupIds: string[] = form.getFieldValue("groups") || [];
    const toAdd = currentGroupIds.filter((id) => !initialGroupIds.includes(id));
    const toRemove = initialGroupIds.filter((id) => !currentGroupIds.includes(id));

    for (const gid of toAdd) {
      await assignModelToGroup(gid, modelId);
    }
    for (const gid of toRemove) {
      await removeModelFromGroup(gid, modelId);
    }
  };

  React.useEffect(() => {
    if (open) {
      if (duplicateFrom) {
        form.setFieldsValue({
          tenant_id: duplicateFrom.tenant_id,
          name: duplicateFrom.name,
          model_id: duplicateFrom.model_id,
          provider: duplicateFrom.provider,
          base_url: duplicateFrom.base_url,
          enabled: duplicateFrom.enabled,
          is_public: duplicateFrom.is_public,
          max_tokens: duplicateFrom.max_tokens,
          temperature: duplicateFrom.temperature,
          thinking_enabled: duplicateFrom.thinking_enabled,
          reasoning_effort: duplicateFrom.reasoning_effort,
          follow_up_questions_enabled: duplicateFrom.follow_up_questions_enabled,
          auto_route_eligible: duplicateFrom.auto_route_eligible,
          context_length: duplicateFrom.context_length,
          input_price_per_1m: duplicateFrom.input_price_per_1m,
          output_price_per_1m: duplicateFrom.output_price_per_1m,
          cache_hit_price_per_1m: duplicateFrom.cache_hit_price_per_1m,
          api_key: "",
        });
        setIsPublic(duplicateFrom.is_public);
        setInitialGroupIds([]);
      } else if (model) {
        form.setFieldsValue({
          tenant_id: model.tenant_id,
          name: model.name,
          model_id: model.model_id,
          provider: model.provider,
          base_url: model.base_url,
          enabled: model.enabled,
          is_public: model.is_public,
          max_tokens: model.max_tokens,
          temperature: model.temperature,

          thinking_enabled: model.thinking_enabled,
          reasoning_effort: model.reasoning_effort,
          follow_up_questions_enabled: model.follow_up_questions_enabled,
          auto_route_eligible: model.auto_route_eligible,
          context_length: model.context_length,
          input_price_per_1m: model.input_price_per_1m,
          output_price_per_1m: model.output_price_per_1m,
          cache_hit_price_per_1m: model.cache_hit_price_per_1m,
        });
        setIsPublic(model.is_public);
      } else {
        form.resetFields();
        form.setFieldsValue({
          enabled: true,
          is_public: false,
          max_tokens: 4096,
          temperature: 0.7,
          api_key: "",
          thinking_enabled: false,
          reasoning_effort: "high",
          follow_up_questions_enabled: false,
          auto_route_eligible: true,
          context_length: undefined,
          input_price_per_1m: undefined,
          output_price_per_1m: undefined,
          cache_hit_price_per_1m: undefined,
        });
        setIsPublic(false);
        setInitialGroupIds([]);
      }
    }
  }, [open, model, duplicateFrom, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      createModel(data as Partial<ModelData> & { api_key: string }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
      message.success("Model created");
      onClose();
    },
    onError: (err: Error) => {
      message.error(err.message || "Failed to create model");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Record<string, unknown>;
    }) => updateModel(id, data as Partial<ModelData> & { api_key?: string }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
      message.success("Model updated");
      onClose();
    },
    onError: (err: Error) => {
      message.error(err.message || "Failed to update model");
    },
  });

  return (
    <Modal
      title={duplicateFrom ? "Duplicate Model" : isEdit ? "Edit Model" : "Add Model"}
      open={open}
      onOk={async () => {
        const values = await form.validateFields();
        if (isEdit) {
          await updateMutation.mutateAsync({
            id: model!.id,
            data: values,
          });
          await syncGroups(model!.id);
        } else {
          const created = await createMutation.mutateAsync(values);
          // Sync groups for new model
          const selectedGroupIds: string[] = values.groups || [];
          if (selectedGroupIds.length > 0 && created) {
            for (const gid of selectedGroupIds) {
              await assignModelToGroup(gid, created.id);
            }
          }
        }
      }}
      onCancel={onClose}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
      width={520}
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
          <Input placeholder="Display name (e.g., Production DeepSeek)" />
        </Form.Item>
        <Form.Item name="model_id" label="Model ID" rules={[{ required: true }]}>
          <Input placeholder="API model name (e.g., deepseek-v4-flash)" />
        </Form.Item>
        <Form.Item
          name="provider"
          label="Provider"
          rules={[{ required: true }]}
        >
          <Select
            options={[
              { label: "OpenAI", value: "openai" },
              { label: "Anthropic", value: "anthropic" },
              { label: "DeepSeek", value: "deepseek" },
              { label: "Ollama", value: "ollama" },
            ]}
          />
        </Form.Item>
        <Form.Item shouldUpdate noStyle>
          {() => {
            const provider = form.getFieldValue("provider");
            const isOllama = provider === "ollama";
            return (
              <Form.Item name="api_key" label={isOllama ? "API Key (optional)" : "API Key"}>
                <Input.Password
                  placeholder={
                    isOllama
                      ? "Not required for local models"
                      : isEdit
                        ? "Leave blank to keep current"
                        : "Enter API key"
                  }
                />
              </Form.Item>
            );
          }}
        </Form.Item>
        <Form.Item name="base_url" label="Base URL">
          <Input placeholder="e.g., https://api.openai.com/v1" />
        </Form.Item>
        <Form.Item name="max_tokens" label="Max Tokens">
          <InputNumber min={1} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="context_length"
          label="Context Window (tokens)"
          tooltip="The model's maximum input context length in tokens. Used to calculate how much file content can be injected into messages."
        >
          <InputNumber min={0} max={2_000_000} style={{ width: "100%" }} placeholder="e.g., 128000" />
        </Form.Item>
        <Form.Item name="temperature" label="Temperature">
          <InputNumber min={0} max={2} step={0.1} style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="input_price_per_1m"
          label="Input Price (per 1M tokens)"
          tooltip="Price per 1 million input tokens. Used for cost calculation."
        >
          <InputNumber min={0} step={0.0001} style={{ width: "100%" }} placeholder="e.g., 0.14" />
        </Form.Item>
        <Form.Item
          name="output_price_per_1m"
          label="Output Price (per 1M tokens)"
          tooltip="Price per 1 million output tokens. Used for cost calculation."
        >
          <InputNumber min={0} step={0.0001} style={{ width: "100%" }} placeholder="e.g., 0.28" />
        </Form.Item>
        <Form.Item
          name="cache_hit_price_per_1m"
          label="Cache Hit Price (per 1M tokens)"
          tooltip="Price per 1 million cached input tokens. Defaults to input price if not set."
        >
          <InputNumber min={0} step={0.0001} style={{ width: "100%" }} placeholder="e.g., 0.0028" />
        </Form.Item>

        <Form.Item shouldUpdate noStyle>
          {() => {
            const provider = form.getFieldValue("provider");
            // Only DeepSeek supports thinking/reasoning mode
            if (provider !== "deepseek") return null;
            return (
              <>
                <Form.Item
                  name="thinking_enabled"
                  label="Enable Thinking Mode"
                  valuePropName="checked"
                  tooltip="When enabled, DeepSeek will emit reasoning tokens between <think> tags, displayed as a collapsible panel in chat"
                >
                  <Switch />
                </Form.Item>
                {form.getFieldValue("thinking_enabled") && (
                  <Form.Item
                    name="reasoning_effort"
                    label="Reasoning Effort"
                    tooltip="Controls reasoning depth in thinking mode. 'low' for quick responses, 'high' for most tasks, 'max' for complex agent requests."
                  >
                    <Select
                      options={[
                        { label: "Low", value: "low" },
                        { label: "High", value: "high" },
                        { label: "Max", value: "max" },
                      ]}
                    />
                  </Form.Item>
                )}
              </>
            );
          }}
        </Form.Item>
        <Form.Item
          name="follow_up_questions_enabled"
          label="Follow-up Questions"
          valuePropName="checked"
          tooltip="When enabled, the model will suggest 3 follow-up questions after each response, displayed as clickable chips in the chat"
        >
          <Switch />
        </Form.Item>
        <Form.Item
          name="auto_route_eligible"
          label="Auto-Route Eligible"
          valuePropName="checked"
          tooltip="When enabled, the intelligent router may automatically select this model based on the task category"
        >
          <Switch />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>
        <Form.Item
          name="is_public"
          label="Public"
          valuePropName="checked"
          tooltip="When enabled, all tenant users can use this model regardless of group membership"
        >
          <Switch onChange={(checked) => setIsPublic(checked)} />
        </Form.Item>
        {!isPublic && (
          <Form.Item name="groups" label="Assigned Groups">
            {groupsLoading ? (
              <Spin size="small" />
            ) : (
              <Select
                mode="multiple"
                placeholder="Select groups that can access this model..."
                options={(allGroups || [] as GroupData[]).map((g: GroupData) => ({
                  label: g.name,
                  value: g.id,
                }))}
              />
            )}
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}

export default ModelForm;
