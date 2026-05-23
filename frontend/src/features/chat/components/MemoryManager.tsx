// =============================================================================
// PH Agent Hub — MemoryManager
// =============================================================================
// Ant Design Drawer+List; CRUD on /memory; search/filter, edit, expandable
// values; shows automatic vs manual entries.
// =============================================================================

import { useState, useMemo } from "react";
import {
  Alert,
  Drawer,
  List,
  Button,
  Typography,
  Popconfirm,
  message,
  Empty,
  Tag,
  Modal,
  Form,
  Input,
  Space,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listMemory,
  createMemory,
  deleteMemory,
  updateMemory,
} from "../services/chat";

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

interface MemoryManagerProps {
  open: boolean;
  onClose: () => void;
  sessionId?: string;
}

export function MemoryManager({
  open,
  onClose,
  sessionId,
}: MemoryManagerProps) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editKey, setEditKey] = useState("");
  const [editValue, setEditValue] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [searchText, setSearchText] = useState("");
  const [form] = Form.useForm();
  const queryClient = useQueryClient();

  const { data: entries, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["memory", sessionId],
    queryFn: () => listMemory(sessionId),
    enabled: open || !!sessionId,
  });

  const createMutation = useMutation({
    mutationFn: (data: { key: string; value: string }) =>
      createMemory({ ...data, session_id: sessionId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId] });
      message.success("Memory entry added");
      setAdding(false);
      form.resetFields();
    },
    onError: (err) => message.error(`Failed to add memory: ${(err as Error).message}`),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { key?: string; value?: string } }) =>
      updateMemory(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId] });
      message.success("Memory entry updated");
      setEditing(null);
    },
    onError: (err) => message.error(`Failed to update memory: ${(err as Error).message}`),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteMemory(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId] });
      message.success("Memory entry deleted");
    },
    onError: (err) => message.error(`Failed to delete memory: ${(err as Error).message}`),
  });

  // Filter entries by search text (case-insensitive match on key or value)
  const filteredEntries = useMemo(() => {
    if (!entries) return [];
    if (!searchText.trim()) return entries;
    const lower = searchText.toLowerCase();
    return entries.filter(
      (e) =>
        e.key.toLowerCase().includes(lower) ||
        e.value.toLowerCase().includes(lower),
    );
  }, [entries, searchText]);

  const handleAdd = async () => {
    const values = await form.validateFields();
    await createMutation.mutateAsync(values);
  };

  const handleEdit = async () => {
    if (!editKey.trim() || !editValue.trim()) return;
    await updateMutation.mutateAsync({
      id: editing!,
      data: { key: editKey, value: editValue },
    });
  };

  const openEdit = (item: { id: string; key: string; value: string }) => {
    setEditing(item.id);
    setEditKey(item.key);
    setEditValue(item.value);
  };

  return (
    <Drawer
      title="Memory"
      open={open}
      onClose={onClose}
      width={480}
      extra={
        <Space>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setAdding(true)}
          >
            Add Entry
          </Button>
        </Space>
      }
    >
      {/* Search */}
      <Input
        placeholder="Search by key or value…"
        prefix={<SearchOutlined />}
        allowClear
        value={searchText}
        onChange={(e) => setSearchText(e.target.value)}
        style={{ marginBottom: 16 }}
      />

      {isError ? (
        <Alert
          type="error"
          message="Failed to load memories"
          description={error?.message || "An error occurred"}
          action={<Button size="small" onClick={() => refetch()}>Retry</Button>}
          showIcon
          style={{ marginBottom: 16 }}
        />
      ) : (
        <List
          loading={isLoading}
          dataSource={filteredEntries}
          locale={{ emptyText: <Empty description="No memory entries" /> }}
        renderItem={(item) => {
          const isExpanded = expandedId === item.id;
          return (
            <List.Item
              actions={[
                <Button
                  icon={<EditOutlined />}
                  size="small"
                  onClick={() => openEdit(item)}
                />,
                <Popconfirm
                  title="Delete this entry?"
                  onConfirm={() => deleteMutation.mutate(item.id)}
                >
                  <Button
                    icon={<DeleteOutlined />}
                    size="small"
                    danger
                  />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Text strong>{item.key}</Text>
                    <Tag
                      color={
                        item.source === "automatic"
                          ? "blue"
                          : "green"
                      }
                    >
                      {item.source}
                    </Tag>
                  </Space>
                }
                description={
                  <div>
                    <Paragraph
                      ellipsis={isExpanded ? undefined : { rows: 2 }}
                      style={{
                        margin: 0,
                        cursor: "pointer",
                        whiteSpace: isExpanded ? "pre-wrap" : undefined,
                      }}
                      onClick={() =>
                        setExpandedId(isExpanded ? null : item.id)
                      }
                    >
                      {item.value}
                    </Paragraph>
                    {!isExpanded && item.value && item.value.length > 80 && (
                      <Text
                        type="secondary"
                        style={{ fontSize: 11, cursor: "pointer" }}
                        onClick={() => setExpandedId(item.id)}
                      >
                        Click to expand
                      </Text>
                    )}
                    {isExpanded && (
                      <Text
                        type="secondary"
                        style={{ fontSize: 11, cursor: "pointer" }}
                        onClick={() => setExpandedId(null)}
                      >
                        Click to collapse
                      </Text>
                    )}
                  </div>
                }
              />
            </List.Item>
          );
        }}
      />
      )}

      {/* Add Modal */}
      <Modal
        title="Add Memory Entry"
        open={adding}
        onOk={handleAdd}
        onCancel={() => {
          setAdding(false);
          form.resetFields();
        }}
        confirmLoading={createMutation.isPending}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="key"
            label="Key"
            rules={[{ required: true }]}
          >
            <Input placeholder="e.g., user_preference" />
          </Form.Item>
          <Form.Item
            name="value"
            label="Value"
            rules={[{ required: true }]}
          >
            <TextArea rows={4} placeholder="Memory value..." />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit Modal */}
      <Modal
        title="Edit Memory Entry"
        open={editing !== null}
        onOk={handleEdit}
        onCancel={() => {
          setEditing(null);
        }}
        confirmLoading={updateMutation.isPending}
        okText="Save"
      >
        <Form layout="vertical">
          <Form.Item label="Key">
            <Input
              value={editKey}
              onChange={(e) => setEditKey(e.target.value)}
              placeholder="e.g., user_preference"
            />
          </Form.Item>
          <Form.Item label="Value">
            <TextArea
              rows={6}
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              placeholder="Memory value..."
            />
          </Form.Item>
        </Form>
      </Modal>
    </Drawer>
  );
}

export default MemoryManager;
