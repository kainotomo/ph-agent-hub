// =============================================================================
// PH Agent Hub — Admin MCP Server List
// =============================================================================
// Ant Design Table with server-side search, transport/enabled filters,
// pagination, test connection, sync tools, create/edit/delete.
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
  listMcpServers,
  deleteMcpServer,
  updateMcpServer,
  testMcpServer,
  syncMcpServerTools,
  McpServerData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";
import { McpServerForm } from "./McpServerForm";

const { useBreakpoint } = Grid;
const { Text } = Typography;

const TRANSPORT_COLORS: Record<string, string> = {
  stdio: "blue",
  streamable_http: "green",
  websocket: "purple",
};

const TRANSPORT_LABELS: Record<string, string> = {
  stdio: "Stdio",
  streamable_http: "Streamable HTTP",
  websocket: "WebSocket",
};

export function McpServerList() {
  const [editingServer, setEditingServer] = useState<McpServerData | null>(null);
  const [creating, setCreating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;
  const debouncedSearch = useDebounce(searchText, 300);

  const [transportFilter, setTransportFilter] = useState<string | undefined>();
  const [enabledFilter, setEnabledFilter] = useState<boolean | undefined>();

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-mcp-servers"],
    (p) =>
      listMcpServers({
        ...p,
        tenant_id: tenantId,
        transport: transportFilter,
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
    mutationFn: (id: string) => deleteMcpServer(id),
    onSuccess: () => {
      message.success("MCP server deleted");
      queryClient.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
    },
    onError: () => message.error("Failed to delete MCP server"),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateMcpServer(id, { enabled }),
    onSuccess: () => {
      message.success("MCP server updated");
      queryClient.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
    },
    onError: () => message.error("Failed to update MCP server"),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => testMcpServer(id),
    onSuccess: (result) => {
      if (result.connected) {
        message.success(`Connected! Found ${result.tools.length} tool(s)`);
      } else {
        message.error(`Connection failed: ${result.error}`);
      }
    },
    onError: () => message.error("Failed to test connection"),
  });

  const syncMutation = useMutation({
    mutationFn: (id: string) => syncMcpServerTools(id),
    onSuccess: (result) => {
      message.success(
        `Synced: ${result.created} created, ${result.updated} updated, ${result.deprecated} deprecated`
      );
      queryClient.invalidateQueries({ queryKey: ["admin-mcp-servers"] });
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
      title: "Transport",
      dataIndex: "transport",
      key: "transport",
      width: 160,
      render: (transport: string) => (
        <Tag color={TRANSPORT_COLORS[transport] || "default"}>
          {TRANSPORT_LABELS[transport] || transport}
        </Tag>
      ),
    },
    {
      title: "URL / Command",
      key: "endpoint",
      width: 250,
      render: (_: unknown, record: McpServerData) => {
        if (record.url) return <Text ellipsis={{ tooltip: record.url }}>{record.url}</Text>;
        if (record.command) return <Text code>{record.command}</Text>;
        return <Text type="secondary">—</Text>;
      },
    },
    {
      title: "Tools",
      key: "tools",
      width: 80,
      align: "center" as const,
      render: () => (
        <Text type="secondary">—</Text>
      ),
    },
    {
      title: "Enabled",
      dataIndex: "enabled",
      key: "enabled",
      width: 100,
      align: "center" as const,
      render: (enabled: boolean, record: McpServerData) => (
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
      render: (_: unknown, record: McpServerData) => (
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
          <Tooltip title="Sync Tools">
            <Button
              type="link"
              size="small"
              icon={<SyncOutlined />}
              loading={syncMutation.isPending && syncMutation.variables === record.id}
              onClick={() => syncMutation.mutate(record.id)}
            />
          </Tooltip>
          <Popconfirm
            title="Delete MCP server?"
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
              Add MCP Server
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
              placeholder="Transport"
              allowClear
              style={{ width: 140 }}
              value={transportFilter}
              onChange={setTransportFilter}
              options={[
                { value: "stdio", label: "Stdio" },
                { value: "streamable_http", label: "Streamable HTTP" },
                { value: "websocket", label: "WebSocket" },
              ]}
            />
          </Space>

          {isLoading ? (
            <Text>Loading...</Text>
          ) : (
            <List
              dataSource={data?.items || []}
              renderItem={(item: McpServerData) => (
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
                        <Tag color={TRANSPORT_COLORS[item.transport]}>
                          {TRANSPORT_LABELS[item.transport]}
                        </Tag>
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={0}>
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

        <McpServerForm
          open={creating}
          server={null}
          onClose={() => setCreating(false)}
        />
        <McpServerForm
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
          Add MCP Server
        </Button>
        <Input
          placeholder="Search by name..."
          prefix={<SearchOutlined />}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{ width: 250 }}
          allowClear
        />
        <Select
          placeholder="Transport"
          allowClear
          style={{ width: 160 }}
          value={transportFilter}
          onChange={setTransportFilter}
          options={[
            { value: "stdio", label: "Stdio" },
            { value: "streamable_http", label: "Streamable HTTP" },
            { value: "websocket", label: "WebSocket" },
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

      <McpServerForm
        open={creating}
        server={null}
        onClose={() => setCreating(false)}
      />
      <McpServerForm
        open={!!editingServer}
        server={editingServer}
        onClose={() => setEditingServer(null)}
      />
    </div>
  );
}

export default McpServerList;
