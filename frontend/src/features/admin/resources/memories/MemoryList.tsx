// =============================================================================
// PH Agent Hub — Admin MemoryList
// =============================================================================
// Ant Design Table with server-side search, source filter, sorting,
// pagination.
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
  Form,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listAdminMemories,
  deleteAdminMemory,
  updateAdminMemory,
  listTenants,
  MemoryData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../../../shared/hooks/useDebounce";

const { useBreakpoint } = Grid;
const { Text, Paragraph } = Typography;

export function MemoryList() {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchText, setSearchText] = useState("");
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;
  const [editVisible, setEditVisible] = useState(false);
  const [editingRecord, setEditingRecord] = useState<MemoryData | null>(null);
  const [detailVisible, setDetailVisible] = useState(false);
  const [viewingRecord, setViewingRecord] = useState<MemoryData | null>(null);
  const [editForm] = Form.useForm();

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-memories"],
    (p) => listAdminMemories({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-memory-list"],
    queryFn: () => listTenants(),
  });

  const tenantNameById = new Map((tenants?.items || []).map((t) => [t.id, t.name]));

  const deleteMutation = useMutation({
    mutationFn: deleteAdminMemory,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-memories"] });
      message.success("Memory entry deleted");
    },
  });

  const editMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { key?: string; value?: string } }) =>
      updateAdminMemory(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-memories"] });
      message.success("Memory entry updated");
      setEditVisible(false);
      setEditingRecord(null);
    },
  });

  const memoriesData = data?.items || [];
  const totalMemories = data?.total || 0;

  const columns = [
    {
      title: "Key",
      dataIndex: "key",
      key: "key",
      sorter: true,
      render: (v: string, record: MemoryData) => (
        <Space>
          <Text strong>{v}</Text>
          <Tag color={record.source === "automatic" ? "blue" : "green"}>
            {record.source}
          </Tag>
        </Space>
      ),
    },
    {
      title: "Value",
      dataIndex: "value",
      key: "value",
      ellipsis: true,
      render: (v: string, record: MemoryData) => (
        <Paragraph
          ellipsis={{ rows: 2 }}
          style={{ margin: 0, maxWidth: 300, cursor: "pointer" }}
          onClick={() => {
            setViewingRecord(record);
            setDetailVisible(true);
          }}
        >
          {v}
        </Paragraph>
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
    },
    {
      title: "Session",
      dataIndex: "session_id",
      key: "session_id",
      width: 120,
      ellipsis: true,
      responsive: ["xl" as const],
      render: (v: string | null) =>
        v ? <Text code style={{ fontSize: 11 }}>{v.slice(0, 8)}…</Text> : <Text type="secondary">—</Text>,
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
      width: 120,
      render: (_: unknown, record: MemoryData) => (
        <Space>
          <Button
            icon={<EyeOutlined />}
            size="small"
            aria-label={`View value for ${record.key}`}
            onClick={() => {
              setViewingRecord(record);
              setDetailVisible(true);
            }}
          />
          <Button
            icon={<EditOutlined />}
            size="small"
            aria-label={`Edit memory ${record.key}`}
            onClick={() => {
              setEditingRecord(record);
              setEditVisible(true);
            }}
          />
          <Popconfirm
            title="Delete this memory entry?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="Search by key, value…"
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
          placeholder="Source"
          allowClear
          style={{ width: 140 }}
          value={(params as Record<string, string | undefined>).source}
          onChange={(value) => updateParams({ source: value, page: 1 } as any)}
          options={[
            { label: "Manual", value: "manual" },
            { label: "Automatic", value: "automatic" },
          ]}
        />
      </Space>

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={memoriesData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalMemories,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          locale={{ emptyText: "No memory entries found" }}
          renderItem={(item) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Button
                  key="view"
                  icon={<EyeOutlined />}
                  size="small"
                  aria-label={`View value for ${item.key}`}
                  onClick={() => {
                    setViewingRecord(item);
                    setDetailVisible(true);
                  }}
                />,
                <Button
                  key="edit"
                  icon={<EditOutlined />}
                  size="small"
                  aria-label={`Edit memory ${item.key}`}
                  onClick={() => {
                    setEditingRecord(item);
                    setEditVisible(true);
                  }}
                />,
                <Popconfirm
                  key="del"
                  title="Delete this memory entry?"
                  onConfirm={() => deleteMutation.mutate(item.id)}
                >
                  <Button icon={<DeleteOutlined />} size="small" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={
                  <Space>
                    <Text strong>{item.key}</Text>
                    <Tag color={item.source === "automatic" ? "blue" : "green"}>
                      {item.source}
                    </Tag>
                  </Space>
                }
                description={
                  <>
                    <Paragraph
                      ellipsis={{ rows: 2 }}
                      style={{ margin: 0, cursor: "pointer" }}
                      onClick={() => {
                        setViewingRecord(item);
                        setDetailVisible(true);
                      }}
                    >
                      {item.value}
                    </Paragraph>
                    <Text type="secondary" style={{ fontSize: 11 }}>
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
          columns={columns}
          dataSource={memoriesData}
          rowKey="id"
          loading={isLoading}
          size="middle"
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalMemories,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange as any}
        />
      )}

      <Modal
        title="Edit Memory Entry"
        open={editVisible}
        onCancel={() => {
          setEditVisible(false);
          setEditingRecord(null);
        }}
        onOk={() => {
          editForm
            .validateFields()
            .then((values) => {
              if (editingRecord) {
                editMutation.mutate({
                  id: editingRecord.id,
                  data: {
                    key: values.key,
                    value: values.value,
                  },
                });
              }
            })
            .catch(() => {});
        }}
      >
        {editingRecord && (
          <Form
            form={editForm}
            layout="vertical"
            initialValues={{ key: editingRecord.key, value: editingRecord.value }}
          >
            <Form.Item name="key" label="Key" rules={[{ required: true }]}>
              <Input />
            </Form.Item>
            <Form.Item name="value" label="Value" rules={[{ required: true }]}>
              <Input.TextArea rows={6} />
            </Form.Item>
          </Form>
        )}
      </Modal>

      <Modal
        title={
          <Space direction="vertical" style={{ width: "100%" }}>
            <Text strong>Memory Value</Text>
            <Text type="secondary">Key: {viewingRecord?.key}</Text>
          </Space>
        }
        open={detailVisible}
        onCancel={() => {
          setDetailVisible(false);
          setViewingRecord(null);
        }}
        footer={null}
        width={600}
        forceRender
      >
        <Paragraph style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {viewingRecord?.value}
        </Paragraph>
      </Modal>
    </div>
  );
}

export default MemoryList;