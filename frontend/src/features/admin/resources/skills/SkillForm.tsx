// =============================================================================
// PH Agent Hub — Admin SkillForm
// =============================================================================
// Ant Design Create/Edit Modal+Form; maf_target_key, default_model_id, tool_ids.
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
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createSkill,
  updateSkill,
  SkillData,
  listTools,
  listModels,
  listTemplates,
  listTenants,
  listUsers,
} from "../../services/admin";
import { useAuth } from "../../../../providers/AuthProvider";

const { TextArea } = Input;

interface SkillFormProps {
  open: boolean;
  skill: SkillData | null;
  duplicateFrom?: SkillData | null;
  onClose: () => void;
}

export function SkillForm({ open, skill, duplicateFrom, onClose }: SkillFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!skill && !duplicateFrom;
  const executionType = Form.useWatch("execution_type", form);
  const visibility = Form.useWatch("visibility", form);
  const tenantId = Form.useWatch("tenant_id", form);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const { data: tools } = useQuery({
    queryKey: ["admin-tools", tenantId],
    queryFn: () => listTools({ tenant_id: tenantId, page_size: 500, enabled: true }).then(r => r.items),
    enabled: open,
  });

  const { data: models } = useQuery({
    queryKey: ["admin-models", tenantId],
    queryFn: () => listModels({ tenant_id: tenantId }).then(r => r.items),
    enabled: open,
  });

  const { data: templates } = useQuery({
    queryKey: ["admin-templates", tenantId],
    queryFn: () => listTemplates({ tenant_id: tenantId }).then(r => r.items),
    enabled: open,
  });

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => listTenants(),
    select: (data) => data?.items || [],
    enabled: open && isAdmin,
  });

  const { data: users } = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => listUsers().then(r => r.items),
    enabled: open,
  });

  React.useEffect(() => {
    if (open) {
      if (duplicateFrom) {
        form.setFieldsValue({
          tenant_id: duplicateFrom.tenant_id,
          user_id: duplicateFrom.user_id,
          title: duplicateFrom.title,
          description: duplicateFrom.description,
          execution_type: duplicateFrom.execution_type,
          maf_target_key: duplicateFrom.maf_target_key,
          visibility: duplicateFrom.visibility,
          template_id: duplicateFrom.template_id,
          default_model_id: duplicateFrom.default_model_id,
          enabled: duplicateFrom.enabled,
          tool_ids: duplicateFrom.tool_ids || [],
          cross_session_retrieval_enabled: duplicateFrom.cross_session_retrieval_enabled ?? false,
          cross_session_max_snippets: duplicateFrom.cross_session_max_snippets ?? 3,
          cross_session_min_score: duplicateFrom.cross_session_min_score ?? 0.30,
          goal: duplicateFrom.goal,
          constraints: duplicateFrom.constraints,
          success_criteria: duplicateFrom.success_criteria,
          agent_config: duplicateFrom.agent_config ? { max_turns: duplicateFrom.agent_config.max_turns } : undefined,
        });
      } else if (skill) {
        form.setFieldsValue({
          tenant_id: skill.tenant_id,
          user_id: skill.user_id,
          title: skill.title,
          description: skill.description,
          execution_type: skill.execution_type,
          maf_target_key: skill.maf_target_key,
          visibility: skill.visibility,
          template_id: skill.template_id,
          default_model_id: skill.default_model_id,
          enabled: skill.enabled,
          tool_ids: skill.tool_ids || [],
          cross_session_retrieval_enabled: skill.cross_session_retrieval_enabled ?? false,
          cross_session_max_snippets: skill.cross_session_max_snippets ?? 3,
          cross_session_min_score: skill.cross_session_min_score ?? 0.30,
          goal: skill.goal,
          constraints: skill.constraints,
          success_criteria: skill.success_criteria,
          agent_config: skill.agent_config ? { max_turns: skill.agent_config.max_turns } : undefined,
        });
      } else {
        form.resetFields();
        form.setFieldsValue({
          tenant_id: undefined,
          user_id: undefined,
          execution_type: "prompt_based",
          visibility: "tenant",
          enabled: true,
          tool_ids: [],
          cross_session_retrieval_enabled: false,
          cross_session_max_snippets: 3,
          cross_session_min_score: 0.30,
        });
      }
    }
  }, [open, skill, duplicateFrom, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      createSkill(data as unknown as Partial<SkillData>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-skills"] });
      message.success("Skill created");
      onClose();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Record<string, unknown>;
    }) => updateSkill(id, data as unknown as Partial<SkillData>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-skills"] });
      message.success("Skill updated");
      onClose();
    },
  });

  return (
    <Modal
      title={duplicateFrom ? "Duplicate Skill" : isEdit ? "Edit Skill" : "Create Skill"}
      open={open}
      onOk={async () => {
        const values = await form.validateFields();
        if (isEdit) {
          await updateMutation.mutateAsync({
            id: skill!.id,
            data: values,
          });
        } else {
          await createMutation.mutateAsync(values);
        }
      }}
      onCancel={onClose}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
      width={640}
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
              options={(tenants || []).map((t) => ({
                label: t.name,
                value: t.id,
              }))}
            />
          </Form.Item>
        )}
        <Form.Item
          name="title"
          label="Title"
          rules={[{ required: true }]}
        >
          <Input />
        </Form.Item>
        <Form.Item
          name="description"
          label="Description"
        >
          <TextArea rows={2} />
        </Form.Item>
        <Form.Item
          name="execution_type"
          label="Execution Type"
          rules={[{ required: true }]}
        >
          <Select
            options={[
              { label: "Prompt Based", value: "prompt_based" },
              { label: "Workflow Based", value: "workflow_based" },
              { label: "Goal Based", value: "goal_based" },
            ]}
          />
        </Form.Item>
        {executionType === "workflow_based" && (
          <Form.Item
            name="maf_target_key"
            label="MAF Target Key"
            rules={[{ required: true }]}
            extra="Must match a registered workflow module"
          >
            <Input placeholder="e.g., invoice_processing" />
          </Form.Item>
        )}
        <Form.Item name="visibility" label="Visibility">
          <Select
            options={[
              { label: "Tenant", value: "tenant" },
              { label: "User", value: "user" },
            ]}
          />
        </Form.Item>
        {visibility === "user" && (
          <Form.Item
            name="user_id"
            label="Assign to User"
            rules={[{ required: true, message: "Please select a user" }]}
          >
            <Select
              placeholder="Select user"
              showSearch
              optionFilterProp="label"
              options={(users || []).map((u) => ({
                label: `${u.display_name || u.email} (${u.email})`,
                value: u.id,
              }))}
            />
          </Form.Item>
        )}
        {executionType !== "workflow_based" && executionType !== "goal_based" && (
          <Form.Item name="template_id" label="Template">
            <Select
              allowClear
              placeholder="Select a template (provides system prompt)"
              options={(templates || []).map((t) => ({
                label: t.title,
                value: t.id,
              }))}
            />
          </Form.Item>
        )}

        {executionType === "goal_based" && (
          <>
            <Form.Item
              name="goal"
              label="Goal"
              rules={[{ required: true, message: "Please enter the goal" }]}
              extra="The objective the agent should achieve"
            >
              <TextArea rows={4} placeholder="e.g., Diagnose why the supplier_rate report is showing incorrect values" />
            </Form.Item>
            <Form.Item
              name="constraints"
              label="Constraints"
              extra="Behavioral constraints for the agent"
            >
              <Select
                mode="multiple"
                allowClear
                placeholder="Select constraints"
                options={[
                  { label: "Read Only (no modifications)", value: "read_only" },
                  { label: "Max Cost €0.50", value: "max_cost_€0.50" },
                  { label: "Max Cost €1.00", value: "max_cost_€1.00" },
                  { label: "Max Cost €5.00", value: "max_cost_€5.00" },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="success_criteria"
              label="Success Criteria"
              extra="How to determine the task is complete"
            >
              <TextArea rows={2} placeholder="e.g., Identify the root cause and propose a specific fix" />
            </Form.Item>
            <Form.Item
              name={["agent_config", "max_turns"]}
              label="Max Turns"
              extra="Maximum agent turns before forced summarization (default: 20)"
            >
              <Input type="number" min={1} max={50} placeholder="20" />
            </Form.Item>
          </>
        )}

        <Form.Item name="default_model_id" label="Default Model">
          <Select
            allowClear
            placeholder="Select default model"
            options={(models || []).map((m) => ({
              label: `${m.name} (${m.provider})`,
              value: m.id,
            }))}
          />
        </Form.Item>
        <Form.Item name="tool_ids" label="Tools">
          <Select
            mode="multiple"
            allowClear
            showSearch
            placeholder="Select tools"
            filterOption={(input, option) =>
              (option?.label as string)?.toLowerCase().includes(input.toLowerCase())
            }
            options={(tools || []).map((t) => ({
              label: t.name,
              value: t.id,
            }))}
          />
        </Form.Item>
        <Form.Item name="enabled" label="Enabled" valuePropName="checked">
          <Switch />
        </Form.Item>

        {/* ---- Cross-session memory (Issue #229) ---- */}
        <Form.Item
          label="Cross-Session Memory"
          style={{ marginBottom: 0 }}
        >
          <Form.Item
            name="cross_session_retrieval_enabled"
            valuePropName="checked"
            style={{ display: "inline-block", marginRight: 32 }}
          >
            <Switch checkedChildren="Enabled" unCheckedChildren="Disabled" />
          </Form.Item>
          <Form.Item
            name="cross_session_max_snippets"
            label="Max Snippets"
            style={{ display: "inline-block", width: 120, marginRight: 32 }}
          >
            <Input type="number" min={1} max={10} />
          </Form.Item>
          <Form.Item
            name="cross_session_min_score"
            label="Min Score"
            style={{ display: "inline-block", width: 120 }}
          >
            <Input type="number" min={0} max={1} step={0.05} />
          </Form.Item>
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default SkillForm;
