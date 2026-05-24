// =============================================================================
// PH Agent Hub — TemporaryChatBadge
// =============================================================================
// Clickable badge shown when session.is_temporary=true.
// Clicking triggers a Popconfirm to convert the session to permanent.
// =============================================================================

import { Tag, Popconfirm, message } from "antd";
import { ClockCircleOutlined, LoadingOutlined } from "@ant-design/icons";

interface TemporaryChatBadgeProps {
  isTemporary: boolean;
  onFinalize?: () => Promise<void>;
  loading?: boolean;
}

export function TemporaryChatBadge({
  isTemporary,
  onFinalize,
  loading,
}: TemporaryChatBadgeProps) {
  if (!isTemporary) return null;

  if (!onFinalize) {
    // Read-only mode (no conversion handler available)
    return (
      <Tag icon={<ClockCircleOutlined />} color="orange">
        Temporary
      </Tag>
    );
  }

  return (
    <Popconfirm
      title="Save this chat permanently?"
      description="Messages and settings will be preserved. This action cannot be undone."
      onConfirm={onFinalize}
      okText="Save Permanently"
      cancelText="Cancel"
      okButtonProps={{ loading }}
    >
      <Tag
        icon={loading ? <LoadingOutlined /> : <ClockCircleOutlined />}
        color="orange"
        style={{ cursor: "pointer" }}
      >
        Temporary
      </Tag>
    </Popconfirm>
  );
}

export default TemporaryChatBadge;
