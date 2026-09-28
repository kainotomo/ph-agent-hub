// =============================================================================
// PH Agent Hub — Admin Workflow List
// =============================================================================
// Read-only view of workflow definitions with enable/disable toggle and
// delete actions.  Shows unbound role status per row via the Roles column.
// =============================================================================

import { useEffect, useState } from "react";
import {
  Table,
  Button,
  Space,
  Tag,
  Popconfirm,
  Switch,
  message,
  Grid,
  List,
  Card,
  Typography,
  Select,
  Input,
  Empty,
} from "antd";
import { DeleteOutlined, SearchOutlined } from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  listWorkflows,
  deleteWorkflow,
  updateWorkflow,
  WorkflowDefinitionData,
  listUnboundRoles,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../../../shared/hooks/useDebounce";
import { useAuth } from "../../../../providers/AuthProvider";

const { useBreakpoint } = Grid;
const { Text } = Typography;

/** Per-row child component that fetches unbound roles for a single workflow. */
function UnboundRolesCell({ workflowId }: { workflowId: string }) {
  const { data, isLoading, isSuccess, isError } = useQuery({
    queryKey: ["admin-workflow-unbound-roles", workflowId],
    queryFn: () => listUnboundRoles(workflowId),
  });

  if (isLoading) return <Tag color="blue">checking…</Tag>;
  if (isError) return <Tag>unknown</Tag>;
  if (isSuccess && data.length > 0) {
    return (
      <>
        <Tag color="red">
          Unbound: {data.map((r) => r.role).join(", ")}
        </Tag>
        <Link to="/admin/model-roles">Bind roles</Link>
      </>
    );
  }
  return <Tag color="green">Bound</Tag>;
}

export function WorkflowList() {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const effectiveTenantId = searchParams.get("tenant_id") || user?.tenant_id || undefined;

  const [searchText, setSearchText] = useState("");
  const debouncedSearch = useDebounce(searchText, 300);
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable<WorkflowDefinitionData>(
    ["admin-workflows"],
    (p) => listWorkflows({ ...p, tenant_id: effectiveTenantId }),
    { tenant_id: effectiveTenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const deleteMutation = useMutation({
    mutationFn: deleteWorkflow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-workflows"] });
      message.success("Workflow deleted");
    },
  });

  const toggleEnabledMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateWorkflow(id, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-workflows"] });
      message.success("Workflow updated");
    },
    onError: (err: Error) => message.error(err.message || "Failed to update workflow"),
  });

  const workflows = data?.items || [];
  const totalWorkflows = data?.total || 0;

  if (isLoading) {
    return <Empty description="Loading workflows…" />;
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        Workflows
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 16 }}>
        A workflow is a tenant-scoped, validated definition of steps and conditional
        edges. An unbound role makes a run fail rather than fall back.
      </Typography.Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="Search by name or key…"
          prefix={<SearchOutlined />}
          allowClear
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value);
            updateParams({ page: 1 });
          }}
          style={{ width: 260 }}
        />
        <Select
          placeholder="Status"
          allowClear
          style={{ width: 150 }}
          value={params.enabled !== undefined ? String(params.enabled) : undefined}
          onChange={(value) =>
            updateParams({
              enabled: value !== undefined ? value === "true" : undefined,
              page: 1,
            })
          }
          options={[
            { label: "Enabled", value: "true" },
            { label: "Disabled", value: "false" },
          ]}
        />
      </Space>

      {workflows.length === 0 && (
        <Empty description="No workflows available" />
      )}

      {workflows.length > 0 && (
        isMobile ? (
          <List
            loading={false}
            dataSource={workflows}
            pagination={{
              current: data?.page || 1,
              pageSize: data?.page_size || 25,
              total: totalWorkflows,
              onChange: (p) => updateParams({ page: p }),
              showSizeChanger: false,
            }}
            renderItem={(workflow) => (
              <Card
                size="small"
                style={{ marginBottom: 8 }}
                actions={[
                  <Link key="edit" to={`/admin/workflows/${workflow.id}/edit`}>
                    Edit
                  </Link>,
                  <Popconfirm
                    key="delete"
                    title="Delete?"
                    onConfirm={() => deleteMutation.mutate(workflow.id)}
                  >
                    <Button icon={<DeleteOutlined />} type="link" danger />
                  </Popconfirm>,
                ]}
              >
                <Card.Meta
                  title={workflow.name}
                  description={
                    <Space direction="vertical" size={2}>
                      <Space>
                        <Tag>{workflow.key}</Tag>
                        <Tag>{workflow.visibility}</Tag>
                      </Space>
                      <UnboundRolesCell workflowId={workflow.id} />
                      <Switch
                        checked={workflow.enabled}
                        onChange={(checked) =>
                          toggleEnabledMutation.mutate({
                            id: workflow.id,
                            enabled: checked,
                          })
                        }
                        size="small"
                      />
                      <Text type="secondary">{workflow.updated_at}</Text>
                    </Space>
                  }
                />
              </Card>
            )}
          />
        ) : (
          <Table
            columns={[
              {
                title: "Name",
                dataIndex: "name",
                key: "name",
                ellipsis: true,
              },
              {
                title: "Key",
                dataIndex: "key",
                key: "key",
                ellipsis: true,
              },
              {
                title: "Visibility",
                dataIndex: "visibility",
                key: "visibility",
                render: (v: string) => <Tag>{v}</Tag>,
              },
              {
                title: "Roles",
                key: "roles",
                render: (_: unknown, record: WorkflowDefinitionData) => (
                  <UnboundRolesCell workflowId={record.id} />
                ),
              },
              {
                title: "Enabled",
                dataIndex: "enabled",
                key: "enabled",
                render: (_: boolean, record: WorkflowDefinitionData) => (
                  <Switch
                    checked={record.enabled}
                    onChange={(checked) =>
                      toggleEnabledMutation.mutate({ id: record.id, enabled: checked })
                    }
                  />
                ),
              },
              {
                title: "Updated",
                dataIndex: "updated_at",
                key: "updated_at",
                width: 180,
                responsive: ["md" as const],
                render: (v: string) => <Text type="secondary">{v}</Text>,
              },
              {
                title: "Actions",
                key: "actions",
                render: (_: unknown, record: WorkflowDefinitionData) => (
                  <Space>
                    <Link to={`/admin/workflows/${record.id}/edit`}>Edit</Link>
                    <Popconfirm
                      title="Delete this workflow?"
                      onConfirm={() => deleteMutation.mutate(record.id)}
                    >
                      <Button icon={<DeleteOutlined />} size="small" danger />
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
            dataSource={workflows}
            rowKey="id"
            loading={false}
            pagination={{
              current: data?.page || 1,
              pageSize: data?.page_size || 25,
              total: totalWorkflows,
              showSizeChanger: true,
              pageSizeOptions: ["10", "25", "50", "100"],
              showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
            }}
            onChange={handleTableChange}
          />
        )
      )}
    </div>
  );
}

export default WorkflowList;
