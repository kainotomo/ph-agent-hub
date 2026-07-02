// =============================================================================
// PH Agent Hub — Account Settings Page
// =============================================================================
// Manage connected accounts for email, calendar, and tasks.
// Supports OAuth (Google, Microsoft) and manual IMAP setup.
// =============================================================================

import { useEffect, useState } from "react";
import {
  Layout,
  Card,
  List,
  Button,
  Tag,
  Typography,
  Space,
  Modal,
  Form,
  Input,
  Select,
  message,
  Spin,
  Empty,
  Popconfirm,
  Badge,
  Divider,
  Row,
  Col,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  StarOutlined,
  LinkOutlined,
  MailOutlined,
  CalendarOutlined,
  CheckSquareOutlined,
  ArrowLeftOutlined,
  ReloadOutlined,
  DatabaseOutlined,
} from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  listCredentials,
  createCredential,
  deleteCredential,
  testConnection,
  updateCredential,
  testRawImap,
  getToolIdByType,
  getGoogleOAuthUrl,
  getMicrosoftOAuthUrl,
  CredentialData,
  ToolInfo,
} from "../../services/credentials";

const { Content } = Layout;
const { Title, Text } = Typography;

// Tool IDs — admin-created tools for each type.
// These are the expected tool names; the actual IDs are fetched dynamically.
const TOOL_TYPE_NAMES: Record<string, { name: string; icon: React.ReactNode; description: string }> = {
  email: {
    name: "Email",
    icon: <MailOutlined />,
    description: "Read and send emails from your connected accounts",
  },
  calendar: {
    name: "Calendar",
    icon: <CalendarOutlined />,
    description: "Check your schedule, find free time, create events",
  },
  tasks: {
    name: "Tasks",
    icon: <CheckSquareOutlined />,
    description: "Create, update, and manage your to-do lists",
  },
  erpnext: {
    name: "ERPNext",
    icon: <DatabaseOutlined />,
    description: "Query your ERP system, create and manage documents",
  },
};

const PROVIDER_LABELS: Record<string, { label: string; color: string }> = {
  gmail: { label: "Gmail", color: "red" },
  outlook: { label: "Outlook", color: "blue" },
  imap: { label: "IMAP", color: "green" },
  google: { label: "Google", color: "red" },
  microsoft: { label: "Microsoft", color: "blue" },
  erpnext: { label: "ERPNext", color: "orange" },
};

const STATUS_COLORS: Record<string, string> = {
  active: "green",
  expired: "orange",
  revoked: "red",
  error: "red",
};

const STATUS_LABELS: Record<string, string> = {
  active: "Connected",
  expired: "Reconnect Needed",
  revoked: "Access Revoked",
  error: "Connection Error",
};

export function AccountSettingsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [connectModal, setConnectModal] = useState<{
    open: boolean;
    toolId: string;
    toolType: string;
  }>({ open: false, toolId: "", toolType: "" });
  const [imapModal, setImapModal] = useState(false);
  const [erpnextModal, setErpnextModal] = useState(false);

  // Handle OAuth callback redirect in popup window
  useEffect(() => {
    const connected = searchParams.get("connected");
    if (connected === "true") {
      // If we're in a popup window, close it and notify the opener
      if (window.opener) {
        window.opener.postMessage({ type: "oauth-connected" }, window.location.origin);
        window.close();
      } else {
        // Direct navigation — just refresh credentials
        queryClient.invalidateQueries({ queryKey: ["credentials"] });
        message.success("Account connected successfully");
      }
    }
  }, [searchParams, queryClient]);

  // Listen for OAuth popup completion from child windows
  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "oauth-connected") {
        queryClient.invalidateQueries({ queryKey: ["credentials"] });
        message.success("Account connected successfully");
      }
    }
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [queryClient]);

  // Fetch all credentials
  const { data: credentials, isLoading } = useQuery({
    queryKey: ["credentials"],
    queryFn: () => listCredentials(),
  });

  // Group credentials by tool type using name matching
  const grouped = groupByToolType(credentials?.items || []);

  return (
    <Layout style={{ height: "100dvh", overflow: "auto" }}>
      <Content style={{ padding: "24px", maxWidth: 800, margin: "0 auto", width: "100%" }}>
        <Space style={{ marginBottom: 24 }}>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/chat")}>
            Back to Chat
          </Button>
        </Space>

        <Title level={3}>Account Settings</Title>
        <Text type="secondary" style={{ display: "block", marginBottom: 24 }}>
          Connect your email, calendar, and task accounts to let the AI assistant
          read, search, and manage them on your behalf.
        </Text>

        {isLoading ? (
          <div style={{ textAlign: "center", padding: 60 }}>
            <Spin size="large" />
          </div>
        ) : (
          <Row gutter={[16, 16]}>
            {Object.entries(TOOL_TYPE_NAMES).map(([type, info]) => (
              <Col xs={24} key={type}>
                <Card
                  title={
                    <Space>
                      {info.icon}
                      <span>{info.name}</span>
                    </Space>
                  }
                  extra={
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      onClick={() => setConnectModal({ open: true, toolId: type, toolType: type })}
                    >
                      Connect Account
                    </Button>
                  }
                >
                  <Text type="secondary" style={{ display: "block", marginBottom: 16 }}>
                    {info.description}
                  </Text>
                  {(!grouped[type] || grouped[type].length === 0) ? (
                    <Empty
                      description={`No ${info.name.toLowerCase()} accounts connected`}
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                    />
                  ) : (
                    <List
                      dataSource={grouped[type]}
                      renderItem={(cred) => (
                        <List.Item
                          actions={[
                            !cred.is_default && (
                              <Button
                                key="default"
                                type="text"
                                icon={<StarOutlined />}
                                onClick={() => handleSetDefault(cred)}
                                title="Set as default"
                              />
                            ),
                            cred.is_default && (
                              <Tag key="default-tag" color="gold">Default</Tag>
                            ),
                            <Button
                              key="test"
                              type="text"
                              icon={<ReloadOutlined />}
                              onClick={() => handleTest(cred)}
                              title="Test connection"
                            />,
                            <Popconfirm
                              key="delete"
                              title={`Remove "${cred.label}"?`}
                              description="The agent will no longer be able to access this account."
                              onConfirm={() => handleDelete(cred)}
                              okText="Remove"
                              cancelText="Cancel"
                            >
                              <Button
                                type="text"
                                danger
                                icon={<DeleteOutlined />}
                                title="Remove account"
                              />
                            </Popconfirm>,
                          ].filter(Boolean)}
                        >
                          <List.Item.Meta
                            avatar={
                              <Badge
                                status={STATUS_COLORS[cred.status] as "success" | "error" | "warning" | "default"}
                                title={STATUS_LABELS[cred.status]}
                              />
                            }
                            title={
                              <Space>
                                <Text strong>{cred.label}</Text>
                                <Tag color={PROVIDER_LABELS[cred.provider]?.color || "default"}>
                                  {PROVIDER_LABELS[cred.provider]?.label || cred.provider}
                                </Tag>
                                {cred.status === "expired" && (
                                  <Button
                                    size="small"
                                    type="link"
                                    icon={<LinkOutlined />}
                                    onClick={() => handleReconnect(cred)}
                                  >
                                    Reconnect
                                  </Button>
                                )}
                              </Space>
                            }
                            description={cred.email_address || "No email address"}
                          />
                        </List.Item>
                      )}
                    />
                  )}
                </Card>
              </Col>
            ))}
          </Row>
        )}

        {/* Connect Account Modal */}
        <ConnectAccountModal
          open={connectModal.open}
          toolType={connectModal.toolType}
          onClose={() => setConnectModal({ ...connectModal, open: false })}
          onIMAP={() => {
            setConnectModal({ ...connectModal, open: false });
            setImapModal(true);
          }}
          onERPNext={() => {
            setConnectModal({ ...connectModal, open: false });
            setErpnextModal(true);
          }}
        />

        {/* Manual IMAP Setup Modal */}
        <ManualSetupModal
          open={imapModal}
          onClose={() => setImapModal(false)}
          onSaved={() => {
            setImapModal(false);
            queryClient.invalidateQueries({ queryKey: ["credentials"] });
          }}
        />

        {/* ERPNext Setup Modal */}
        <ErpnextSetupModal
          open={erpnextModal}
          onClose={() => setErpnextModal(false)}
          onSaved={() => {
            setErpnextModal(false);
            queryClient.invalidateQueries({ queryKey: ["credentials"] });
          }}
        />
      </Content>
    </Layout>
  );

  // ------------------------------------------------------------------
  // Handlers
  // ------------------------------------------------------------------

  async function handleSetDefault(cred: CredentialData) {
    try {
      await updateCredential(cred.id, { is_default: true });
      message.success(`"${cred.label}" set as default`);
      queryClient.invalidateQueries({ queryKey: ["credentials"] });
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : "Failed to set default");
    }
  }

  async function handleTest(cred: CredentialData) {
    try {
      const result = await testConnection(cred.id);
      if (result.ok) {
        message.success(result.message);
      } else {
        message.warning(result.message);
      }
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : "Connection test failed");
    }
  }

  async function handleDelete(cred: CredentialData) {
    try {
      await deleteCredential(cred.id);
      message.success(`"${cred.label}" removed`);
      queryClient.invalidateQueries({ queryKey: ["credentials"] });
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : "Failed to remove account");
    }
  }

  async function handleReconnect(cred: CredentialData) {
    // Reconnection triggers the OAuth flow
    if (cred.provider === "imap") {
      setImapModal(true);
    } else if (cred.provider === "gmail" || cred.provider === "google") {
      try {
        const { url } = await getGoogleOAuthUrl(cred.tool_id);
        window.open(url, "_blank", "width=600,height=700");
      } catch {
        message.error("Failed to start OAuth flow");
      }
    } else {
      try {
        const { url } = await getMicrosoftOAuthUrl(cred.tool_id);
        window.open(url, "_blank", "width=600,height=700");
      } catch {
        message.error("Failed to start OAuth flow");
      }
    }
    // The page will refresh via the OAuth callback redirect
  }
}

// =============================================================================
// Connect Account Modal
// =============================================================================

function ConnectAccountModal({
  open,
  toolType,
  onClose,
  onIMAP,
  onERPNext,
}: {
  open: boolean;
  toolType: string;
  onClose: () => void;
  onIMAP: () => void;
  onERPNext: () => void;
}) {
  const [connecting, setConnecting] = useState<string | null>(null);

  const handleGoogle = async () => {
    setConnecting("google");
    try {
      const { url } = await getGoogleOAuthUrl(toolType === "tasks" ? "tasks_tool" : `${toolType}_tool`);
      window.open(url, "_blank", "width=600,height=700");
      // The callback redirects to /settings, so we don't need to close here
      message.info("Complete the Google sign-in in the popup window");
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : "Failed to initiate Google sign-in");
    }
    setConnecting(null);
  };

  const handleMicrosoft = async () => {
    setConnecting("microsoft");
    try {
      const { url } = await getMicrosoftOAuthUrl(toolType === "tasks" ? "tasks_tool" : `${toolType}_tool`);
      window.open(url, "_blank", "width=600,height=700");
      message.info("Complete the Microsoft sign-in in the popup window");
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : "Failed to initiate Microsoft sign-in");
    }
    setConnecting(null);
  };

  return (
    <Modal
      title={`Connect ${TOOL_TYPE_NAMES[toolType]?.name || "Account"}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={420}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Button
          block
          size="large"
          icon={<MailOutlined />}
          onClick={handleGoogle}
          loading={connecting === "google"}
        >
          🔵 Google ({toolType === "email" ? "Gmail" : toolType === "calendar" ? "Google Calendar" : "Google Tasks"})
        </Button>

        <Button
          block
          size="large"
          icon={<MailOutlined />}
          onClick={handleMicrosoft}
          loading={connecting === "microsoft"}
        >
          🟦 Microsoft ({toolType === "email" ? "Outlook" : toolType === "calendar" ? "Outlook Calendar" : "Microsoft To Do"})
        </Button>

        {toolType === "email" && (
          <>
            <Divider>or</Divider>
            <Button block size="large" onClick={onIMAP}>
              ✉️ Other Email (IMAP)
            </Button>
            <Text type="secondary" style={{ fontSize: 12, textAlign: "center" }}>
              Use app passwords for Gmail/Outlook with IMAP enabled on your account
            </Text>
          </>
        )}

        {toolType === "erpnext" && (
          <>
            <Divider>or</Divider>
            <Button block size="large" onClick={onERPNext}>
              🔑 Manual ERPNext Setup
            </Button>
            <Text type="secondary" style={{ fontSize: 12, textAlign: "center" }}>
              Enter your ERPNext site URL, API key, and API secret
            </Text>
          </>
        )}
      </Space>
    </Modal>
  );
}

// =============================================================================
// Manual IMAP Setup Modal
// =============================================================================

function ManualSetupModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form] = Form.useForm();
  const [testing, setTesting] = useState(false);
  const [creating, setCreating] = useState(false);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);

      // Look up the email tool ID
      const { tool_id } = await getToolIdByType("email");

      // Create the credential with IMAP details
      await createCredential({
        tool_id,
        label: values.label,
        provider: "imap",
        email_address: values.email,
        credentials: {
          imap_host: values.imap_host,
          imap_port: parseInt(values.imap_port, 10),
          username: values.email,
          password: values.password,
          smtp_host: values.smtp_host,
          smtp_port: parseInt(values.smtp_port, 10),
        },
        is_default: true,
      });

      message.success(`"${values.label}" connected successfully`);
      onSaved();
    } catch (err: unknown) {
      if (err instanceof Error) {
        message.error(err.message);
      }
    }
    setCreating(false);
  };

  const handleTest = async () => {
    try {
      const values = await form.validateFields();
      setTesting(true);
      const result = await testRawImap(
        values.imap_host,
        parseInt(values.imap_port, 10),
        values.email,
        values.password,
      );
      if (result.ok) {
        message.success(result.message);
      } else {
        message.warning(result.message);
      }
    } catch (err: unknown) {
      if (err instanceof Error) {
        message.error(err.message);
      }
      // Form validation errors from validateFields are shown inline
    }
    setTesting(false);
  };

  return (
    <Modal
      title="✉️ Manual Email Setup (IMAP)"
      open={open}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={creating}
      okText="Save Account"
      width={480}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="label"
          label="Account Label"
          rules={[{ required: true, message: "Enter a label (e.g. 'Work Email')" }]}
        >
          <Input placeholder="e.g. Work Email, Personal Gmail" />
        </Form.Item>

        <Form.Item
          name="email"
          label="Email Address"
          rules={[
            { required: true, message: "Enter your email address" },
            { type: "email", message: "Enter a valid email" },
          ]}
        >
          <Input placeholder="you@example.com" />
        </Form.Item>

        <Row gutter={16}>
          <Col span={16}>
            <Form.Item
              name="imap_host"
              label="IMAP Server"
              rules={[{ required: true, message: "Enter IMAP server" }]}
            >
              <Input placeholder="imap.gmail.com" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="imap_port" label="Port" initialValue="993">
              <Input placeholder="993" />
            </Form.Item>
          </Col>
        </Row>

        <Row gutter={16}>
          <Col span={16}>
            <Form.Item
              name="smtp_host"
              label="SMTP Server"
              rules={[{ required: true, message: "Enter SMTP server" }]}
            >
              <Input placeholder="smtp.gmail.com" />
            </Form.Item>
          </Col>
          <Col span={8}>
            <Form.Item name="smtp_port" label="Port" initialValue="587">
              <Input placeholder="587" />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item
          name="password"
          label="Password / App Password"
          rules={[{ required: true, message: "Enter your password or app password" }]}
          extra="Use an app password for Gmail/Outlook if you have 2FA enabled"
        >
          <Input.Password placeholder="App password" />
        </Form.Item>

        <Button
          icon={<ReloadOutlined />}
          onClick={handleTest}
          loading={testing}
          style={{ width: "100%" }}
        >
          Test Connection
        </Button>
      </Form>
    </Modal>
  );
}

// =============================================================================
// ERPNext Setup Modal
// =============================================================================

function ErpnextSetupModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form] = Form.useForm();
  const [creating, setCreating] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loadingTools, setLoadingTools] = useState(false);
  // When there's exactly one tool, store its ID directly (no dropdown needed)
  const [singleToolId, setSingleToolId] = useState<string | null>(null);

  // Fetch available ERPNext tools when modal opens
  useEffect(() => {
    if (open) {
      setLoadingTools(true);
      setSingleToolId(null);
      getToolIdByType("erpnext")
        .then((result) => {
          const toolList = result.tools ?? [{ id: result.tool_id, name: result.tool_id }];
          setTools(toolList);
          if (toolList.length === 1) {
            // Single tool — store ID directly, no dropdown needed
            setSingleToolId(toolList[0].id);
          } else {
            // Multiple tools — pre-select first one in the dropdown
            form.setFieldsValue({ tool_id: toolList[0]?.id });
          }
        })
        .catch(() => {
          setTools([]);
        })
        .finally(() => setLoadingTools(false));
    }
  }, [open, form]);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);

      // Use the directly stored ID when there's a single tool,
      // otherwise use the dropdown selection.
      const tool_id = singleToolId ?? values.tool_id;

      await createCredential({
        tool_id,
        label: values.label,
        provider: "erpnext",
        email_address: values.email || undefined,
        credentials: {
          base_url: values.base_url,
          api_key: values.api_key,
          api_secret: values.api_secret,
        },
        is_default: true,
      });

      message.success(`"${values.label}" connected successfully`);
      onSaved();
    } catch (err: unknown) {
      if (err instanceof Error) {
        message.error(err.message);
      }
    }
    setCreating(false);
  };

  return (
    <Modal
      title="🔑 Connect ERPNext Account"
      open={open}
      onCancel={onClose}
      onOk={handleSave}
      confirmLoading={creating}
      okText="Save Account"
      width={520}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="label"
          label="Account Label"
          rules={[{ required: true, message: "Enter a label (e.g. 'My ERP')" }]}
        >
          <Input placeholder="e.g. My ERP, Production Site" />
        </Form.Item>

        {tools.length > 1 && (
          <Form.Item
            name="tool_id"
            label="ERPNext Tool"
            rules={[{ required: true, message: "Select which ERPNext tool to connect" }]}
          >
            <Select
              placeholder="Select an ERPNext tool"
              loading={loadingTools}
              options={tools.map((t) => ({
                label: t.base_url ? `${t.name} (${t.base_url})` : t.name,
                value: t.id,
              }))}
            />
          </Form.Item>
        )}

        <Form.Item
          name="base_url"
          label="ERPNext Site URL"
          rules={[
            { required: true, message: "Enter your ERPNext site URL" },
            { type: "url", message: "Enter a valid URL (https://...)" },
          ]}
        >
          <Input placeholder="https://erpnext.example.com" />
        </Form.Item>

        <Form.Item
          name="api_key"
          label="API Key"
          rules={[{ required: true, message: "Enter your ERPNext API key" }]}
        >
          <Input placeholder="Your ERPNext API key" />
        </Form.Item>

        <Form.Item
          name="api_secret"
          label="API Secret"
          rules={[{ required: true, message: "Enter your ERPNext API secret" }]}
        >
          <Input.Password placeholder="Your ERPNext API secret" />
        </Form.Item>

        <Form.Item
          name="email"
          label="Email (optional)"
          extra="Your ERPNext login email — for display purposes only"
        >
          <Input placeholder="you@example.com" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

// =============================================================================
// Helpers
// =============================================================================

function groupByToolType(credentials: CredentialData[]): Record<string, CredentialData[]> {
  const grouped: Record<string, CredentialData[]> = {};
  for (const cred of credentials) {
    const toolType = cred.tool_type || cred.tool_id;
    if (!grouped[toolType]) {
      grouped[toolType] = [];
    }
    grouped[toolType].push(cred);
  }
  return grouped;
}
