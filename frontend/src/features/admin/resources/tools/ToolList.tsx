// =============================================================================
// PH Agent Hub — Admin ToolList
// =============================================================================
// Ant Design Table/List with server-side search, type/category/enabled
// filters, sorting, pagination.
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
} from "antd";
import { EditOutlined, DeleteOutlined, CopyOutlined, SearchOutlined, EyeOutlined, EyeInvisibleOutlined } from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listTools,
  deleteTool,
  updateTool,
  listTenants,
  ToolData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";
import { ToolForm } from "./ToolForm";

const { useBreakpoint } = Grid;

export function ToolList() {
  const [editingTool, setEditingTool] = useState<ToolData | null>(null);
  const [duplicatingTool, setDuplicatingTool] = useState<ToolData | null>(null);
  const [creating, setCreating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-tools"],
    (p) => listTools({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-tool-list"],
    queryFn: () => listTenants(),
  });

  const tenantNameById = new Map((tenants?.items || []).map((t) => [t.id, t.name]));

  const CATEGORY_LABELS: Record<string, string> = {
    financial: "Financial",
    web: "Web",
    enterprise: "Enterprise",
    utility: "Utility",
    custom: "Custom",
    mcp: "MCP",
    system: "System",
    general: "General",
    communication: "Communication",
    devops: "DevOps",
    productivity: "Productivity",
    creative: "Creative",
  };

  const CATEGORY_COLORS: Record<string, string> = {
    financial: "green",
    web: "blue",
    enterprise: "orange",
    utility: "cyan",
    custom: "purple",
    mcp: "teal",
    system: "default",
    general: "default",
    communication: "geekblue",
    devops: "red",
    productivity: "gold",
    creative: "magenta",
  };

  const CATEGORY_OPTIONS = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({
    value,
    label,
  }));

  const deleteMutation = useMutation({
    mutationFn: deleteTool,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tools"] });
      message.success("Tool deleted");
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to delete tool");
    },
  });

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateTool(id, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tools"] });
    },
  });

  const columns = [
    { title: "Name", dataIndex: "name", key: "name", sorter: true,
      render: (v: string, record: ToolData) => (
        <Typography.Text ellipsis={{ tooltip: record.description || undefined }}>
          {v}
        </Typography.Text>
      ),
    },
    { title: "Description", dataIndex: "description", key: "description", 
      ellipsis: true, width: 250, responsive: ["xl" as const],
    {
      title: "Category",
      dataIndex: "category",
      key: "category",
      sorter: true,
      render: (v: string) => (
        <Tag color={CATEGORY_COLORS[v] || "default"}>
          {CATEGORY_LABELS[v] || v}
        </Tag>
      ),
    },
    {
      title: "Type",
      dataIndex: "type",
      key: "type",
      sorter: true,
      render: (v: string, record: ToolData) => (
        <Space size={4}>
          <Tag color="purple">{v}</Tag>
          {v === "custom" && record.code && (
            <Tag color="green" style={{ fontSize: 11 }}>code</Tag>
          )}
        </Space>
      ),
    },
    {
      title: "Tenant",
      dataIndex: "tenant_id",
      key: "tenant_id",
      width: 130,
      ellipsis: true,
      responsive: ["lg" as const],
      render: (v: string) => {
        const tenantName = tenantNameById.get(v);
        return tenantName ? (
          <Typography.Text>{tenantName}</Typography.Text>
        ) : (
          <Typography.Text type="secondary">Unknown tenant</Typography.Text>
        );
      },
    },
    {
      title: "Enabled",
      dataIndex: "enabled",
      key: "enabled",
      render: (_: boolean, record: ToolData) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) =>
            toggleEnabled.mutate({ id: record.id, enabled: checked })
          }
        />
      ),
    },
    {
      title: "Public",
      dataIndex: "is_public",
      key: "is_public",
      width: 90,
      filters: [
        { text: "Public", value: true },
        { text: "Private", value: false },
      ],
      onFilter: (value: boolean | React.Key, record: ToolData) => record.is_public === value,
      render: (v: boolean) =>
        v ? (
          <Tag icon={<EyeOutlined />} color="blue">
            Public
          </Tag>
        ) : (
          <Tag icon={<EyeInvisibleOutlined />}>
            Private
          </Tag>
        ),
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: ToolData) => (
        <Space>
          <Button
            icon={<EditOutlined />}
            size="small"
            onClick={() => setEditingTool(record)}
          />
          <Button
            icon={<CopyOutlined />}
            size="small"
            onClick={() => setDuplicatingTool(record)}
            title="Duplicate"
          />
          <Popconfirm
            title="Delete this tool?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const toolsData = data?.items || [];
  const totalTools = data?.total || 0;

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={() => setCreating(true)}>
          Create Tool
        </Button>
        <Input
          placeholder="Search by name, type…"
          prefix={<SearchOutlined />}
          allowClear
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value);
            updateParams({ page: 1 });
          }}
          style={{ width: 220 }}
        />
        <Select
          allowClear
          placeholder="Filter by category"
          style={{ width: 160 }}
          value={params.category as string | undefined}
          onChange={(value) => updateParams({ category: value, page: 1 })}
          options={CATEGORY_OPTIONS}
        />
        <Select
          allowClear
          placeholder="Status"
          style={{ width: 120 }}
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

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={toolsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalTools,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          renderItem={(tool) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Button
                  icon={<EditOutlined />}
                  type="link"
                  onClick={() => setEditingTool(tool)}
                />,
                <Button
                  icon={<CopyOutlined />}
                  type="link"
                  onClick={() => setDuplicatingTool(tool)}
                  title="Duplicate"
                />,
                <Popconfirm
                  title="Delete?"
                  onConfirm={() => deleteMutation.mutate(tool.id)}
                >
                  <Button icon={<DeleteOutlined />} type="link" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={tool.name}
                description={
                  <Space direction="vertical" size={2}>
                    <Space size={4}>
                      <Tag color={CATEGORY_COLORS[tool.category] || "default"}>
                        {CATEGORY_LABELS[tool.category] || tool.category}
                      </Tag>
                      <Tag color="purple">{tool.type}</Tag>
                      {tool.type === "custom" && tool.code && (
                        <Tag color="green" style={{ fontSize: 11 }}>code</Tag>
                      )}
                      {tool.is_public ? (
                        <Tag icon={<EyeOutlined />} color="blue" style={{ fontSize: 11 }}>Public</Tag>
                      ) : (
                        <Tag icon={<EyeInvisibleOutlined />} style={{ fontSize: 11 }}>Private</Tag>
                      )}
                    </Space>
                    <Switch
                      checked={tool.enabled}
                      onChange={(checked) =>
                        toggleEnabled.mutate({
                          id: tool.id,
                          enabled: checked,
                        })
                      }
                      size="small"
                    />
                  </Space>
                }
              />
            </Card>
          )}
        />
      ) : (
        <Table
          columns={columns}
          dataSource={toolsData}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalTools,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange}
        />
      )}

      <ToolForm
        open={!!editingTool || creating}
        tool={creating ? null : editingTool}
        onClose={() => {
          setEditingTool(null);
          setCreating(false);
        }}
      />

      <ToolForm
        open={!!duplicatingTool}
        tool={null}
        duplicateFrom={duplicatingTool}
        onClose={() => setDuplicatingTool(null)}
      />
    </div>
  );
}

export default ToolList;
