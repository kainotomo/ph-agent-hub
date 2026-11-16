// =============================================================================
// PH Agent Hub — Notification Settings (Issue #619)
// =============================================================================
// Browser notifications are an account-level preference with a device-level
// capability: the switch is stored on the user, but the browser permission
// belongs to this device.  The panel therefore shows both, and the master
// switch requests the permission from the user's click — the only gesture
// browsers accept.
// =============================================================================

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Divider,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { BellOutlined } from "@ant-design/icons";

import { useAuth } from "../../providers/AuthProvider";
import {
  DEFAULT_NOTIFICATION_PREFS,
  setNotificationPrefs,
  type NotificationPreferences,
} from "../../services/auth";
import {
  getNotificationPermission,
  isNotificationSupported,
  requestNotificationPermission,
  showBrowserNotification,
  type BrowserNotificationPermission,
} from "../../services/browserNotifications";

const { Text } = Typography;

const DEVICE_LABELS: Record<
  BrowserNotificationPermission,
  { text: string; color: string }
> = {
  granted: { text: "Enabled on this device", color: "green" },
  default: { text: "Not enabled on this device", color: "gold" },
  denied: { text: "Blocked by the browser", color: "red" },
  unsupported: { text: "Not supported by this browser", color: "default" },
};

export function NotificationSettingsSection() {
  const { user, refreshUser } = useAuth();
  const [prefs, setPrefs] = useState<NotificationPreferences>(
    user?.notification_prefs ?? DEFAULT_NOTIFICATION_PREFS,
  );
  const [permission, setPermission] = useState<BrowserNotificationPermission>(
    () => getNotificationPermission(),
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setPrefs(user?.notification_prefs ?? DEFAULT_NOTIFICATION_PREFS);
  }, [user?.notification_prefs]);

  const persist = useCallback(
    async (next: NotificationPreferences) => {
      setSaving(true);
      try {
        const saved = await setNotificationPrefs(next);
        setPrefs(saved);
        await refreshUser();
      } catch {
        message.error("Could not save notification settings");
      } finally {
        setSaving(false);
      }
    },
    [refreshUser],
  );

  const handleMasterChange = useCallback(
    async (checked: boolean) => {
      if (checked) {
        // Ask first: this click is the user gesture the browser requires,
        // and a denied prompt is permanent for the origin.
        const granted = await requestNotificationPermission();
        setPermission(granted);
        if (granted !== "granted") {
          message.warning(
            "Your browser blocked notifications. Allow them in the site settings and try again.",
          );
          return;
        }
      }
      await persist({ ...prefs, enabled: checked });
    },
    [persist, prefs],
  );

  const handleDeviceEnable = useCallback(async () => {
    const granted = await requestNotificationPermission();
    setPermission(granted);
    if (granted === "granted") {
      message.success("Notifications are enabled on this device");
    }
  }, []);

  const handleTest = useCallback(async () => {
    const sent = await showBrowserNotification({
      title: "PH Agent Hub",
      body: "This is a test notification.",
      tag: "ph-test",
      url: "/settings",
      silent: !prefs.sound,
    });
    if (!sent) {
      message.warning("Enable notifications on this device first");
    }
  }, [prefs.sound]);

  if (!isNotificationSupported()) {
    return (
      <Card
        title={
          <Space>
            <BellOutlined />
            Notifications
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Text type="secondary">
          This browser does not support notifications.
        </Text>
      </Card>
    );
  }

  const device = DEVICE_LABELS[permission];

  return (
    <Card
      title={
        <Space>
          <BellOutlined />
          Notifications
        </Space>
      }
      extra={<Tag color={device.color}>{device.text}</Tag>}
      style={{ marginBottom: 24 }}
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <div>
          <Space align="center">
            <Switch
              checked={prefs.enabled}
              onChange={(checked) => void handleMasterChange(checked)}
              loading={saving}
            />
            <Text strong>Notify me when a response finishes</Text>
          </Space>
          <div>
            <Text type="secondary">
              Only while the app is open in a browser tab.
            </Text>
          </div>
        </div>

        <Divider style={{ margin: "4px 0" }} />

        <Space direction="vertical" size={4}>
          <Space>
            <Switch
              checked={prefs.on_completion}
              disabled={!prefs.enabled}
              onChange={(checked) =>
                void persist({ ...prefs, on_completion: checked })
              }
            />
            <Text>Completed responses</Text>
          </Space>
          <Space>
            <Switch
              checked={prefs.on_failure}
              disabled={!prefs.enabled}
              onChange={(checked) =>
                void persist({ ...prefs, on_failure: checked })
              }
            />
            <Text>Runs that fail</Text>
          </Space>
          <Space>
            <Switch
              checked={prefs.sound}
              disabled={!prefs.enabled}
              onChange={(checked) => void persist({ ...prefs, sound: checked })}
            />
            <Text>Play a sound</Text>
          </Space>
        </Space>

        {permission === "denied" && (
          <Alert
            type="warning"
            showIcon
            message="Notifications are blocked in this browser"
            description="Allow notifications for this site in the browser settings, then reload this page."
          />
        )}

        {permission === "default" && (
          <Button onClick={() => void handleDeviceEnable()}>
            Enable on this device
          </Button>
        )}

        {permission === "granted" && (
          <Button onClick={() => void handleTest()}>
            Send test notification
          </Button>
        )}

        <Text type="secondary" style={{ fontSize: 12 }}>
          {prefs.enabled
            ? "Saved to your account. Each device still needs its own browser permission."
            : "Notifications are off. Turning them on asks this browser for permission."}
        </Text>
      </Space>
    </Card>
  );
}

export default NotificationSettingsSection;
