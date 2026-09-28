// =============================================================================
// PH Agent Hub — Admin WorkflowForm
// =============================================================================
// Create / edit workflow definitions.  Renders metadata fields, a dynamic
// step editor, guardrails, and a submit handler wired to create / update
// mutations.  (Topology preview gate deferred to part 2.)
// =============================================================================

import {
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Button,
  Space,
  message,
  Typography,
  Card,
  Alert,
  Divider,
  Modal,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
} from "@ant-design/icons";
import { useSearchParams, Link } from "react-router-dom";
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getWorkflow,
  createWorkflow,
  updateWorkflow,
  getWorkflowSchema,
  listRegisteredAgents,
  listUnboundRoles,
  listModels,
  listTools,
  WorkflowDefinitionDocument,
  WorkflowStepData,
  RegisteredAgentData,
  UnboundRoleData,
  ModelData,
  ToolData,
  WorkflowTopologyWarningData,
  previewWorkflowTopology,
} from "../../services/admin";
import { useAuth } from "../../../../providers/AuthProvider";

const { Text } = Typography;

function generateStepId(knownIds: string[]): string {
  let id = "step_" + Date.now();
  while (knownIds.includes(id)) {
    id = id + "_";
  }
  return id;
}

// ---------------------------------------------------------------------------
// Main form component
// ---------------------------------------------------------------------------

export function WorkflowForm({ id }: { id?: string }) {
  const [form] = Form.useForm();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const tenantId =
    searchParams.get("tenant_id") || user?.tenant_id || undefined;

  const isEdit = !!id;

  // ------------------------------------------------------------------
  // Queries
  // ------------------------------------------------------------------

  const { data: schema } = useQuery({
    queryKey: ["admin-workflow-schema"],
    queryFn: getWorkflowSchema,
  });

  const { data: models } = useQuery({
    queryKey: ["admin-models-enabled", tenantId],
    queryFn: () =>
      listModels({ tenant_id: tenantId, enabled: true, page_size: 200 }),
    select: (res) => (res?.items ?? []) as ModelData[],
    enabled: !!tenantId,
  });

  const { data: tools } = useQuery({
    queryKey: ["admin-tools-enabled", tenantId],
    queryFn: () =>
      listTools({ tenant_id: tenantId, enabled: true, page_size: 200 }),
    select: (res) => (res?.items ?? []) as ToolData[],
    enabled: !!tenantId,
  });

  const { data: agents } = useQuery({
    queryKey: ["admin-registered-agents"],
    queryFn: listRegisteredAgents,
  });

  const { data: workflow } = useQuery({
    queryKey: ["admin-workflow", id],
    queryFn: () => getWorkflow(id!),
    enabled: isEdit,
  });

  const { data: unboundRoles } = useQuery({
    queryKey: ["admin-workflow-unbound-roles", id],
    queryFn: () => listUnboundRoles(id!),
    enabled: isEdit,
  });

  // ------------------------------------------------------------------
  // Mutations
  // ------------------------------------------------------------------

  const createMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      createWorkflow(values as {
        tenant_id?: string;
        key: string;
        name: string;
        description?: string | null;
        definition: WorkflowDefinitionDocument;
        visibility: string;
        enabled: boolean;
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-workflows"] });
      message.success("Workflow created");
    },
    onError: (err: Error) =>
      message.error(err.message || "Failed to create workflow"),
  });

  const updateMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      updateWorkflow(id!, values as {
        name?: string;
        description?: string | null;
        definition?: WorkflowDefinitionDocument;
        visibility?: string;
        enabled?: boolean;
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-workflows"] });
      message.success("Workflow updated");
    },
    onError: (err: Error) =>
      message.error(err.message || "Failed to update workflow"),
  });

  // ------------------------------------------------------------------
  // Populate form on load (edit mode)
  // ------------------------------------------------------------------

  const initForm = () => {
    if (workflow) {
      form.setFieldsValue({
        key: workflow.key,
        name: workflow.name,
        description: workflow.description ?? undefined,
        visibility: workflow.visibility,
        enabled: workflow.enabled,
        steps: workflow.definition?.steps || [],
        max_total_tokens: workflow.definition?.max_total_tokens,
        max_cost: workflow.definition?.max_cost,
        default_step_timeout_seconds: workflow.definition?.default_step_timeout_seconds,
      });
    } else {
      form.resetFields();
      form.setFieldsValue({ enabled: true });
    }
  };

  // Initialise after schema + workflow queries resolve
  useEffect(() => {
    if (isEdit && !workflow) return;
    initForm();
  }, [workflow, isEdit]);

  // ------------------------------------------------------------------
  // Watch current steps for conditional rendering & step references
  // ------------------------------------------------------------------

  const currentSteps = Form.useWatch("steps", form) ?? [];

  // ------------------------------------------------------------------
  // Step editor helpers
  // ------------------------------------------------------------------

  const addStep = () => {
    const value = form.getFieldValue("steps") || [];
    const newStep: WorkflowStepData = {
      id: generateStepId(value.map((s: WorkflowStepData) => s.id)),
      name: "",
      type: "inline",
      instructions: "",
      model_ref: null,
      tool_refs: [],
      input: "",
      context_mode: "full",
      on_error: "stop",
    };
    form.setFieldsValue({ steps: [...value, newStep] });
  };

  const removeStep = (index: number) => {
    const value = form.getFieldValue("steps") || [];
    form.setFieldsValue({
      steps: value.filter((_v: unknown, i: number) => i !== index),
    });
  };

  const moveStep = (index: number, direction: "up" | "down") => {
    const value = form.getFieldValue("steps") || [];
    if (
      (direction === "up" && index === 0) ||
      (direction === "down" && index === value.length - 1)
    ) {
      return;
    }
    const next = [...value];
    const target = direction === "up" ? index - 1 : index + 1;
    [next[index], next[target]] = [next[target], next[index]];
    form.setFieldsValue({ steps: next });
  };

  // ------------------------------------------------------------------
  // Form values -> submit payload
  // ------------------------------------------------------------------

  const onFinish = async (values: Record<string, unknown>) => {
    const steps = values.steps as WorkflowStepData[];
    const existingDefinition = workflow?.definition;

    const definition: WorkflowDefinitionDocument = {
      key: values.key as string,
      name: values.name as string,
      description: (values.description as string) || undefined,
      steps,
      branches: existingDefinition?.branches ?? [],
      max_total_tokens: values.max_total_tokens as number | null | undefined,
      max_cost: values.max_cost as number | null | undefined,
      default_step_timeout_seconds: values.default_step_timeout_seconds as
        | number
        | null
        | undefined,
    };

    // Step-count guard
    if (typeof schema?.max_steps === "number" && steps.length > schema.max_steps) {
      message.error(`Workflow exceeds the maximum of ${schema.max_steps} steps`);
      return;
    }

    if (isEdit) {
      const document: WorkflowDefinitionDocument = { ...definition };
      const warning: WorkflowTopologyWarningData = await previewWorkflowTopology(id!, document);

      if (warning.requires_confirmation) {
        const addedIds = warning.added_step_ids?.filter((s: string) => s !== "");
        const removedIds = warning.removed_step_ids?.filter((s: string) => s !== "");
        Modal.confirm({
          title: "Topology change requires confirmation",
          content: (
            <>
              <p>{warning.report}</p>
              {addedIds.length > 0 && (
                <p><strong>Added steps:</strong> {addedIds.join(", ")}</p>
              )}
              {removedIds.length > 0 && (
                <p><strong>Removed steps:</strong> {removedIds.join(", ")}</p>
              )}
            </>
          ),
          okText: "Save anyway",
          onOk: async () => {
            try {
              await updateMutation.mutateAsync({
                name: values.name as string,
                description: (values.description as string) || undefined,
                definition,
                visibility: values.visibility as string,
                enabled: values.enabled as boolean,
                confirm_topology_edit: true,
              });
            } catch (err: any) {
              message.error(err?.message || "Failed to update workflow");
              throw err;
            }
          },
        });
        return;
      }

      await updateMutation.mutateAsync({
        name: values.name as string,
        description: (values.description as string) || undefined,
        definition,
        visibility: values.visibility as string,
        enabled: values.enabled as boolean,
      });
    } else {
      await createMutation.mutateAsync({
        tenant_id: tenantId,
        key: values.key as string,
        name: values.name as string,
        description: (values.description as string) || undefined,
        definition,
        visibility: values.visibility as string,
        enabled: values.enabled as boolean,
      });
    }
  };

  // ------------------------------------------------------------------
  // Derive options
  // ------------------------------------------------------------------

  const allStepIds = currentSteps
    .map((s: WorkflowStepData) => s.id)
    .filter((sid: string) => sid !== "") as string[];

  const modelRoleOptions = (schema?.model_roles ?? []).map((r) => ({
    label: r,
    value: r,
  }));

  const modelOptions = (models || []).map((m: ModelData) => ({
    label: `${m.name} (${m.model_id})`,
    value: m.model_id,
  }));

  const toolRoleOptions = (schema?.tool_roles ?? []).map((r) => ({
    label: r,
    value: r,
  }));

  const toolOptions = (tools || []).map((t: ToolData) => ({
    label: t.name,
    value: t.id,
  }));

  const agentOptions = [
    ...((schema?.agent_roles ?? []) as string[]).map((r) => ({
      label: r,
      value: r,
    })),
    ...((agents || []) as RegisteredAgentData[]).map((a: RegisteredAgentData) => ({
      label: `${a.name} (${a.key})`,
      value: a.key,
    })),
  ];

  const inputOptions = [
    { label: "Inherit upstream output", value: "" },
    { label: "user_message", value: "user_message" },
    ...allStepIds.map(
      (sid) =>
        ({
          label: `output_of:${sid}`,
          value: `output_of:${sid}`,
        }) as { label: string; value: string },
    ),
  ];

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        {isEdit ? "Edit Workflow" : "Create Workflow"}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 16 }}>
        Define steps, guardrails, and visibility for a workflow definition.
        The key identifies the workflow and cannot be changed after creation.
      </Typography.Paragraph>

      {/* Unbound roles alerts (edit mode) */}
      {isEdit && unboundRoles && unboundRoles.length > 0 && (
        <Space direction="vertical" style={{ width: "100%", marginBottom: 16 }} size="middle">
          {unboundRoles.map((role: UnboundRoleData, idx: number) => (
            <Alert
              key={idx}
              type="warning"
              message={`Unbound role: ${role.role}`}
              description={
                <Space direction="vertical" size={2}>
                  <Text>Models: {(role.models || []).join(", ") || "none"}</Text>
                  <Text>
                    Workflows: {(role.workflow_keys || []).join(", ") || "none"}
                  </Text>
                  <Link to="/admin/model-roles">Bind roles →</Link>
                </Space>
              }
              showIcon
            />
          ))}
        </Space>
      )}

      {/* Read-only branches (edit mode) */}
      {isEdit &&
        workflow?.definition?.branches &&
        workflow.definition.branches.length > 0 && (
          <Alert
            type="info"
            message="Workflow branches (read-only)"
            description={
              <Space direction="vertical" size={2}>
                {workflow.definition.branches.map((b, idx) => (
                  <Text key={idx} code>
                    {b.source} --{b.condition}-{">"} {b.target}
                  </Text>
                ))}
                <Text type="secondary">
                  Branches are authored by developers. Use the topology preview to
                  validate changes before saving.
                </Text>
              </Space>
            }
            showIcon
            style={{ marginBottom: 16 }}
          />
        )}

      <Form
        form={form}
        layout="vertical"
        onFinish={onFinish}
        initialValues={{
          visibility: "tenant",
          enabled: true,
          steps: [],
        }}
      >
        {/* ---- Metadata fields ---- */}
        <Card style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Form.Item
              name="key"
              label="Key"
              rules={[{ required: true, message: "Key is required" }]}
              extra={
                isEdit
                  ? "The key is immutable after creation. Use it as a stable identifier."
                  : "Unique identifier for this workflow."
              }
            >
              <Input disabled={isEdit} placeholder="my-workflow" />
            </Form.Item>

            <Form.Item
              name="name"
              label="Name"
              rules={[{ required: true, message: "Name is required" }]}
            >
              <Input placeholder="Human Agent Hub" />
            </Form.Item>

            <Form.Item name="description" label="Description">
              <Input.TextArea rows={3} placeholder="Human-in-the-loop agent hub for customer support" />
            </Form.Item>

            <Form.Item
              name="visibility"
              label="Visibility"
              rules={[{ required: true }]}
            >
              <Select>
                <Select.Option value="tenant">Tenant</Select.Option>
                <Select.Option value="user">User</Select.Option>
              </Select>
            </Form.Item>

            <Form.Item
              name="enabled"
              label="Enabled"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>
          </Space>
        </Card>

        {/* ---- Step editor ---- */}
        <Card style={{ marginBottom: 16 }} title="Steps">
          <Form.List name="steps">
            {(fields, { add: addField, remove: removeField }) => (
              <>
                {fields.map((field, index) => {
                  return (
                    <Card
                      key={field.key}
                      size="small"
                      style={{ marginBottom: 12 }}
                      title={`Step ${index + 1}: ${form.getFieldValue(["steps", index, "name"]) || "Untitled"}`}
                      extra={
                        <Space>
                          <Button
                            size="small"
                            icon={<ArrowUpOutlined />}
                            onClick={() => moveStep(index, "up")}
                            disabled={index === 0}
                          />
                          <Button
                            size="small"
                            icon={<ArrowDownOutlined />}
                            onClick={() => moveStep(index, "down")}
                            disabled={index === fields.length - 1}
                          />
                          <Button
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => removeField(index)}
                          />
                        </Space>
                      }
                    >
                      <Space direction="vertical" style={{ width: "100%" }}>
                        <Form.Item
                          {...field}
                          name={[field.name, "id"]}
                          label="Step ID"
                          rules={[{ required: true, message: "Step ID is required" }]}
                          extra="This is the executor identity for the step and must be stable across edits."
                        >
                          <Input placeholder="step_1" />
                        </Form.Item>

                        <Form.Item
                          {...field}
                          name={[field.name, "name"]}
                          label="Step Name"
                        >
                          <Input placeholder="Summarize the conversation" />
                        </Form.Item>

                        <Form.Item
                          {...field}
                          name={[field.name, "type"]}
                          label="Type"
                          rules={[{ required: true }]}
                        >
                          <Select>
                            {(schema?.step_types ?? []).map((t) => (
                              <Select.Option key={t} value={t}>
                                {t}
                              </Select.Option>
                            ))}
                          </Select>
                        </Form.Item>

                        {/* Instructions — inline only */}
                        <Form.Item noStyle shouldUpdate={(prev, curr) => {
                          const prevType = prev.steps?.[index]?.type;
                          const currType = curr.steps?.[index]?.type;
                          return prevType !== currType;
                        }}>
                          {() => {
                            const curType = form.getFieldValue([
                              "steps",
                              index,
                              "type",
                            ]);
                            if (curType !== "inline") return null;
                            return (
                              <Form.Item
                                {...field}
                                name={[field.name, "instructions"]}
                                label="Instructions"
                                rules={[{ required: true, message: "Instructions are required for inline steps" }]}
                                extra="Free-text instructions that tell the executor what to do."
                              >
                                <Input.TextArea rows={4} placeholder="Extract key details from the conversation..." />
                              </Form.Item>
                            );
                          }}
                        </Form.Item>

                        {/* Agent ref — agent type only */}
                        <Form.Item noStyle shouldUpdate={(prev, curr) => {
                          const prevType = prev.steps?.[index]?.type;
                          const currType = curr.steps?.[index]?.type;
                          return prevType !== currType;
                        }}>
                          {() => {
                            const curType = form.getFieldValue([
                              "steps",
                              index,
                              "type",
                            ]);
                            if (curType !== "agent") return null;
                            return (
                              <Form.Item
                                {...field}
                                name={[field.name, "agent_ref"]}
                                label="Agent Reference"
                              >
                                <Select allowClear placeholder="Select agent or role">
                                  {agentOptions.map((opt) => (
                                    <Select.Option key={opt.value} value={opt.value}>
                                      {opt.label}
                                    </Select.Option>
                                  ))}
                                </Select>
                              </Form.Item>
                            );
                          }}
                        </Form.Item>

                        {/* Model ref */}
                        <Form.Item
                          {...field}
                          name={[field.name, "model_ref"]}
                          label="Model Reference"
                        >
                          <Select allowClear placeholder="Select model or role">
                            {modelRoleOptions.length > 0 && (
                              <Select.OptGroup label="Roles">
                                {modelRoleOptions.map((opt) => (
                                  <Select.Option key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </Select.Option>
                                ))}
                              </Select.OptGroup>
                            )}
                            {modelOptions.length > 0 && (
                              <Select.OptGroup label="Models">
                                {modelOptions.map((opt) => (
                                  <Select.Option key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </Select.Option>
                                ))}
                              </Select.OptGroup>
                            )}
                            {modelRoleOptions.length === 0 &&
                              modelOptions.length === 0 && (
                                <Select.Option value="" disabled>
                                  No models available
                                </Select.Option>
                              )}
                          </Select>
                        </Form.Item>

                        {/* Tool refs */}
                        <Form.Item
                          {...field}
                          name={[field.name, "tool_refs"]}
                          label="Tool References"
                        >
                          <Select
                            mode="multiple"
                            allowClear
                            placeholder="Select tools or roles"
                          >
                            {toolRoleOptions.length > 0 && (
                              <Select.OptGroup label="Roles">
                                {toolRoleOptions.map((opt) => (
                                  <Select.Option key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </Select.Option>
                                ))}
                              </Select.OptGroup>
                            )}
                            {toolOptions.length > 0 && (
                              <Select.OptGroup label="Tools">
                                {toolOptions.map((opt) => (
                                  <Select.Option key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </Select.Option>
                                ))}
                              </Select.OptGroup>
                            )}
                            {toolRoleOptions.length === 0 &&
                              toolOptions.length === 0 && (
                                <Select.Option value="" disabled>
                                  No tools available
                                </Select.Option>
                              )}
                          </Select>
                        </Form.Item>

                        {/* Reasoning effort */}
                        <Form.Item
                          {...field}
                          name={[field.name, "reasoning_effort"]}
                          label="Reasoning Effort"
                        >
                          <Input allowClear placeholder="high" />
                        </Form.Item>

                        {/* Temperature */}
                        <Form.Item
                          {...field}
                          name={[field.name, "temperature"]}
                          label="Temperature"
                        >
                          <InputNumber
                            min={0}
                            max={2}
                            step={0.1}
                            style={{ width: "100%" }}
                            placeholder="0.7"
                          />
                        </Form.Item>

                        {/* Input */}
                        <Form.Item
                          {...field}
                          name={[field.name, "input"]}
                          label="Input"
                        >
                          <Select allowClear>
                            {inputOptions.map((opt) => (
                              <Select.Option key={opt.value ?? `__empty__`} value={opt.value}>
                                {opt.label}
                              </Select.Option>
                            ))}
                          </Select>
                        </Form.Item>

                        {/* Context mode */}
                        <Form.Item
                          {...field}
                          name={[field.name, "context_mode"]}
                          label="Context Mode"
                        >
                          <Select>
                            {(schema?.context_modes ?? []).map((cm) => (
                              <Select.Option key={cm} value={cm}>
                                {cm}
                              </Select.Option>
                            ))}
                          </Select>
                        </Form.Item>

                        {/* On error */}
                        <Form.Item
                          {...field}
                          name={[field.name, "on_error"]}
                          label="On Error"
                        >
                          <Select>
                            {(schema?.on_error_values ?? []).map((oe) => (
                              <Select.Option key={oe} value={oe}>
                                {oe}
                              </Select.Option>
                            ))}
                          </Select>
                        </Form.Item>

                        {/* Timeout */}
                        <Form.Item
                          {...field}
                          name={[field.name, "timeout_seconds"]}
                          label="Timeout (seconds)"
                        >
                          <InputNumber
                            min={0}
                            style={{ width: "100%" }}
                            placeholder="30"
                          />
                        </Form.Item>
                      </Space>
                    </Card>
                  );
                })}

                <Button
                  type="dashed"
                  onClick={addField}
                  style={{ width: "100%", marginBottom: 8 }}
                  icon={<PlusOutlined />}
                >
                  Add step
                </Button>
              </>
            )}
          </Form.List>

          <Space style={{ marginTop: 8 }}>
            <Button type="primary" onClick={addStep} icon={<PlusOutlined />}>
              Add step
            </Button>
            <Button
              danger
              icon={<DeleteOutlined />}
              onClick={() => {
                const steps = form.getFieldValue("steps") || [];
                if (steps.length > 0) {
                  removeStep(steps.length - 1);
                }
              }}
            >
              Remove
            </Button>
          </Space>
        </Card>

        {/* ---- Guardrails block ---- */}
        <Card style={{ marginBottom: 16 }} title="Guardrails">
          <Space direction="vertical" style={{ width: "100%" }}>
            <Form.Item
              name="max_total_tokens"
              label="Max Total Tokens"
            >
              <InputNumber
                min={0}
                style={{ width: "100%" }}
                placeholder="50000"
              />
            </Form.Item>

            <Form.Item
              name="max_cost"
              label="Max Cost (USD)"
            >
              <InputNumber
                min={0}
                step={0.01}
                style={{ width: "100%" }}
                placeholder="10.00"
              />
            </Form.Item>

            <Form.Item
              name="default_step_timeout_seconds"
              label="Default Step Timeout (seconds)"
            >
              <InputNumber
                min={0}
                style={{ width: "100%" }}
                placeholder="60"
              />
            </Form.Item>

            <Text type="secondary">
              These ceilings are enforced between steps to prevent runaway runs.
            </Text>
          </Space>
        </Card>

        {/* ---- Submit ---- */}
        <Divider />
        <Space>
          <Button
            type="primary"
            htmlType="submit"
            loading={createMutation.isPending || updateMutation.isPending}
          >
            {isEdit ? "Save" : "Submit"}
          </Button>
          <Link to="/admin/workflows">
            <Button>Cancel</Button>
          </Link>
        </Space>
      </Form>
    </div>
  );
}

export default WorkflowForm;
