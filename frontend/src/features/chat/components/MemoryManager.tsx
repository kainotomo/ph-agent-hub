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
  Pagination,
  Descriptions,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  SearchOutlined,
  DownloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listMemory,
  createMemory,
  deleteMemory,
  updateMemory,
  exportMemory,
  clearMemory,
  mergeMemory,
  type MemoryEntry,
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
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const [showDuplicates, setShowDuplicates] = useState(false);

  const { data: envelope, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["memory", sessionId, page],
    queryFn: () => listMemory({ sessionId, page, pageSize }),
    enabled: open || !!sessionId,
  });

  const entries = envelope?.items ?? [];

  // ---------------------------------------------------------------------------
  // Near-duplicate key detection
  // ---------------------------------------------------------------------------

  interface DuplicateGroup {
    normKey: string;
    entries: MemoryEntry[];
  }

  const duplicateGroups: DuplicateGroup[] = useMemo(() => {
    if (!entries || entries.length < 2) return [];
    const groups = new Map<string, MemoryEntry[]>();
    for (const entry of entries) {
      const norm = entry.key
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "");
      const existing = groups.get(norm) || [];
      existing.push(entry);
      groups.set(norm, existing);
    }
    const result: DuplicateGroup[] = [];
    for (const [normKey, group] of groups) {
      if (group.length < 2) continue;
      const rawKeys = new Set(group.map((e) => e.key));
      if (rawKeys.size > 1) {
        result.push({ normKey, entries: group });
      }
    }
    return result;
  }, [entries]);

  const hasDuplicates = duplicateGroups.length > 0;

  const createMutation = useMutation({
    mutationFn: (data: { key: string; value: string }) =>
      createMemory(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId, page] });
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
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId, page] });
      message.success("Memory entry updated");
      setEditing(null);
    },
    onError: (err) => message.error(`Failed to update memory: ${(err as Error).message}`),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteMemory(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId, page] });
      message.success("Memory entry deleted");
    },
    onError: (err) => message.error(`Failed to delete memory: ${(err as Error).message}`),
  });

  const exportMutation = useMutation({
    mutationFn: () => exportMemory(sessionId),
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "memory-export.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      message.success(`Exported ${data.count} entries`);
    },
    onError: (err) => message.error(`Failed to export memory: ${(err as Error).message}`),
  });

  const clearMutation = useMutation({
    mutationFn: () => clearMemory(),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId, page] });
      setPage(1);
      message.success(`Deleted ${data.deleted} entries`);
    },
    onError: (err) => message.error(`Failed to clear memory: ${(err as Error).message}`),
  });

  const mergeMutation = useMutation({
    mutationFn: (data: { target_id: string; source_ids: string[] }) =>
      mergeMemory(data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ["memory", sessionId, page] });
      message.success(`Merged ${variables.source_ids.length + 1} entries`);
      setShowDuplicates(false);
    },
    onError: (err) => message.error(`Failed to merge memory: ${(err as Error).message}`),
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

  // NOTE: pagination is done by the server (see the listMemory call above).
  // The search box only filters the rows of the page currently loaded, so the
  // already-paginated list must NOT be sliced again here.

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
          <Button
            icon={<DownloadOutlined />}
            onClick={() => exportMutation.mutate()}
          >
            Export
          </Button>
          <Popconfirm
            title="Delete all memory entries?"
            onConfirm={() => clearMutation.mutate()}
          >
            <Button danger>Clear all</Button>
          </Popconfirm>
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

      {/* Duplicate warning */}
      {hasDuplicates && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined />}
          message={`${duplicateGroups.length} possible duplicate memory key${duplicateGroups.length > 1 ? "s" : ""}`}
          action={
            <Button
              size="small"
              onClick={() => setShowDuplicates(true)}
            >
              Review duplicates
            </Button>
          }
          style={{ marginBottom: 16 }}
        />
      )}

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
                    <Tag color="purple">
                      {item.session_id === null ? "Global" : "This session"}
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

      {/* Pagination */}
      {envelope && envelope.total_pages > 1 && (
        <Pagination
          current={page}
          total={envelope.total}
          pageSize={pageSize}
          onChange={(p) => setPage(p)}
          showTotal={(total) => `${total} entries`}
          style={{ marginTop: 16, textAlign: "center" }}
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
          <Text type="secondary">Saved for all your conversations.</Text>
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

      {/* Duplicate Review Modal */}
      <Modal
        title="Possible duplicate memory keys"
        open={showDuplicates}
        onCancel={() => setShowDuplicates(false)}
        footer={null}
      >
        <p style={{ color: "#666", marginBottom: 16 }}>
          The oldest entry is kept as the target and values from the other entries are appended to it.
        </p>
        {duplicateGroups.map((group, idx) => {
          // Target is the entry with the earliest created_at; fall back to first entry
          const sorted = [...group.entries].sort(
            (a, b) =>
              (a.created_at ?? "").localeCompare(b.created_at ?? ""),
          );
          const target = sorted[0];
          const sources = sorted.slice(1);
          const handleMerge = () => {
            mergeMutation.mutate({
              target_id: target.id,
              source_ids: sources.map((s) => s.id),
            });
          };
          return (
            <div key={idx} style={{ marginBottom: 16, paddingBottom: 16, borderBottom: "1px solid #f0f0f0" }}>
              <div style={{ marginBottom: 8 }}>
                <Space direction="vertical" size={2} style={{ width: "100%" }}>
                  {group.entries.map((e) => (
                    <Tag key={e.id} color={e.id === target.id ? "green" : "orange"}>
                      {e.key}
                    </Tag>
                  ))}
                </Space>
              </div>
              <Descriptions size="small" column={1} style={{ marginBottom: 8 }}>
                <Descriptions.Item label="Target">{target.value}</Descriptions.Item>
                {sources.map((s) => (
                  <Descriptions.Item key={s.id} label={`Source (merged)`}>{s.value}</Descriptions.Item>
                ))}
              </Descriptions>
              <Button
                type="primary"
                loading={mergeMutation.isPending}
                onClick={handleMerge}
              >
                Merge
              </Button>
            </div>
          );
        })}
      </Modal>
    </Drawer>
  );
}

export default MemoryManager;
