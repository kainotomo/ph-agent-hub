// =============================================================================
// PH Agent Hub — Admin A2A Server List
// =============================================================================
// Ant Design Table with server-side search, protocol binding / enabled
// filters, pagination, test connection, sync skills, create/edit/delete.
// =============================================================================

import { useState, useEffect } from "react";
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
  Tooltip,
} from "antd";
import {
  EditOutlined,
  DeleteOutlined,
  ApiOutlined,
  SyncOutlined,
  PlusOutlined,
  SearchOutlined,
  CheckCircleOutlined,
} from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listA2aServers,
  deleteA2aServer,
  updateA2aServer,
  testA2aServer,
  syncA2aServerTools,
  A2aServerData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";
import { A2aServerForm } from "./A2aServerForm";

const { useBreakpoint } = Grid;
const { Text } = Typography;

const BINDING_COLORS: Record<string, string> = {
  jsonrpc: "blue",
  rest: "green",
  grpc: "purple",
};

const BINDING_LABELS: Record<string, string> = {
  jsonrpc: "JSON-RPC",
  rest: "REST",
  grpc: "gRPC",
};

export function A2aServerList() {
  const [editingServer, setEditingServer] = useState<A2aServerData | null>(null);
  const [creating, setCreating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;
  const debouncedSearch = useDebounce(searchText, 300);

  const [bindingFilter, setBindingFilter] = useState<string | undefined>();
  const [enabledFilter, setEnabledFilter] = useState<boolean | undefined>();

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-a2a-servers"],
    (p) =>
      listA2aServers({
        ...p,
        tenant_id: tenantId,
        protocol_binding: bindingFilter,
        enabled: enabledFilter,
      }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  useEffect(() => {
    updateParams({ enabled: enabledFilter });
  }, [enabledFilter, updateParams]);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteA2aServer(id),
    onSuccess: () => {
      message.success("A2A server deleted");
      queryClient.invalidateQueries({ queryKey: ["admin-a2a-servers"] });
    },
    onError: () => message.error("Failed to delete A2A server"),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateA2aServer(id, { enabled }),
    onSuccess: () => {
      message.success("A2A server updated");
      queryClient.invalidateQueries({ queryKey: ["admin-a2a-servers"] });
    },
    onError: () => message.error("Failed to update A2A server"),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => testA2aServer(id),
    onSuccess: (result) => {
      if (result.connected) {
        message.success(`Connected! Agent: ${result.agent_name} (${result.skills.length} skill(s))`);
      } else {
        message.error(`Connection failed: ${result.error}`);
      }
    },
    onError: () => message.error("Failed to test connection"),
  });

  const syncMutation = useMutation({
    mutationFn: (id: string) => syncA2aServerTools(id),
    onSuccess: (result) => {
      message.success(
        `Synced: ${result.created} created, ${result.updated} updated, ${result.deprecated} deprecated`
      );
      queryClient.invalidateQueries({ queryKey: ["admin-a2a-servers"] });
      queryClient.invalidateQueries({ queryKey: ["admin-tools"] });
    },
    onError: () => message.error("Failed to sync tools"),
  });

  const columns = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      sorter: true,
      render: (name: string) => (
        <Space>
          <ApiOutlined />
          <span>{name}</span>
        </Space>
      ),
    },
    {
      title: "Protocol",
      dataIndex: "protocol_binding",
      key: "protocol_binding",
      width: 120,
      render: (binding: string) => (
        <Tag color={BINDING_COLORS[binding] || "default"}>
          {BINDING_LABELS[binding] || binding}
        </Tag>
      ),
    },
    {
      title: "URL",
      dataIndex: "url",
      key: "url",
      width: 250,
      render: (url: string | null) => {
        if (url) return <Text ellipsis={{ tooltip: url }}>{url}</Text>;
        return <Text type="secondary">—</Text>;
      },
    },
    {
      title: "Auth",
      dataIndex: "auth_scheme",
      key: "auth_scheme",
      width: 90,
      render: (scheme: string | null) => {
        if (!scheme || scheme === "none") return <Text type="secondary">None</Text>;
        return <Tag>{scheme}</Tag>;
      },
    },
    {
      title: "Enabled",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      align: "center" as const,
      render: (enabled: boolean, record: A2aServerData) => (
        <Switch
          checked={enabled}
          onChange={(checked) =>
            toggleMutation.mutate({ id: record.id, enabled: checked })
          }
          size="small"
        />
      ),
    },
    {
      title: "Actions",
      key: "actions",
      width: 280,
      render: (_: unknown, record: A2aServerData) => (
        <Space size="small">
          <Tooltip title="Edit">
            <Button
              type="link"
              size="small"
              icon={<EditOutlined />}
              onClick={() => setEditingServer(record)}
            />
          </Tooltip>
          <Tooltip title="Test Connection">
            <Button
              type="link"
              size="small"
              icon={<CheckCircleOutlined />}
              loading={testMutation.isPending && testMutation.variables === record.id}
              onClick={() => testMutation.mutate(record.id)}
            />
          </Tooltip>
          <Tooltip title="Sync Skills">
            <Button
              type="link"
              size="small"
              icon={<SyncOutlined />}
              loading={syncMutation.isPending && syncMutation.variables === record.id}
              onClick={() => syncMutation.mutate(record.id)}
            />
          </Tooltip>
          <Popconfirm
            title="Delete A2A server?"
            description="This will also remove all synced tools."
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Tooltip title="Delete">
              <Button type="link" size="small" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // Mobile card view
  if (isMobile) {
    return (
      <div style={{ padding: 16 }}>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Space wrap>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
              Add A2A Server
            </Button>
            <Input
              placeholder="Search..."
              prefix={<SearchOutlined />}
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              style={{ width: 200 }}
              allowClear
            />
            <Select
              placeholder="Protocol"
              allowClear
              style={{ width: 140 }}
              value={bindingFilter}
              onChange={setBindingFilter}
              options={[
                { value: "jsonrpc", label: "JSON-RPC" },
                { value: "rest", label: "REST" },
                { value: "grpc", label: "gRPC" },
              ]}
            />
          </Space>

          {isLoading ? (
            <Text>Loading...</Text>
          ) : (
            <List
              dataSource={data?.items || []}
              renderItem={(item: A2aServerData) => (
                <Card
                  size="small"
                  style={{ marginBottom: 8 }}
                  actions={[
                    <EditOutlined key="edit" onClick={() => setEditingServer(item)} />,
                    <CheckCircleOutlined
                      key="test"
                      onClick={() => testMutation.mutate(item.id)}
                    />,
                    <SyncOutlined
                      key="sync"
                      onClick={() => syncMutation.mutate(item.id)}
                    />,
                    <Popconfirm
                      title="Delete?"
                      onConfirm={() => deleteMutation.mutate(item.id)}
                    >
                      <DeleteOutlined key="delete" />
                    </Popconfirm>,
                  ]}
                >
                  <Card.Meta
                    avatar={<ApiOutlined />}
                    title={
                      <Space>
                        {item.name}
                        <Tag color={BINDING_COLORS[item.protocol_binding]}>
                          {BINDING_LABELS[item.protocol_binding]}
                        </Tag>
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={0}>
                        <Text ellipsis={{ tooltip: item.url }}>{item.url}</Text>
                        <Switch
                          checked={item.enabled}
                          size="small"
                          onChange={(checked) =>
                            toggleMutation.mutate({ id: item.id, enabled: checked })
                          }
                        />
                      </Space>
                    }
                  />
                </Card>
              )}
            />
          )}
        </Space>

        <A2aServerForm
          open={creating}
          server={null}
          onClose={() => setCreating(false)}
        />
        <A2aServerForm
          open={!!editingServer}
          server={editingServer}
          onClose={() => setEditingServer(null)}
        />
      </div>
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
          Add A2A Server
        </Button>
        <Input
          placeholder="Search by name or URL..."
          prefix={<SearchOutlined />}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{ width: 250 }}
          allowClear
        />
        <Select
          placeholder="Protocol"
          allowClear
          style={{ width: 140 }}
          value={bindingFilter}
          onChange={setBindingFilter}
          options={[
            { value: "jsonrpc", label: "JSON-RPC" },
            { value: "rest", label: "REST" },
            { value: "grpc", label: "gRPC" },
          ]}
        />
        <Select
          placeholder="Status"
          allowClear
          style={{ width: 120 }}
          value={enabledFilter !== undefined ? (enabledFilter ? "enabled" : "disabled") : undefined}
          onChange={(val) =>
            setEnabledFilter(val === "enabled" ? true : val === "disabled" ? false : undefined)
          }
          options={[
            { value: "enabled", label: "Enabled" },
            { value: "disabled", label: "Disabled" },
          ]}
        />
      </Space>

      <Table
        dataSource={data?.items || []}
        columns={columns}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: params.page,
          pageSize: params.page_size,
          total: data?.total || 0,
          showSizeChanger: true,
          pageSizeOptions: ["10", "25", "50", "100"],
          showTotal: (total: number) => `${total} server(s)`,
        }}
        onChange={handleTableChange}
        scroll={{ x: 800 }}
        size="middle"
      />

      <A2aServerForm
        open={creating}
        server={null}
        onClose={() => setCreating(false)}
      />
      <A2aServerForm
        open={!!editingServer}
        server={editingServer}
        onClose={() => setEditingServer(null)}
      />
    </div>
  );
}

export default A2aServerList;
