// =============================================================================
// PH Agent Hub — Admin TenantForm
// =============================================================================
// Admin only; Ant Design Create/Edit Modal+Form.
// =============================================================================

import React from "react";
import { Modal, Form, Input, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createTenant, updateTenant, TenantData } from "../../services/admin";

interface TenantFormProps {
  open: boolean;
  tenant: TenantData | null;
  onClose: () => void;
}

export function TenantForm({ open, tenant, onClose }: TenantFormProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const isEdit = !!tenant;

  React.useEffect(() => {
    if (open) {
      if (tenant) {
        form.setFieldsValue({ name: tenant.name });
      } else {
        form.resetFields();
      }
    }
  }, [open, tenant, form]);

  const createMutation = useMutation({
    mutationFn: (data: { name: string }) => createTenant(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
      queryClient.invalidateQueries({ queryKey: ["admin-tenant-status"] });
      message.success("Tenant created");
      onClose();
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to create tenant");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name: string } }) =>
      updateTenant(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
      queryClient.invalidateQueries({ queryKey: ["admin-tenant-status"] });
      message.success("Tenant updated");
      onClose();
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to update tenant");
    },
  });

  return (
    <Modal
      title={isEdit ? "Edit Tenant" : "Create Tenant"}
      open={open}
      onOk={async () => {
        const values = await form.validateFields();
        try {
          if (isEdit) {
            await updateMutation.mutateAsync({ id: tenant!.id, data: values });
          } else {
            await createMutation.mutateAsync(values);
          }
        } catch {
          // Error already handled by onError callbacks
        }
      }}
      onCancel={onClose}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="Name"
          rules={[{ required: true }]}
        >
          <Input />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default TenantForm;
