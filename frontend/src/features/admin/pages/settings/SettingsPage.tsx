// =============================================================================
// PH Agent Hub — Admin SettingsPage
// =============================================================================
// Admin only; manages application-wide settings (currency, license key).
// Includes real-time license validation feedback (Issue #243).
// =============================================================================

import { useState, useEffect, useCallback, useRef } from "react";
import {
  Card,
  Typography,
  Form,
  Select,
  Input,
  Button,
  message,
  Spin,
  Tag,
  Space,
  Alert,
  Descriptions,
  Switch,
} from "antd";
import {
  SettingOutlined,
  KeyOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  RocketOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getSettings,
  updateSettings,
  getLicenseStatus,
  getTenantStatus,
} from "../../services/admin";
import type { LicenseStatusData, TenantStatusData } from "../../services/admin";
import { setCurrency } from "../../../../shared/utils/formatCurrency";

const { Title, Text } = Typography;

const CURRENCY_OPTIONS = [
  { value: "EUR", label: "€  EUR" },
  { value: "USD", label: "$  USD" },
  { value: "GBP", label: "£  GBP" },
  { value: "JPY", label: "¥  JPY" },
  { value: "CNY", label: "¥  CNY" },
];

// ---------------------------------------------------------------------------
// License status badge
// ---------------------------------------------------------------------------

function LicenseStatusBadge({ status }: { status: string }) {
  switch (status) {
    case "valid":
      return (
        <Tag icon={<CheckCircleOutlined />} color="success">
          Valid — Pro License
        </Tag>
      );
    case "expired":
      return (
        <Tag icon={<WarningOutlined />} color="warning">
          License Expired
        </Tag>
      );
    case "invalid":
      return (
        <Tag icon={<CloseCircleOutlined />} color="error">
          Invalid License
        </Tag>
      );
    default:
      return (
        <Tag icon={<InfoCircleOutlined />} color="default">
          No License — Free Tier
        </Tag>
      );
  }
}

function formatTenantLimit(limit: number): string {
  return limit === -1 || limit >= 1_000_000 ? "Unlimited" : String(limit);
}

// ---------------------------------------------------------------------------
// SettingsPage
// ---------------------------------------------------------------------------

export function SettingsPage() {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const [licenseInput, setLicenseInput] = useState("");
  const [showKey, setShowKey] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch settings
  const { data: settingsData, isLoading: settingsLoading } = useQuery({
    queryKey: ["admin-settings"],
    queryFn: getSettings,
  });

  // Fetch license status
  const {
    data: licenseData,
    isLoading: licenseLoading,
    refetch: refetchLicense,
  } = useQuery<LicenseStatusData>({
    queryKey: ["admin-license-status"],
    queryFn: getLicenseStatus,
    refetchOnWindowFocus: false,
  });

  // Fetch tenant status
  const { data: tenantData, isLoading: tenantLoading } = useQuery<TenantStatusData>({
    queryKey: ["admin-tenant-status"],
    queryFn: getTenantStatus,
    refetchOnWindowFocus: false,
  });

  // Save settings mutation
  const mutation = useMutation({
    mutationFn: updateSettings,
    onSuccess: (data) => {
      if (data.settings.currency) {
        setCurrency(data.settings.currency);
      }
      message.success("Settings saved");
      queryClient.invalidateQueries({ queryKey: ["admin-settings"] });
      queryClient.invalidateQueries({ queryKey: ["admin-license-status"] });
      queryClient.invalidateQueries({ queryKey: ["admin-tenant-status"] });
    },
    onError: (err: Error) => {
      message.error(err.message || "Failed to save settings");
    },
  });

  // Debounced license validation on input change
  const debouncedValidate = useCallback(
    (_value: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        refetchLicense();
      }, 800);
    },
    [refetchLicense],
  );

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  // Set initial form values when data loads
  useEffect(() => {
    if (settingsData?.settings) {
      const storedLicense = settingsData.settings.license_key || "";
      form.setFieldsValue({
        currency: settingsData.settings.currency || "EUR",
        license_key: storedLicense,
        demo_enabled: settingsData.settings.demo_enabled === "true",
      });
      setLicenseInput(storedLicense);
    }
  }, [settingsData, form]);

  if (settingsLoading || licenseLoading || tenantLoading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  }

  const handleSave = (values: Record<string, string | boolean>) => {
    // Only include license_key if it was changed
    const payload: Record<string, string> = { currency: values.currency as string };
    if (values.license_key !== undefined) {
      payload.license_key = (values.license_key as string) || "";
    }
    // demo_enabled is a boolean from Switch, store as "true"/"false" string
    payload.demo_enabled = values.demo_enabled ? "true" : "false";
    mutation.mutate(payload);
  };

  const handleLicenseChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setLicenseInput(value);
    form.setFieldsValue({ license_key: value });
    debouncedValidate(value);
  };

  const licenseStatus = licenseData?.status || "not_set";
  const tenantCount = tenantData?.total_tenants ?? 0;
  const tenantLimit = tenantData?.effective_limit ?? 3;

  return (
    <div>
      <Title level={4}>
        <SettingOutlined /> Settings
      </Title>

      <Space direction="vertical" size="large" style={{ width: "100%", maxWidth: 600 }}>
        {/* Tenant capacity banner */}
        {tenantData && !tenantData.can_create && (
          <Alert
            type={licenseStatus === "valid" ? "warning" : "info"}
            showIcon
            message={
              licenseStatus === "valid"
                ? "Tenant limit reached"
                : "Free tier limit reached"
            }
            description={tenantData.message || ""}
            action={
              licenseStatus !== "valid" ? (
                <Button size="small" type="primary" href="#license">
                  <KeyOutlined /> Enter License Key
                </Button>
              ) : undefined
            }
          />
        )}

        {/* License status summary */}
        {licenseStatus !== "not_set" && (
          <Card size="small" id="license">
            <Space direction="vertical" style={{ width: "100%" }}>
              <Space>
                <LicenseStatusBadge status={licenseStatus} />
                <Text type="secondary">
                  {tenantCount} of {formatTenantLimit(tenantLimit)} tenants used
                </Text>
              </Space>
              {licenseData?.licensee && licenseStatus === "valid" && (
                <Descriptions size="small" column={2}>
                  <Descriptions.Item label="Licensed to">
                    {licenseData.licensee}
                  </Descriptions.Item>
                  {licenseData.expires_at && (
                    <Descriptions.Item label="Expires">
                      {(() => {
                        const d = new Date(licenseData.expires_at);
                        const day = d.getDate().toString().padStart(2, "0");
                        const months = [
                          "Jan","Feb","Mar","Apr","May","Jun",
                          "Jul","Aug","Sep","Oct","Nov","Dec",
                        ];
                        return `${day}-${months[d.getMonth()]}-${d.getFullYear()}`;
                      })()}
                    </Descriptions.Item>
                  )}
                  {licenseData.max_tenants !== null && (
                    <Descriptions.Item label="Max tenants">
                      {formatTenantLimit(licenseData.max_tenants)}
                    </Descriptions.Item>
                  )}
                </Descriptions>
              )}
            </Space>
          </Card>
        )}

        {/* Settings form */}
        <Card>
          <Form
            form={form}
            layout="vertical"
            onFinish={handleSave}
          >
            {/* Currency */}
            <Form.Item
              name="currency"
              label="Currency"
              tooltip="Used to format cost values across the app"
            >
              <Select options={CURRENCY_OPTIONS} />
            </Form.Item>

            {/* Demo Mode */}
            <Form.Item
              name="demo_enabled"
              label={
                <Space>
                  <RocketOutlined />
                  <span>Demo Mode</span>
                </Space>
              }
              valuePropName="checked"
              tooltip="When enabled, the login page shows a 'Try It Now' button and anonymous visitors can chat via /demo"
            >
              <Switch />
            </Form.Item>

            {/* License Key */}
            <Form.Item
              name="license_key"
              label={
                <Space>
                  <KeyOutlined />
                  <span>License Key</span>
                  <LicenseStatusBadge status={licenseStatus} />
                </Space>
              }
              tooltip="Enter a Pro license key to unlock unlimited tenants and priority support"
            >
              <Input
                placeholder="Paste your license key here"
                value={licenseInput}
                onChange={handleLicenseChange}
                suffix={
                  <Button
                    type="text"
                    size="small"
                    icon={showKey ? <EyeOutlined /> : <EyeInvisibleOutlined />}
                    onClick={() => setShowKey(!showKey)}
                    tabIndex={-1}
                  />
                }
                type={showKey ? "text" : "password"}
              />
            </Form.Item>

            {/* Help text based on status */}
            {licenseStatus === "not_set" && (
              <Alert
                type="info"
                message="Free Tier"
                description={`You can create up to ${tenantLimit} tenants for free. Upgrade to Pro for unlimited tenants.`}
                style={{ marginBottom: 16 }}
              />
            )}
            {licenseStatus === "invalid" && (
              <Alert
                type="error"
                message="Invalid License"
                description="The license key could not be verified. Please check that you copied the entire key correctly."
                style={{ marginBottom: 16 }}
              />
            )}
            {licenseStatus === "expired" && (
              <Alert
                type="warning"
                message="License Expired"
                description="Your Pro license has expired. Tenants beyond the free tier limit are no longer accessible. Renew your license to restore access."
                style={{ marginBottom: 16 }}
              />
            )}

            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={mutation.isPending}
              >
                Save Settings
              </Button>
            </Form.Item>
          </Form>
        </Card>
      </Space>
    </div>
  );
}

export default SettingsPage;
