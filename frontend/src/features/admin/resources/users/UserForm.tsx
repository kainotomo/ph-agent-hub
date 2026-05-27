// =============================================================================
// PH Agent Hub — Admin UserForm
// =============================================================================
// Ant Design Create/Edit Modal+Form; role selector (admin: any role;
// manager: user only).
// =============================================================================

import React from "react";
import { Modal, Form, Input, Select, Switch, message, Spin } from "antd";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import { useAuth } from "../../../../providers/AuthProvider";
import {
  createUser,
  updateUser,
  listUserGroups,
  addGroupMember,
  removeGroupMember,
  listGroups,
  listTenants,
  UserData,
  GroupData,
  TenantData,
} from "../../services/admin";

interface UserFormProps {
  open: boolean;
  user: UserData | null;
  duplicateFrom?: UserData | null;
  onClose: () => void;
}

export function UserForm({ open, user, duplicateFrom, onClose }: UserFormProps) {
  const [form] = Form.useForm();
  const { user: currentUser } = useAuth();
  const queryClient = useQueryClient();
  const isEdit = !!user && !duplicateFrom;
  const isAdmin = currentUser?.role === "admin";

  // Groups state
  const [initialGroupIds, setInitialGroupIds] = React.useState<string[]>([]);
  const [hasGroupChanges, setHasGroupChanges] = React.useState(false);

  const { data: allGroups } = useQuery({
    queryKey: ["admin-groups"],
    queryFn: () => listGroups().then(r => r.items),
    enabled: open,
  });

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: () => listTenants(),
    select: (data) => data?.items || [],
    enabled: open && isAdmin,
  });

  const { data: userGroups, isLoading: groupsLoading } = useQuery({
    queryKey: ["admin-user-groups", user?.id],
    queryFn: () => listUserGroups(user!.id),
    enabled: open && isEdit,
  });

  React.useEffect(() => {
    if (userGroups) {
      const ids = userGroups.map((g: GroupData) => g.id);
      setInitialGroupIds(ids);
      form.setFieldsValue({ groups: ids });
    }
  }, [userGroups, form]);

  const syncGroups = async (userId: string) => {
    const currentGroupIds: string[] = form.getFieldValue("groups") || [];
    const toAdd = currentGroupIds.filter((id) => !initialGroupIds.includes(id));
    const toRemove = initialGroupIds.filter((id) => !currentGroupIds.includes(id));

    for (const gid of toAdd) {
      await addGroupMember(gid, userId);
    }
    for (const gid of toRemove) {
      await removeGroupMember(gid, userId);
    }
  };

  React.useEffect(() => {
    if (open) {
      if (duplicateFrom) {
        // Transform email: user@domain.com -> user-copy@domain.com
        const emailParts = duplicateFrom.email.split("@");
        const dupEmail = emailParts.length === 2
          ? `${emailParts[0]}-copy@${emailParts[1]}`
          : `${duplicateFrom.email}-copy`;
        form.setFieldsValue({
          email: dupEmail,
          display_name: duplicateFrom.display_name,
          role: duplicateFrom.role,
          is_active: duplicateFrom.is_active,
          tenant_id: duplicateFrom.tenant_id,
        });
      } else if (user) {
        form.setFieldsValue({
          email: user.email,
          display_name: user.display_name,
          role: user.role,
          is_active: user.is_active,
          tenant_id: user.tenant_id,
        });
      } else {
        form.resetFields();
        form.setFieldsValue({
          role: "user",
          is_active: true,
        });
      }
    }
  }, [open, user, duplicateFrom, form]);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => createUser(data as Partial<UserData> & { password: string }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      message.success("User created");
      onClose();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, unknown> }) =>
      updateUser(id, data as Partial<UserData> & { password?: string }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      message.success("User updated");
      onClose();
    },
  });

  const handleOk = async () => {
    const values = await form.validateFields();
    if (isEdit) {
      // Only send changed fields
      const data: Record<string, unknown> = {};
      if (values.email !== user!.email) data.email = values.email;
      if (values.display_name !== user!.display_name) data.display_name = values.display_name;
      if (values.role !== user!.role) data.role = values.role;
      if (values.is_active !== user!.is_active) data.is_active = values.is_active;
      if (values.password) data.password = values.password;
      if (isAdmin && values.tenant_id !== user!.tenant_id) data.tenant_id = values.tenant_id;
      if (Object.keys(data).length === 0 && !hasGroupChanges) {
        onClose();
        return;
      }
      await updateMutation.mutateAsync({ id: user!.id, data });

      // Sync groups if changed
      if (hasGroupChanges) {
        await syncGroups(user!.id);
      }
    } else {
      const created = await createMutation.mutateAsync(values);
      // Sync groups for new user
      const selectedGroupIds: string[] = values.groups || [];
      if (selectedGroupIds.length > 0 && created) {
        for (const gid of selectedGroupIds) {
          await addGroupMember(gid, created.id);
        }
      }
    }
  };

  return (
    <Modal
      title={duplicateFrom ? "Duplicate User" : isEdit ? "Edit User" : "Create User"}
      open={open}
      onOk={handleOk}
      onCancel={onClose}
      confirmLoading={createMutation.isPending || updateMutation.isPending}
      width={480}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="email"
          label="Email"
          rules={[{ required: true, type: "email" }]}
        >
          <Input />
        </Form.Item>
        <Form.Item
          name="display_name"
          label="Display Name"
          rules={[{ required: true }]}
        >
          <Input />
        </Form.Item>
        <Form.Item name="password" label="Password">
          <Input.Password
            placeholder={
              isEdit ? "Leave blank to keep current" : duplicateFrom ? "Enter password" : "Enter password"
            }
          />
        </Form.Item>
        <Form.Item name="role" label="Role" rules={[{ required: true }]}>
          <Select
            options={
              isAdmin
                ? [
                    { label: "User", value: "user" },
                    { label: "Manager", value: "manager" },
                    { label: "Admin", value: "admin" },
                  ]
                : [{ label: "User", value: "user" }]
            }
          />
        </Form.Item>
        <Form.Item name="is_active" label="Active" valuePropName="checked">
          <Switch />
        </Form.Item>
        {(isEdit || duplicateFrom) && (
          <Form.Item name="groups" label="Groups">
            {groupsLoading ? (
              <Spin size="small" />
            ) : (
              <Select
                mode="multiple"
                placeholder="Select groups..."
                onChange={() => setHasGroupChanges(true)}
                options={(allGroups || []).map((g: GroupData) => ({
                  label: g.name,
                  value: g.id,
                }))}
              />
            )}
          </Form.Item>
        )}
        {isAdmin && (
          <Form.Item name="tenant_id" label="Tenant">
            <Select
              showSearch
              placeholder="Select tenant..."
              optionFilterProp="label"
              allowClear
              options={(tenants || []).map((t: TenantData) => ({
                label: t.name,
                value: t.id,
              }))}
            />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}

export default UserForm;
