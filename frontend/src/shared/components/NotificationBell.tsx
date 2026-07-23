// =============================================================================
// PH Agent Hub — NotificationBell Component
// =============================================================================
// Bell icon with unread count badge; dropdown shows recent notifications
// with "Mark all read" action (Issue #449).
// =============================================================================

import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  List,
  Popover,
  Typography,
  Empty,
  Spin,
  Space,
  Tag,
  Tooltip,
} from "antd";
import {
  BellOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  MinusCircleOutlined,
  CheckOutlined,
} from "@ant-design/icons";
import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  NotificationData,
  NotificationListResponse,
  UnreadCountResponse,
} from "../../features/chat/services/notifications";
import { useNavigate } from "react-router-dom";

const { Text, Paragraph } = Typography;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_TAG_COLORS: Record<string, string> = {
  TASK_COMPLETED: "success",
  TASK_FAILED: "error",
  TASK_CANCELLED: "warning",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  TASK_COMPLETED: <CheckCircleOutlined style={{ color: "#52c41a" }} />,
  TASK_FAILED: <CloseCircleOutlined style={{ color: "#ff4d4f" }} />,
  TASK_CANCELLED: <MinusCircleOutlined style={{ color: "#faad14" }} />,
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
  if (diffDay < 7) return `${diffDay}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Unread count — poll every 30 seconds
  const { data: unreadData } = useQuery<UnreadCountResponse>({
    queryKey: ["notification-unread-count"],
    queryFn: getUnreadCount,
    refetchInterval: 30_000,
  });

  // Recent notifications — fetch when popover opens
  const {
    data: notifData,
    isLoading,
  } = useQuery<NotificationListResponse>({
    queryKey: ["notifications", "recent"],
    queryFn: () => listNotifications(1, 20),
    enabled: open,
  });

  const unreadCount = unreadData?.count ?? 0;

  const handleMarkAllRead = async () => {
    await markAllNotificationsRead();
    queryClient.invalidateQueries({ queryKey: ["notification-unread-count"] });
    queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };

  const handleNotificationClick = async (notif: NotificationData) => {
    // Mark as read
    if (!notif.is_read) {
      await markNotificationRead(notif.id);
      queryClient.invalidateQueries({ queryKey: ["notification-unread-count"] });
    }
    setOpen(false);

    // Navigate to the relevant session
    if (notif.reference_id && notif.reference_type === "autopilot_run") {
      navigate("/background-tasks");
    }
  };

  const content = (
    <div style={{ width: 360, maxHeight: 420 }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 12px",
          borderBottom: "1px solid #f0f0f0",
        }}
      >
        <Text strong>Notifications</Text>
        {unreadCount > 0 && (
          <Button
            type="link"
            size="small"
            icon={<CheckOutlined />}
            onClick={handleMarkAllRead}
          >
            Mark all read
          </Button>
        )}
      </div>

      {/* List */}
      {isLoading ? (
        <div style={{ textAlign: "center", padding: 24 }}>
          <Spin size="small" />
        </div>
      ) : !notifData || notifData.items.length === 0 ? (
        <Empty
          description="No notifications"
          style={{ padding: 24, margin: 0 }}
        />
      ) : (
        <List<NotificationData>
          dataSource={notifData.items}
          renderItem={(item) => (
            <List.Item
              style={{
                cursor: "pointer",
                padding: "8px 12px",
                background: item.is_read ? "transparent" : "#f6ffed",
                transition: "background 0.2s",
              }}
              onClick={() => handleNotificationClick(item)}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLElement).style.background = "#f5f5f5";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.background = item.is_read
                  ? "transparent"
                  : "#f6ffed";
              }}
            >
              <List.Item.Meta
                avatar={
                  <span style={{ fontSize: 16, lineHeight: "32px" }}>
                    {STATUS_ICONS[item.type] || <BellOutlined />}
                  </span>
                }
                title={
                  <Space size={4}>
                    <Text strong style={{ fontSize: 13 }}>
                      {item.title}
                    </Text>
                    <Tag
                      color={STATUS_TAG_COLORS[item.type] || "default"}
                      style={{ fontSize: 10, lineHeight: "16px" }}
                    >
                      {item.type.replace("TASK_", "").toLowerCase()}
                    </Tag>
                  </Space>
                }
                description={
                  <div>
                    {item.body && (
                      <Paragraph
                        ellipsis={{ rows: 2 }}
                        style={{ margin: 0, fontSize: 12, color: "#666" }}
                      >
                        {item.body}
                      </Paragraph>
                    )}
                    <Text
                      type="secondary"
                      style={{ fontSize: 11 }}
                    >
                      {timeAgo(item.created_at)}
                    </Text>
                  </div>
                }
              />
            </List.Item>
          )}
          style={{ maxHeight: 340, overflow: "auto" }}
        />
      )}

      {/* Footer link */}
      <div
        style={{
          borderTop: "1px solid #f0f0f0",
          padding: "6px 12px",
          textAlign: "center",
        }}
      >
        <Button
          type="link"
          size="small"
          onClick={() => {
            setOpen(false);
            navigate("/background-tasks");
          }}
        >
          View all background tasks
        </Button>
      </div>
    </div>
  );

  return (
    <Popover
      content={content}
      trigger="click"
      open={open}
      onOpenChange={setOpen}
      placement="bottomRight"
      arrow={false}
    >
      <Tooltip title="Notifications">
        <Button
          type="text"
          icon={
            <Badge count={unreadCount} size="small" offset={[2, -2]}>
              <BellOutlined style={{ fontSize: 16 }} />
            </Badge>
          }
          style={{ display: "flex", alignItems: "center", justifyContent: "center" }}
        />
      </Tooltip>
    </Popover>
  );
}
