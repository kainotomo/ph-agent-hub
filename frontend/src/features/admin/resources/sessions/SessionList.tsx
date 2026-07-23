// =============================================================================
// PH Agent Hub — Admin SessionList
// =============================================================================
// Ant Design Table with server-side search, pinned/temp/tag filters,
// sorting, pagination.
// =============================================================================

import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Table,
  Button,
  Tag,
  Popconfirm,
  Input,
  Space,
  message,
  Grid,
  List,
  Card,
  Typography,
  Select,
  Modal,
} from "antd";
import {
  DeleteOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listAdminSessions,
  deleteAdminSession,
  deleteAdminSessions,
  listTenants,
  AdminSessionData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function SessionList() {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchText, setSearchText] = useState("");
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-sessions"],
    (p) => listAdminSessions({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-session-list"],
    queryFn: () => listTenants(),
  });

  const tenantNameById = new Map((tenants?.items || []).map((t) => [t.id, t.name]));

  const deleteMutation = useMutation({
    mutationFn: deleteAdminSession,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-sessions"] });
      message.success("Session deleted");
    },
  });

  const batchDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => deleteAdminSessions(ids),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["admin-sessions"] });
      if (data.deleted > 0) {
        message.success(`Deleted ${data.deleted} session${data.deleted !== 1 ? "s" : ""}`);
      }
      if (data.skipped.length > 0) {
        message.warning(`${data.skipped.length} session${data.skipped.length !== 1 ? "s" : ""} skipped`);
      }
      setSelectedRowKeys([]);
    },
    onError: (err: Error) => {
      message.error(`Failed to delete sessions: ${err.message}`);
      setSelectedRowKeys([]);
    },
  });

  const sessionsData = data?.items || [];
  const totalSessions = data?.total || 0;

  const columns = [
    {
      title: "Title",
      dataIndex: "title",
      key: "title",
      sorter: true,
      render: (v: string, record: AdminSessionData) => (
        <Space>
          <Text strong>{v}</Text>
          {record.is_pinned && <Tag color="orange">Pinned</Tag>}
          {record.is_temporary && <Tag color="blue">Temp</Tag>}
        </Space>
      ),
    },
    {
      title: "Tags",
      dataIndex: "tags",
      key: "tags",
      render: (tags: AdminSessionData["tags"]) => (
        <Space size={4} wrap>
          {(tags || []).slice(0, 5).map((t) => (
            <Tag key={t.id} color={t.color || "default"} style={{ fontSize: 11 }}>
              {t.name}
            </Tag>
          ))}
          {(tags || []).length > 5 && (
            <Text type="secondary" style={{ fontSize: 11 }}>
              +{tags.length - 5} more
            </Text>
          )}
        </Space>
      ),
    },
    {
      title: "Tenant",
      dataIndex: "tenant_id",
      key: "tenant_id",
      width: 120,
      ellipsis: true,
      responsive: ["lg" as const],
      render: (v: string) => {
        const tenantName = tenantNameById.get(v);
        return tenantName ? <Text>{tenantName}</Text> : <Text type="secondary">Unknown tenant</Text>;
      },
    },
    {
      title: "User",
      dataIndex: "user_id",
      key: "user_id",
      width: 120,
      ellipsis: true,
      responsive: ["lg" as const],
      render: (v: string) => <Text code style={{ fontSize: 11 }}>{v.slice(0, 8)}…</Text>,
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      width: 120,
      responsive: ["md" as const],
      render: (v: string) => new Date(v).toLocaleDateString(),
    },
    {
      title: "Actions",
      key: "actions",
      width: 80,
      render: (_: unknown, record: AdminSessionData) => (
        <Popconfirm
          title="Delete this session?"
          onConfirm={() => deleteMutation.mutate(record.id)}
        >
          <Button icon={<DeleteOutlined />} size="small" danger />
        </Popconfirm>
      ),
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: (keys: React.Key[]) => setSelectedRowKeys(keys),
  };

  const handleBatchDelete = () => {
    const ids = selectedRowKeys as string[];
    Modal.confirm({
      title: `Delete ${ids.length} session${ids.length !== 1 ? "s" : ""}?`,
      content: `This will permanently delete ${ids.length} session${ids.length !== 1 ? "s" : ""} and all their messages. This action cannot be undone.`,
      okText: "Delete",
      okButtonProps: { danger: true },
      cancelText: "Cancel",
      onOk: () => batchDeleteMutation.mutate(ids),
    });
  };

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="Search by title…"
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
          placeholder="Pinned"
          allowClear
          style={{ width: 120 }}
          value={params.is_pinned !== undefined ? String(params.is_pinned) : undefined}
          onChange={(value) =>
            updateParams({
              is_pinned: value !== undefined ? value === "true" : undefined,
              page: 1,
            })
          }
          options={[
            { label: "Pinned", value: "true" },
            { label: "Not Pinned", value: "false" },
          ]}
        />
        {selectedRowKeys.length > 0 && (
          <>
            <Text type="secondary">{selectedRowKeys.length} selected</Text>
            <Button
              type="primary"
              danger
              size="small"
              loading={batchDeleteMutation.isPending}
              onClick={handleBatchDelete}
            >
              Delete Selected
            </Button>
          </>
        )}
      </Space>

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={sessionsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalSessions,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          locale={{ emptyText: "No sessions found" }}
          renderItem={(item) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Popconfirm
                  key="del"
                  title="Delete this session?"
                  onConfirm={() => deleteMutation.mutate(item.id)}
                >
                  <Button icon={<DeleteOutlined />} size="small" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={
                  <Space>
                    <Text strong>{item.title}</Text>
                    {item.is_pinned && <Tag color="orange">Pinned</Tag>}
                  </Space>
                }
                description={
                  <>
                    <Space size={4} wrap style={{ marginBottom: 4 }}>
                      {(item.tags || []).map((t) => (
                        <Tag key={t.id} color={t.color || "default"} style={{ fontSize: 10 }}>
                          {t.name}
                        </Tag>
                      ))}
                    </Space>
                    <Text type="secondary" style={{ fontSize: 11, display: "block" }}>
                      User: {item.user_id.slice(0, 8)}… ·{" "}
                      {new Date(item.created_at).toLocaleDateString()}
                    </Text>
                  </>
                }
              />
            </Card>
          )}
        />
      ) : (
        <Table
          rowKey="id"
          rowSelection={rowSelection}
          columns={columns}
          dataSource={sessionsData}
          loading={isLoading}
          locale={{ emptyText: "No sessions found" }}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalSessions,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange}
        />
      )}
    </div>
  );
}

export default SessionList;
