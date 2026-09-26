// =============================================================================
// PH Agent Hub — Admin Model Role List
// =============================================================================
// Read-only view of model-role bindings and enabled models. One card per role
// showing what is bound and whether the role will fail at run time.
// =============================================================================

import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Card, Empty, message, Popconfirm, Select, Space, Spin, Tag, Typography } from "antd";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../../../providers/AuthProvider";
import { listModelRoleBindings, listModels, setModelRoleBindings, clearModelRoleBindings } from "../../services/admin";

export function ModelRoleList() {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const effectiveTenantId = searchParams.get("tenant_id") || user?.tenant_id || undefined;

  const { data: bindings, isLoading: bindingsLoading } = useQuery({
    queryKey: ["admin-model-role-bindings", effectiveTenantId],
    queryFn: () => listModelRoleBindings({ tenant_id: effectiveTenantId }),
    enabled: Boolean(effectiveTenantId),
  });

  const { data: listModelsData, isLoading: modelsLoading } = useQuery({
    queryKey: ["admin-model-role-options", effectiveTenantId],
    queryFn: () => listModels({ tenant_id: effectiveTenantId, enabled: true, page_size: 200 }),
    enabled: Boolean(effectiveTenantId),
  });

  const isLoading = bindingsLoading || modelsLoading;
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, string[]>>({});

  useEffect(() => {
    if (bindings) {
      const d: Record<string, string[]> = {};
      for (const binding of bindings) {
        d[binding.role] = binding.models.map((m) => m.id);
      }
      setDrafts(d);
    }
  }, [bindings]);

  const roleList = bindings || [];

  const options = useMemo(() => {
    const allModels: Array<{ id: string; name: string; enabled: boolean }> = [];
    const seenIds = new Set<string>();

    // Start with enabled models from the listModels query
    for (const m of listModelsData?.items || []) {
      if (!seenIds.has(m.id)) {
        seenIds.add(m.id);
        allModels.push({ id: m.id, name: m.name, enabled: m.enabled });
      }
    }

    // Then append every model present in any binding that is not already included
    for (const binding of roleList) {
      for (const m of binding.models) {
        if (!seenIds.has(m.id)) {
          seenIds.add(m.id);
          allModels.push(m);
        }
      }
    }

    return allModels.map((m) => ({
      label: m.enabled ? m.name : `${m.name} (disabled)`,
      value: m.id,
    }));
  }, [listModelsData, roleList]);

  const saveMutation = useMutation({
    mutationFn: ({ role, modelIds }: { role: string; modelIds: string[] }) => setModelRoleBindings(role, modelIds, { tenant_id: effectiveTenantId }),
    onSuccess: () => {
      message.success("Role binding saved");
      queryClient.invalidateQueries({ queryKey: ["admin-model-role-bindings"] });
    },
    onError: (err: Error) => message.error(err.message || "Failed to save role binding"),
  });

  const clearMutation = useMutation({
    mutationFn: (role: string) => clearModelRoleBindings(role, { tenant_id: effectiveTenantId }),
    onSuccess: () => {
      message.success("Role binding cleared");
      queryClient.invalidateQueries({ queryKey: ["admin-model-role-bindings"] });
    },
    onError: (err: Error) => message.error(err.message || "Failed to clear role binding"),
  });

  if (isLoading) {
    return <Spin />;
  }

  if (roleList.length === 0) {
    return <Empty description="No model roles available" />;
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        Model Roles
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 16 }}>
        Workflows reference model roles such as @reasoning. A role with no enabled model
        bound to it fails at run time.
      </Typography.Paragraph>

      {roleList.map((binding) => (
        <Card
          key={binding.role}
          title={binding.role}
          style={{ marginBottom: 16 }}
        >
          {binding.models.length === 0 ? (
            <Alert type="error" showIcon message="Unbound — workflows using this role will fail" />
          ) : binding.models.every((m) => !m.enabled) ? (
            <Alert type="warning" showIcon message="No enabled model — workflows using this role will fail" />
          ) : (
            binding.models.map((m) => (
              <Tag
                key={m.id}
                color={m.enabled ? "blue" : "default"}
                style={{ marginRight: 8, marginBottom: 4 }}
              >
                {m.name}{!m.enabled ? " (disabled)" : ""}
              </Tag>
            ))
          )}
          <Select mode="multiple" style={{ width: "100%", marginTop: 12 }} placeholder="Select one or more models" value={drafts[binding.role] ?? []} onChange={(ids: string[]) => setDrafts((d) => ({ ...d, [binding.role]: ids }))} options={options} />
          <Space style={{ marginTop: 12 }}>
            <Button type="primary" aria-label={`Save ${binding.role}`} loading={saveMutation.isPending} onClick={() => saveMutation.mutate({ role: binding.role, modelIds: drafts[binding.role] ?? [] })}>Save</Button>
            <Popconfirm title="Clear all models bound to this role?" onConfirm={() => clearMutation.mutate(binding.role)}><Button danger aria-label={`Clear ${binding.role}`}>Clear</Button></Popconfirm>
          </Space>
        </Card>
      ))}
    </div>
  );
}

export default ModelRoleList;
