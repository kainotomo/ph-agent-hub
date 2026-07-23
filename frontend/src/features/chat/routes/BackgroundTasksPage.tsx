// =============================================================================
// PH Agent Hub — Background Tasks Page
// =============================================================================
// Lists the current user's background tasks with status, progress, and
// cancel actions (Issue #449).
// =============================================================================

import React, { useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Card,
  Empty,
  Grid,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  Progress,
} from "antd";
import {
  StopOutlined,
  ReloadOutlined,
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  MinusCircleOutlined,
  SyncOutlined,
} from "@ant-design/icons";
import {
  listBackgroundTasks,
  cancelBackgroundTask,
  BackgroundTaskData,
} from "../services/backgroundTasks";

const { Text, Title } = Typography;
const { useBreakpoint } = Grid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATE_CONFIG: Record<
  string,
  { color: string; icon: React.ReactNode; label: string }
> = {
  EXECUTING: {
    color: "processing",
    icon: <SyncOutlined spin />,
    label: "Running",
  },
  COMPLETED: {
    color: "success",
    icon: <CheckCircleOutlined />,
    label: "Completed",
  },
  FAILED: {
    color: "error",
    icon: <CloseCircleOutlined />,
    label: "Failed",
  },
  CANCELLED: {
    color: "warning",
    icon: <MinusCircleOutlined />,
    label: "Cancelled",
  },
  PAUSED: {
    color: "default",
    icon: <MinusCircleOutlined />,
    label: "Paused",
  },
};

function timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function truncate(str: string, max: number): string {
  if (str.length <= max) return str;
  return str.slice(0, max) + "...";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function BackgroundTasksPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const screens = useBreakpoint();
  const [page, setPage] = useState(1);
  const [stateFilter, setStateFilter] = useState<string | undefined>(undefined);
  const cancellingRef = useRef<Set<string>>(new Set());

  const { data, isLoading, isRefetching, refetch } = useQuery({
    queryKey: ["background-tasks", page, stateFilter],
    queryFn: () => listBackgroundTasks(page, 20, stateFilter),
    refetchInterval: (query) => {
      // Auto-refresh every 10s if any task is still running
      const items = query.state.data?.items ?? [];
      return items.some((t) => t.state === "EXECUTING") ? 10_000 : false;
    },
  });

  const handleCancel = async (taskId: string) => {
    if (cancellingRef.current.has(taskId)) return;
    cancellingRef.current.add(taskId);
    try {
      await cancelBackgroundTask(taskId);
      message.success("Task cancelled");
      queryClient.invalidateQueries({ queryKey: ["background-tasks"] });
    } catch {
      message.error("Failed to cancel task");
    } finally {
      cancellingRef.current.delete(taskId);
    }
  };

  const columns = [
    {
      title: "Goal",
      dataIndex: "goal" as const,
      key: "goal",
      render: (goal: string, _record: BackgroundTaskData) => (
        <Tooltip title={goal}>
          <Text strong>{truncate(goal, 60)}</Text>
        </Tooltip>
      ),
    },
    {
      title: "Status",
      dataIndex: "state" as const,
      key: "state",
      width: 130,
      render: (state: string) => {
        const cfg = STATE_CONFIG[state] || {
          color: "default",
          icon: null,
          label: state,
        };
        return (
          <Tag icon={cfg.icon} color={cfg.color}>
            {cfg.label}
          </Tag>
        );
      },
    },
    {
      title: "Progress",
      key: "progress",
      width: 200,
      render: (_: unknown, record: BackgroundTaskData) => {
        if (record.state === "EXECUTING") {
          return (
            <div style={{ minWidth: 160 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Turn {record.current_turn}/{record.max_turns}
              </Text>
              <Progress
                percent={Math.round(
                  (record.current_turn / record.max_turns) * 100,
                )}
                size="small"
                showInfo={false}
              />
              {record.progress_message && (
                <Text
                  type="secondary"
                  style={{ fontSize: 11, display: "block" }}
                  ellipsis
                >
                  {record.progress_message}
                </Text>
              )}
            </div>
          );
        }
        if (record.state === "COMPLETED" && record.result_summary) {
          return (
            <Tooltip title={record.result_summary}>
              <Text type="secondary" style={{ fontSize: 12 }} ellipsis>
                {truncate(record.result_summary, 80)}
              </Text>
            </Tooltip>
          );
        }
        if (
          record.state === "FAILED" &&
          record.error_message
        ) {
          return (
            <Tooltip title={record.error_message}>
              <Text type="danger" style={{ fontSize: 12 }} ellipsis>
                {truncate(record.error_message, 60)}
              </Text>
            </Tooltip>
          );
        }
        return (
          <Text type="secondary" style={{ fontSize: 12 }}>
            —
          </Text>
        );
      },
    },
    {
      title: "Started",
      dataIndex: "created_at" as const,
      key: "created_at",
      width: 100,
      render: (date: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {timeAgo(date)}
        </Text>
      ),
    },
    {
      title: "Actions",
      key: "actions",
      width: 100,
      render: (_: unknown, record: BackgroundTaskData) => (
        <Space size={4}>
          {record.state === "EXECUTING" && (
            <Tooltip title="Cancel task">
              <Button
                size="small"
                danger
                icon={<StopOutlined />}
                loading={cancellingRef.current.has(record.id)}
                onClick={() => handleCancel(record.id)}
              />
            </Tooltip>
          )}
          <Button
            size="small"
            type="link"
            onClick={() => navigate(`/chat/${record.session_id}`)}
          >
            View
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ height: "100dvh", overflow: "hidden", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "12px 24px",
          borderBottom: "1px solid #f0f0f0",
          background: "#fff",
          gap: 12,
        }}
      >
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate("/chat")}
          type="text"
        />
        <Title level={4} style={{ margin: 0, flex: 1 }}>
          Background Tasks
        </Title>
        <Space size={8}>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => refetch()}
            loading={isRefetching}
          >
            Refresh
          </Button>
        </Space>
      </div>

      {/* Filter bar */}
      <div
        style={{
          padding: "8px 24px",
          borderBottom: "1px solid #f0f0f0",
          background: "#fafafa",
        }}
      >
        <Space size={8} wrap>
          <Tag.CheckableTag
            checked={stateFilter === undefined}
            onChange={() => setStateFilter(undefined)}
          >
            All
          </Tag.CheckableTag>
          <Tag.CheckableTag
            checked={stateFilter === "EXECUTING"}
            onChange={() => setStateFilter("EXECUTING")}
          >
            Running
          </Tag.CheckableTag>
          <Tag.CheckableTag
            checked={stateFilter === "COMPLETED"}
            onChange={() => setStateFilter("COMPLETED")}
          >
            Completed
          </Tag.CheckableTag>
          <Tag.CheckableTag
            checked={stateFilter === "FAILED"}
            onChange={() => setStateFilter("FAILED")}
          >
            Failed
          </Tag.CheckableTag>
          <Tag.CheckableTag
            checked={stateFilter === "CANCELLED"}
            onChange={() => setStateFilter("CANCELLED")}
          >
            Cancelled
          </Tag.CheckableTag>
        </Space>
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        <Card
          bordered={false}
          style={{ boxShadow: "none" }}
        >
          <Table
            dataSource={data?.items ?? []}
            columns={columns}
            rowKey="id"
            loading={isLoading}
            pagination={{
              current: page,
              pageSize: 20,
              total: data?.total ?? 0,
              onChange: setPage,
              showSizeChanger: false,
            }}
            locale={{
              emptyText: (
                <Empty
                  description={
                    stateFilter
                      ? `No ${stateFilter.toLowerCase()} tasks`
                      : "No background tasks yet"
                  }
                >
                  <Button
                    type="primary"
                    onClick={() => navigate("/chat")}
                  >
                    Start a task
                  </Button>
                </Empty>
              ),
            }}
            scroll={{ x: screens.xs ? 600 : undefined }}
            size="middle"
          />
        </Card>
      </div>
    </div>
  );
}
