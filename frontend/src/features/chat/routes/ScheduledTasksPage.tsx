// =============================================================================
// PH Agent Hub — Scheduled Tasks Page
// =============================================================================
// Page for viewing, editing, pausing, resuming, and deleting scheduled tasks
// (Issue #297 — Scheduled & Recurring Agent Tasks).
// =============================================================================

import { useCallback, useMemo, useState } from "react";
import {
  Button,
  Card,
  Form,
  Grid,
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
  ArrowLeftOutlined,
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
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
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

  // --- Mobile card render -------------------------------------------------
  const renderMobileCard = useCallback(
    (task: ScheduledTaskData) => {
      const stateCfg = STATE_CONFIG[task.state];
      const statusCfg = STATUS_CONFIG[task.last_run_status ?? ""];
      return (
        <Card
          key={task.id}
          size="small"
          style={{ marginBottom: 12 }}
          title={
            <Tooltip title={task.goal}>
              <Text ellipsis style={{ maxWidth: 220, display: "inline-block" }}>
                {task.goal}
              </Text>
            </Tooltip>
          }
          extra={
            stateCfg ? (
              <Tag color={stateCfg.color} icon={stateCfg.icon}>
                {task.state}
              </Tag>
            ) : (
              <Tag>{task.state}</Tag>
            )
          }
          actions={[
            task.state === "ACTIVE" ? (
              <Tooltip title="Pause" key="pause">
                <PauseCircleOutlined onClick={() => handlePause(task.id)} />
              </Tooltip>
            ) : task.state === "PAUSED" ? (
              <Tooltip title="Resume" key="resume">
                <PlayCircleOutlined onClick={() => handleResume(task.id)} />
              </Tooltip>
            ) : (
              <span key="state" />
            ),
            <Tooltip title="Edit" key="edit">
              <EditOutlined onClick={() => handleEdit(task)} />
            </Tooltip>,
            <Tooltip title="Delete" key="delete">
              <DeleteOutlined onClick={() => handleDelete(task.id)} />
            </Tooltip>,
          ]}
        >
          <div style={{ lineHeight: 1.8 }}>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Schedule:{" "}
              </Text>
              <Tooltip
                title={`Cron: ${task.cron_expression} • TZ: ${task.timezone}`}
              >
                <Text style={{ fontSize: 13 }}>{task.schedule_description}</Text>
              </Tooltip>
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Next Run:{" "}
              </Text>
              <Text style={{ fontSize: 13 }}>{formatNextRun(task.next_run_at)}</Text>
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Last Run:{" "}
              </Text>
              {task.last_run_at ? (
                <Space size={4}>
                  <Tag
                    color={statusCfg?.color ?? "default"}
                    style={{ fontSize: 11, lineHeight: "18px" }}
                  >
                    {task.last_run_status ?? "UNKNOWN"}
                  </Tag>
                  <Text style={{ fontSize: 13 }}>
                    {formatLastRun(task.last_run_at)}
                  </Text>
                  {task.last_run_session_id && (
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0, height: 20, fontSize: 12 }}
                      onClick={() =>
                        navigate(`/chat/${task.last_run_session_id}`)
                      }
                    >
                      View
                    </Button>
                  )}
                </Space>
              ) : (
                <Text style={{ color: "#999", fontSize: 13 }}>Never</Text>
              )}
            </div>
          </div>
        </Card>
      );
    },
    [handlePause, handleResume, handleEdit, handleDelete, navigate],
  );

  // --- Empty state --------------------------------------------------------
  const emptyState = (
    <div style={{ padding: 40, textAlign: "center" }}>
      <ClockCircleOutlined style={{ fontSize: 48, color: "#d9d9d9" }} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
        No scheduled tasks yet. Create one in a chat by asking the agent
        to schedule a recurring task.
      </Typography.Paragraph>
      <Button type="primary" onClick={() => navigate("/chat")}>
        Go to Chat
      </Button>
    </div>
  );

  // --- Mobile pagination helper -------------------------------------------
  const totalPages = data ? Math.ceil(data.total / 50) : 0;

  return (
    <div
      style={
        isMobile
          ? {
              height: "100dvh",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }
          : { padding: 24 }
      }
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          ...(isMobile
            ? {
                padding: "12px 16px",
                borderBottom: "1px solid #f0f0f0",
                background: "#fff",
              }
            : { marginBottom: 16 }),
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            minWidth: 0,
            flex: 1,
          }}
        >
          {isMobile && (
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate("/chat")}
              type="text"
            />
          )}
          <div style={{ overflow: "hidden" }}>
            <Typography.Title
              level={isMobile ? 5 : 4}
              style={{ margin: 0 }}
            >
              <ClockCircleOutlined style={{ marginRight: 8 }} />
              Scheduled Tasks
            </Typography.Title>
            {!isMobile && (
              <Typography.Text type="secondary">
                Agent tasks that run automatically on a recurring schedule
              </Typography.Text>
            )}
          </div>
        </div>
        <Space size={isMobile ? 4 : 8}>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refetch()}
            size={isMobile ? "small" : "middle"}
          >
            {isMobile ? undefined : "Refresh"}
          </Button>
          {!isMobile && (
            <Button type="primary" onClick={() => navigate("/chat")}>
              New Schedule in Chat
            </Button>
          )}
        </Space>
      </div>

      {/* Filter tabs */}
      <div
        style={{
          ...(isMobile
            ? {
                padding: "8px 16px",
                borderBottom: "1px solid #f0f0f0",
                background: "#fafafa",
              }
            : { marginBottom: 12 }),
        }}
      >
        <Space size={isMobile ? 4 : 8} wrap>
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

      {/* Content: Table (desktop) or Card list (mobile) */}
      {isMobile ? (
        <div style={{ flex: 1, overflow: "auto", padding: 12 }}>
          {data?.items?.length ? (
            data.items.map(renderMobileCard)
          ) : (
            emptyState
          )}
          {data && data.total > 10 && totalPages > 1 && (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                gap: 12,
                padding: "16px 0",
              }}
            >
              <Button
                size="small"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                Page {page} of {totalPages}
              </Typography.Text>
              <Button
                size="small"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      ) : (
        <Table
          dataSource={data?.items ?? []}
          columns={columns}
          rowKey="id"
          loading={isLoading || isRefetching}
          size="middle"
          scroll={{ x: 700 }}
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
          locale={{ emptyText: emptyState }}
        />
      )}

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
