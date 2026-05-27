// =============================================================================
// PH Agent Hub — Admin TemplateForm
// =============================================================================
// Ant Design Create/Edit Modal+Form.
// =============================================================================

import React from "react";
import {
  Modal,
  Form,
  Input,
  Select,
  message,
} from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createTemplate,
  updateTemplate,
  TemplateData,
  listTenants,
  listUsers,
} from "../../services/admin";
import { useAuth } from "../../../../providers/AuthProvider";

const { TextArea } = Input;

interface TemplateFormProps {
  open: boolean;
  template: TemplateData | null;
  duplicateFrom?: TemplateData | null;
  onClose: () => void;
}

export function TemplateForm({ open, template, duplicateFrom, onClose }: TemplateFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!template && !duplicateFrom;
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const scope = Form.useWatch("scope", form);

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
          title: duplicateFrom.title,
          description: duplicateFrom.description,
          system_prompt: duplicateFrom.system_prompt,
          scope: duplicateFrom.scope,
          assigned_user_id: duplicateFrom.assigned_user_id,
        });
      } else if (template) {
        form.setFieldsValue({
          tenant_id: template.tenant_id,
          title: template.title,
          description: template.description,
          system_prompt: template.system_prompt,
          scope: template.scope,
          assigned_user_id: template.assigned_user_id,
        });
      } else {
        form.resetFields();
        form.setFieldsValue({
          tenant_id: undefined,
          scope: "tenant",
        });
      }
    }
  }, [open, template, duplicateFrom, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      createTemplate(data as unknown as Partial<TemplateData>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-templates"] });
      message.success("Template created");
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
    }) => updateTemplate(id, data as unknown as Partial<TemplateData>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-templates"] });
      message.success("Template updated");
      onClose();
    },
  });

  return (
    <Modal
      title={duplicateFrom ? "Duplicate Template" : isEdit ? "Edit Template" : "Create Template"}
      open={open}
      onOk={async () => {
        const values = await form.validateFields();
        if (isEdit) {
          await updateMutation.mutateAsync({
            id: template!.id,
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
          name="system_prompt"
          label="System Prompt"
          rules={[{ required: true }]}
        >
          <TextArea rows={6} />
        </Form.Item>
        <Form.Item name="scope" label="Scope">
          <Select
            options={[
              { label: "Tenant", value: "tenant" },
              { label: "User", value: "user" },
            ]}
          />
        </Form.Item>
        {scope === "user" && (
          <Form.Item
            name="assigned_user_id"
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
      </Form>
    </Modal>
  );
}

export default TemplateForm;
