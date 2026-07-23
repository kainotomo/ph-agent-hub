// =============================================================================
// PH Agent Hub — Scheduled Tasks Page
// =============================================================================
// Page for viewing, editing, pausing, resuming, and deleting scheduled tasks
// (Issue #297 — Scheduled & Recurring Agent Tasks).
// =============================================================================

import { useCallback, useMemo, useState } from "react";
import {
  Button,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ScheduledTaskData,
  listScheduledTasks,
  pauseScheduledTask,
  resumeScheduledTask,
  deleteScheduledTask,
  updateScheduledTask,
} from "../services/scheduledTasks";

const { Text } = Typography;
const { TextArea } = Input;

// ---------------------------------------------------------------------------
// State config
// ---------------------------------------------------------------------------

const STATE_CONFIG: Record<string, { color: string; icon: React.ReactNode }> = {
  ACTIVE: { color: "green", icon: <CheckCircleOutlined /> },
  PAUSED: { color: "orange", icon: <PauseCircleOutlined /> },
};

const STATUS_CONFIG: Record<string, { color: string }> = {
  SUCCESS: { color: "green" },
  FAILED: { color: "red" },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatNextRun(nextRunAt: string | null): string {
  if (!nextRunAt) return "—";
  const date = new Date(nextRunAt);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  if (diffMs < 0) return "Overdue";
  const diffMins = Math.round(diffMs / 60000);
  const diffHours = Math.round(diffMs / 3600000);
  const diffDays = Math.round(diffMs / 86400000);
  if (diffMins < 60) return `in ${diffMins}m`;
  if (diffHours < 24) return `in ${diffHours}h`;
  if (diffDays < 7) return `in ${diffDays}d`;
  return date.toLocaleDateString();
}

function formatLastRun(lastRunAt: string | null): string {
  if (!lastRunAt) return "—";
  const date = new Date(lastRunAt);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.round(diffMs / 60000);
  const diffHours = Math.round(diffMs / 3600000);
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return date.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ScheduledTasksPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [stateFilter, setStateFilter] = useState<string | undefined>();
  const [editingTask, setEditingTask] = useState<ScheduledTaskData | null>(null);
  const [editForm] = Form.useForm();

  // --- Data fetching -------------------------------------------------------
  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["scheduled-tasks", page, stateFilter],
    queryFn: () => listScheduledTasks(page, 50, stateFilter),
  });

  // --- Mutations -----------------------------------------------------------
  const pauseMutation = useMutation({
    mutationFn: (taskId: string) => pauseScheduledTask(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      message.success("Schedule paused");
    },
    onError: () => {
      message.error("Failed to pause schedule");
    },
  });

  const resumeMutation = useMutation({
    mutationFn: (taskId: string) => resumeScheduledTask(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      message.success("Schedule resumed");
    },
    onError: () => {
      message.error("Failed to resume schedule");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => deleteScheduledTask(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      message.success("Schedule deleted");
    },
    onError: () => {
      message.error("Failed to delete schedule");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ taskId, data }: { taskId: string; data: any }) =>
      updateScheduledTask(taskId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      setEditingTask(null);
      message.success("Schedule updated");
    },
    onError: () => {
      message.error("Failed to update schedule");
    },
  });

  // --- Handlers ------------------------------------------------------------
  const handlePause = useCallback(
    (taskId: string) => pauseMutation.mutate(taskId),
    [pauseMutation],
  );
  const handleResume = useCallback(
    (taskId: string) => resumeMutation.mutate(taskId),
    [resumeMutation],
  );
  const handleDelete = useCallback((taskId: string) => {
    Modal.confirm({
      title: "Delete scheduled task?",
      content: "This action cannot be undone. The schedule will be removed and will no longer execute.",
      okText: "Delete",
      okType: "danger",
      onOk: () => deleteMutation.mutate(taskId),
    });
  }, [deleteMutation]);

  const handleEdit = useCallback((task: ScheduledTaskData) => {
    setEditingTask(task);
    editForm.setFieldsValue({
      goal: task.goal,
      schedule_description: task.schedule_description,
      cron_expression: task.cron_expression,
      timezone: task.timezone,
    });
  }, [editForm]);

  const handleEditSave = useCallback(async () => {
    if (!editingTask) return;
    try {
      const values = await editForm.validateFields();
      updateMutation.mutate({ taskId: editingTask.id, data: values });
    } catch {
      // validation failed
    }
  }, [editingTask, editForm, updateMutation]);

  // --- Columns -------------------------------------------------------------
  const columns = useMemo(
    () => [
      {
        title: "Goal",
        dataIndex: "goal",
        key: "goal",
        width: 250,
        ellipsis: true,
        render: (goal: string) => (
          <Tooltip title={goal}>
            <Text ellipsis style={{ maxWidth: 240 }}>
              {goal}
            </Text>
          </Tooltip>
        ),
      },
      {
        title: "Schedule",
        dataIndex: "schedule_description",
        key: "schedule",
        width: 180,
        render: (desc: string, record: ScheduledTaskData) => (
          <Tooltip title={`Cron: ${record.cron_expression} • TZ: ${record.timezone}`}>
            <span>{desc}</span>
          </Tooltip>
        ),
      },
      {
        title: "Next Run",
        dataIndex: "next_run_at",
        key: "nextRun",
        width: 120,
        render: (nextRunAt: string | null) => (
          <span>{formatNextRun(nextRunAt)}</span>
        ),
      },
      {
        title: "Last Run",
        key: "lastRun",
        width: 180,
        render: (_: any, record: ScheduledTaskData) => {
          if (!record.last_run_at) return <span style={{ color: "#999" }}>Never</span>;
          const statusConfig = STATUS_CONFIG[record.last_run_status ?? ""];
          return (
            <Space size={4}>
              <Tag color={statusConfig?.color ?? "default"}>
                {record.last_run_status ?? "UNKNOWN"}
              </Tag>
              <Text type="secondary">{formatLastRun(record.last_run_at)}</Text>
              {record.last_run_session_id && (
                <Button
                  type="link"
                  size="small"
                  onClick={() => navigate(`/chat/${record.last_run_session_id}`)}
                >
                  View
                </Button>
              )}
            </Space>
          );
        },
      },
      {
        title: "State",
        dataIndex: "state",
        key: "state",
        width: 100,
        render: (state: string) => {
          const config = STATE_CONFIG[state];
          return config ? (
            <Tag color={config.color} icon={config.icon}>
              {state}
            </Tag>
          ) : (
            <Tag>{state}</Tag>
          );
        },
      },
      {
        title: "Actions",
        key: "actions",
        width: 140,
        render: (_: any, record: ScheduledTaskData) => (
          <Space size={4}>
            {record.state === "ACTIVE" ? (
              <Tooltip title="Pause">
                <Button
                  type="text"
                  size="small"
                  icon={<PauseCircleOutlined />}
                  onClick={() => handlePause(record.id)}
                />
              </Tooltip>
            ) : record.state === "PAUSED" ? (
              <Tooltip title="Resume">
                <Button
                  type="text"
                  size="small"
                  icon={<PlayCircleOutlined />}
                  onClick={() => handleResume(record.id)}
                />
              </Tooltip>
            ) : null}
            <Tooltip title="Edit">
              <Button
                type="text"
                size="small"
                icon={<EditOutlined />}
                onClick={() => handleEdit(record)}
              />
            </Tooltip>
            <Tooltip title="Delete">
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(record.id)}
              />
            </Tooltip>
          </Space>
        ),
      },
    ],
    [handlePause, handleResume, handleDelete, handleEdit, navigate],
  );

  // --- Filter tabs ---------------------------------------------------------
  const filterTabs = useMemo(
    () => [
      { key: undefined, label: "All" },
      { key: "ACTIVE", label: "Active" },
      { key: "PAUSED", label: "Paused" },
    ],
    [],
  );

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>
            <ClockCircleOutlined style={{ marginRight: 8 }} />
            Scheduled Tasks
          </Typography.Title>
          <Typography.Text type="secondary">
            Agent tasks that run automatically on a recurring schedule
          </Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            Refresh
          </Button>
          <Button type="primary" onClick={() => navigate("/chat")}>
            New Schedule in Chat
          </Button>
        </Space>
      </div>

      {/* Filter tabs */}
      <div style={{ marginBottom: 12 }}>
        <Space>
          {filterTabs.map((tab) => (
            <Button
              key={tab.key ?? "all"}
              type={stateFilter === tab.key ? "primary" : "default"}
              size="small"
              onClick={() => {
                setStateFilter(tab.key);
                setPage(1);
              }}
            >
              {tab.label}
            </Button>
          ))}
        </Space>
      </div>

      {/* Table */}
      <Table
        dataSource={data?.items ?? []}
        columns={columns}
        rowKey="id"
        loading={isLoading || isRefetching}
        pagination={
          data && data.total > 50
            ? {
                current: page,
                pageSize: 50,
                total: data.total,
                onChange: setPage,
                showSizeChanger: false,
              }
            : false
        }
        locale={{
          emptyText: (
            <div style={{ padding: 40, textAlign: "center" }}>
              <ClockCircleOutlined style={{ fontSize: 48, color: "#d9d9d9" }} />
              <Typography.Paragraph
                type="secondary"
                style={{ marginTop: 12 }}
              >
                No scheduled tasks yet. Create one in a chat by asking the agent
                to schedule a recurring task.
              </Typography.Paragraph>
              <Button type="primary" onClick={() => navigate("/chat")}>
                Go to Chat
              </Button>
            </div>
          ),
        }}
      />

      {/* Edit Modal */}
      <Modal
        title="Edit Scheduled Task"
        open={!!editingTask}
        onOk={handleEditSave}
        onCancel={() => setEditingTask(null)}
        confirmLoading={updateMutation.isPending}
        okText="Save"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="goal"
            label="Goal"
            rules={[{ required: true, message: "Please enter the agent goal" }]}
          >
            <TextArea rows={3} />
          </Form.Item>
          <Form.Item
            name="schedule_description"
            label="Schedule Description"
            rules={[{ required: true, message: "Please enter a description" }]}
          >
            <Input placeholder="e.g. Every Friday at 8pm" />
          </Form.Item>
          <Form.Item
            name="cron_expression"
            label="Cron Expression"
            rules={[{ required: true, message: "Please enter a cron expression" }]}
            extra="Use https://crontab.guru to build your expression."
          >
            <Input placeholder="e.g. 0 20 * * 5" />
          </Form.Item>
          <Form.Item
            name="timezone"
            label="Timezone"
            rules={[{ required: true, message: "Please enter a timezone" }]}
          >
            <Input placeholder="UTC, Europe/London, America/New_York" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
