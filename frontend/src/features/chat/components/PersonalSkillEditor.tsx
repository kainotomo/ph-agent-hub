// =============================================================================
// PH Agent Hub — PersonalSkillEditor
// =============================================================================
// Ant Design Modal+Form; POST/PUT/DELETE /skills for personal skills only.
// =============================================================================

import { useEffect, useState } from "react";
import {
  Modal,
  Form,
  Input,
  Select,
  Button,
  List,
  Popconfirm,
  message,
  Empty,
  Space,
  Typography,
  Divider,
} from "antd";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../../../services/api";

const { TextArea } = Input;
const { Text } = Typography;

interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

interface SkillData {
  id: string;
  tenant_id: string;
  user_id: string | null;
  title: string;
  description: string;
  execution_type: string;
  maf_target_key: string;
  visibility: string;
  template_id: string | null;
  default_prompt_id: string | null;
  default_model_id: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  tool_ids: string[];
}

interface PersonalSkillEditorProps {
  open: boolean;
  onClose: () => void;
}

export function PersonalSkillEditor({
  open,
  onClose,
}: PersonalSkillEditorProps) {
  const [editingSkill, setEditingSkill] = useState<SkillData | null>(null);
  const [view, setView] = useState<"list" | "form">("list");
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const executionType = Form.useWatch("execution_type", form);

  const { data: skills, isLoading } = useQuery({
    queryKey: ["skills"],
    queryFn: () => api<PaginatedResponse<SkillData>>("/skills"),
    enabled: open,
  });

  const { data: templates } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<{ id: string; title: string }[]>("/templates"),
    enabled: open,
  });

  const personalSkills = (skills?.items || []).filter((s) => s.user_id !== null);

  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) =>
      api<SkillData>("/skills", { method: "POST", body: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["skills"] });
      message.success("Skill created");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: Record<string, unknown>;
    }) => api<SkillData>(`/skills/${id}`, { method: "PUT", body: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["skills"] });
      message.success("Skill updated");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      api<void>(`/skills/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["skills"] });
      message.success("Skill deleted");
    },
  });

  const handleSave = async () => {
    const values = await form.validateFields();
    if (editingSkill?.id) {
      await updateMutation.mutateAsync({
        id: editingSkill.id,
        data: values,
      });
    } else {
      await createMutation.mutateAsync(values);
    }
    setEditingSkill(null);
    setView("list");
    form.resetFields();
  };

  const handleEdit = (skill: SkillData) => {
    setEditingSkill(skill);
    form.setFieldsValue(skill);
    setView("form");
  };

  const handleNew = () => {
    setEditingSkill(null);
    form.resetFields();
    form.setFieldsValue({
      execution_type: "prompt_based",
      enabled: true,
      visibility: "personal",
    });
    setView("form");
  };

  const handleDelete = async (id: string) => {
    await deleteMutation.mutateAsync(id);
  };

  useEffect(() => {
    if (!open) {
      setView("list");
      setEditingSkill(null);
    }
  }, [open]);

  return (
    <Modal
      title="Personal Skills"
      open={open}
      onCancel={onClose}
      width={640}
      footer={null}
    >
      {view === "list" ? (
        <>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleNew}
            style={{ marginBottom: 16 }}
          >
            New Personal Skill
          </Button>
          <List
            loading={isLoading}
            dataSource={personalSkills}
            locale={{
              emptyText: (
                <Empty description="No personal skills yet. Create your first one!" />
              ),
            }}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Button
                    icon={<EditOutlined />}
                    size="small"
                    onClick={() => handleEdit(item)}
                  />,
                  <Popconfirm
                    title="Delete this skill?"
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
                  description={item.description}
                />
              </List.Item>
            )}
          />
        </>
      ) : (
        <>
          <Text strong>
            {editingSkill?.id ? "Edit Skill" : "New Skill"}
          </Text>
          <Divider />
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
            >
              <TextArea rows={2} />
            </Form.Item>
            <Form.Item
              name="execution_type"
              label="Execution Type"
              rules={[{ required: true }]}
            >
              <Select
                options={[
                  { label: "Prompt Based", value: "prompt_based" },
                  { label: "Workflow Based", value: "workflow_based" },
                ]}
              />
            </Form.Item>
            {executionType === "workflow_based" && (
              <Form.Item
                name="maf_target_key"
                label="MAF Target Key"
                rules={[{ required: true }]}
                extra="Must match a registered workflow module"
              >
                <Input placeholder="e.g., invoice_processing" />
              </Form.Item>
            )}
            {executionType !== "workflow_based" && (
              <Form.Item name="template_id" label="Template">
                <Select
                  allowClear
                  placeholder="Select a template (provides system prompt)"
                  options={(templates || []).map((t) => ({
                    label: t.title,
                    value: t.id,
                  }))}
                />
              </Form.Item>
            )}
            <Space>
              <Button onClick={() => setView("list")}>Cancel</Button>
              <Button
                type="primary"
                onClick={handleSave}
                loading={
                  createMutation.isPending || updateMutation.isPending
                }
              >
                Save
              </Button>
            </Space>
          </Form>
        </>
      )}
    </Modal>
  );
}

export default PersonalSkillEditor;
