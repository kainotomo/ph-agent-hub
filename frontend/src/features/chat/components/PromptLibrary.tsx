// =============================================================================
// PH Agent Hub — PromptLibrary
// =============================================================================
// Ant Design Drawer+List; GET/POST/PUT/DELETE /prompts; user-owned.
// "Use" opens a variable-resolution modal — the resolved text is inserted
// directly into the chat input (no backend involvement).
// =============================================================================

import { useState, useMemo } from "react";
import {
  Drawer,
  List,
  Button,
  Typography,
  Modal,
  Form,
  Input,
  Popconfirm,
  message,
  Empty,
  Tag,
} from "antd";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../../../services/api";

const { Paragraph, Text } = Typography;
const { TextArea } = Input;

interface PromptData {
  id: string;
  tenant_id: string;
  user_id: string;
  template_id: string | null;
  title: string;
  description: string;
  content: string;
  created_at: string;
  updated_at: string;
}

interface PromptLibraryProps {
  /** Called when the user resolves a prompt and wants to insert it into the chat input. */
  onUse?: (resolvedText: string) => void;
}

// Extract {{variable_name}} patterns from prompt content
function extractVariables(content: string): string[] {
  const re = /\{\{(\w+)\}\}/g;
  const names = new Set<string>();
  let match: RegExpExecArray | null;
  while ((match = re.exec(content)) !== null) {
    names.add(match[1]);
  }
  return Array.from(names);
}

// Replace {{variable}} placeholders with user-provided values.
// Unfilled variables are replaced with an empty string.
function resolveTemplate(content: string, values: Record<string, string>): string {
  return content.replace(/\{\{(\w+)\}\}/g, (_, name) => values[name] ?? "");
}

export function PromptLibrary({
  onUse,
}: PromptLibraryProps) {
  const [open, setOpen] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState<PromptData | null>(null);
  const [form] = Form.useForm();
  const queryClient = useQueryClient();

  // ---- Use / variable modal state ----
  const [usingPrompt, setUsingPrompt] = useState<PromptData | null>(null);
  const [variableForm] = Form.useForm();
  const variables = useMemo(
    () => (usingPrompt ? extractVariables(usingPrompt.content) : []),
    [usingPrompt],
  );

  const { data: prompts, isLoading } = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<PromptData[]>("/prompts"),
    enabled: open,
  });

  const createMutation = useMutation({
    mutationFn: (data: {
      title: string;
      description: string;
      content: string;
    }) => api<PromptData>("/prompts", { method: "POST", body: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      message.success("Prompt created");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Partial<PromptData>;
    }) => api<PromptData>(`/prompts/${id}`, { method: "PUT", body: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      message.success("Prompt updated");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      api<void>(`/prompts/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      message.success("Prompt deleted");
    },
  });

  const handleSave = async () => {
    const values = await form.validateFields();
    if (editingPrompt?.id) {
      await updateMutation.mutateAsync({ id: editingPrompt.id, data: values });
    } else {
      await createMutation.mutateAsync(values);
    }
    setEditingPrompt(null);
    form.resetFields();
  };

  const handleEdit = (prompt: PromptData) => {
    setEditingPrompt(prompt);
    form.setFieldsValue(prompt);
  };

  const handleDelete = async (id: string) => {
    await deleteMutation.mutateAsync(id);
  };

  const handleNew = () => {
    setEditingPrompt({} as PromptData);
    form.resetFields();
  };

  const handleUse = (prompt: PromptData) => {
    setUsingPrompt(prompt);
    variableForm.resetFields();
  };

  const handleUseConfirm = async () => {
    const values = await variableForm.validateFields();
    const resolved = resolveTemplate(usingPrompt!.content, values);
    onUse?.(resolved);
    setUsingPrompt(null);
  };

  return (
    <>
      <Button
        size="small"
        onClick={() => setOpen(true)}
      >
        Prompts
      </Button>

      <Drawer
        title="Prompt Library"
        open={open}
        onClose={() => {
          setOpen(false);
          setEditingPrompt(null);
        }}
        width={480}
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleNew}
          >
            New Prompt
          </Button>
        }
      >
        <List
          loading={isLoading}
          dataSource={prompts || []}
          locale={{ emptyText: <Empty description="No prompts yet" /> }}
          renderItem={(item) => (
            <List.Item
              actions={[
                onUse && (
                  <Button
                    type="primary"
                    size="small"
                    onClick={() => handleUse(item)}
                  >
                    Use
                  </Button>
                ),
                <Button
                  icon={<EditOutlined />}
                  size="small"
                  onClick={() => handleEdit(item)}
                />,
                <Popconfirm
                  title="Delete this prompt?"
                  onConfirm={() => handleDelete(item.id)}
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
                title={item.title}
                description={
                  <Paragraph
                    ellipsis={{ rows: 2 }}
                    style={{ margin: 0 }}
                  >
                    {item.content}
                  </Paragraph>
                }
              />
            </List.Item>
          )}
        />

        {/* Edit/Create Modal */}
        <Modal
          title={editingPrompt?.id ? "Edit Prompt" : "New Prompt"}
          open={!!editingPrompt}
          onOk={handleSave}
          onCancel={() => setEditingPrompt(null)}
          confirmLoading={
            createMutation.isPending || updateMutation.isPending
          }
          width={640}
        >
          <Form form={form} layout="vertical">
            <Form.Item
              name="title"
              label="Title"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="description"
              label="Description"
              rules={[{ required: true }]}
            >
              <TextArea rows={2} />
            </Form.Item>
            <Form.Item
              name="content"
              label="Content"
              rules={[{ required: true }]}
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Use <Tag style={{ fontSize: 11 }}>{"{{variable_name}}"}</Tag>{" "}
                  for placeholders the user fills in when applying the prompt.
                </Text>
              }
            >
              <TextArea rows={8} />
            </Form.Item>
          </Form>
        </Modal>
      </Drawer>

      {/* Use / Variable Resolution Modal */}
      <Modal
        title={`Use: ${usingPrompt?.title ?? ""}`}
        open={!!usingPrompt}
        onOk={handleUseConfirm}
        onCancel={() => setUsingPrompt(null)}
        okText="Insert"
        width={640}
      >
        {usingPrompt && (
          <>
            <Paragraph
              style={{
                background: "#f5f5f5",
                padding: 12,
                borderRadius: 6,
                whiteSpace: "pre-wrap",
                fontFamily: "monospace",
                fontSize: 13,
                maxHeight: 200,
                overflow: "auto",
              }}
            >
              {usingPrompt.content}
            </Paragraph>

            {variables.length > 0 ? (
              <Form form={variableForm} layout="vertical" style={{ marginTop: 16 }}>
                {variables.map((name) => (
                  <Form.Item
                    key={name}
                    name={name}
                    label={name}
                  >
                    <Input placeholder={`Value for ${name} (optional)`} />
                  </Form.Item>
                ))}
              </Form>
            ) : (
              <Text type="secondary">
                This prompt has no variables. Click Insert to add it to the message input.
              </Text>
            )}
          </>
        )}
      </Modal>
    </>
  );
}

export default PromptLibrary;
