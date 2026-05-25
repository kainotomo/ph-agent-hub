// =============================================================================
// PH Agent Hub — AdminLayout
// =============================================================================
// Ant Design Layout with fixed Sider on desktop, Drawer on mobile (<768px);
// sidebar nav items; hides Tenants+Settings menu items for manager role;
// role badge in top bar.
// =============================================================================

import { useState } from "react";
import { Layout, Menu, Button, Typography, Space, Tag, Drawer, Grid, Select } from "antd";
import {
  UserOutlined,
  TeamOutlined,
  ApiOutlined,
  ToolOutlined,
  FileTextOutlined,
  ThunderboltOutlined,
  BarChartOutlined,
  SettingOutlined,
  LogoutOutlined,
  MenuOutlined,
  GroupOutlined,
  DatabaseOutlined,
  CommentOutlined,
  MessageOutlined,
  AuditOutlined,
  CloudServerOutlined,
  CodeOutlined,
} from "@ant-design/icons";
import { useNavigate, useLocation, Outlet, useSearchParams } from "react-router-dom";
import { useAuth } from "../../../providers/AuthProvider";
import { Logo } from "../../../shared/components/Logo";
import { useQuery } from "@tanstack/react-query";
import { listTenants } from "../services/admin";
import { version } from "../../../../package.json";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;
const { useBreakpoint } = Grid;

export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const isAdmin = user?.role === "admin";
  const selectedTenantId = searchParams.get("tenant_id") || undefined;

  // Fetch tenant list for the filter dropdown
  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-selector"],
    queryFn: () => listTenants(),
    enabled: isAdmin,
  });

  const handleTenantChange = (value: string | undefined) => {
    setSearchParams((prev) => {
      if (value) {
        prev.set("tenant_id", value);
      } else {
        prev.delete("tenant_id");
      }
      return prev;
    });
  };

  const menuItems = [
    { key: "/admin/users", icon: <UserOutlined />, label: "Users" },
    ...(isAdmin
      ? [{ key: "/admin/tenants", icon: <TeamOutlined />, label: "Tenants" }]
      : []),
    { key: "/admin/models", icon: <ApiOutlined />, label: "Models" },
    { key: "/admin/tools", icon: <ToolOutlined />, label: "Tools" },
    { key: "/admin/mcp-servers", icon: <CloudServerOutlined />, label: "MCP Servers" },
    {
      key: "/admin/templates",
      icon: <FileTextOutlined />,
      label: "Templates",
    },
    {
      key: "/admin/skills",
      icon: <ThunderboltOutlined />,
      label: "Skills",
    },
    {
      key: "/admin/groups",
      icon: <GroupOutlined />,
      label: "Groups",
    },
    {
      key: "/admin/memories",
      icon: <DatabaseOutlined />,
      label: "Memories",
    },
    {
      key: "/admin/rag-documents",
      icon: <FileTextOutlined />,
      label: "RAG Documents",
    },
    {
      key: "/admin/embed",
      icon: <CodeOutlined />,
      label: "Embed Widget",
    },
    {
      key: "/admin/sessions",
      icon: <CommentOutlined />,
      label: "Sessions",
    },
    ...(isAdmin
      ? [
          {
            key: "/admin/audit",
            icon: <AuditOutlined />,
            label: "Audit Log",
          },
        ]
      : []),
    {
      key: "/admin/analytics",
      icon: <BarChartOutlined />,
      label: "Analytics",
    },
    ...(isAdmin
      ? [
          {
            key: "/admin/settings",
            icon: <SettingOutlined />,
            label: "Settings",
          },
        ]
      : []),
  ];

  const selectedKey = menuItems.find((item) =>
    location.pathname.startsWith(item.key),
  )?.key;

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const sidebarContent = (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div
        style={{
          padding: "16px",
          textAlign: "center",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
        }}
      >
        <Logo size={30} showText textColor="#fff" version={version} />
      </div>
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={selectedKey ? [selectedKey] : []}
        items={menuItems}
        onClick={({ key }) => {
          navigate(key);
          setMobileMenuOpen(false);
        }}
        style={{ flex: 1 }}
      />
      <div style={{ padding: "8px 16px", borderTop: "1px solid rgba(255,255,255,0.1)" }}>
        <Button
          type="text"
          icon={<LogoutOutlined />}
          onClick={handleLogout}
          style={{ color: "rgba(255,255,255,0.65)", width: "100%", textAlign: "left" }}
        >
          Logout
        </Button>
      </div>
    </div>
  );

  return (
    <Layout style={{ minHeight: "100vh" }}>
      {isMobile ? (
        <>
          <Header
            style={{
              background: "#001529",
              padding: "0 16px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <Button
              type="text"
              icon={<MenuOutlined style={{ color: "#fff" }} />}
              onClick={() => setMobileMenuOpen(true)}
            />
            <Space>
              <Button
                type="text"
                icon={<MessageOutlined style={{ color: "#fff" }} />}
                onClick={() => navigate("/chat")}
              />
              <Tag color={isAdmin ? "red" : "blue"}>
                {user?.role}
              </Tag>
              <Text style={{ color: "#fff" }}>{user?.display_name}</Text>
            </Space>
          </Header>
          {isAdmin && (
            <div
              style={{
                background: "#fff",
                padding: "8px 16px",
                borderBottom: "1px solid #f0f0f0",
              }}
            >
              <Select
                allowClear
                placeholder="All tenants"
                style={{ width: "100%" }}
                value={selectedTenantId}
                onChange={handleTenantChange}
                options={(tenants?.items || []).map((t) => ({
                  value: t.id,
                  label: t.name,
                }))}
              />
            </div>
          )}
          <Drawer
            open={mobileMenuOpen}
            onClose={() => setMobileMenuOpen(false)}
            placement="left"
            width={260}
            bodyStyle={{ padding: 0, background: "#001529" }}
          >
            {sidebarContent}
          </Drawer>
        </>
      ) : (
        <Sider width={240} theme="dark">
          {sidebarContent}
        </Sider>
      )}

      <Layout>
        {!isMobile && (
          <Header
            style={{
              background: "#fff",
              padding: "0 24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "flex-end",
              borderBottom: "1px solid #f0f0f0",
            }}
          >
            <Space>
              {isAdmin && (
                <Select
                  allowClear
                  placeholder="All tenants"
                  style={{ minWidth: 200 }}
                  value={selectedTenantId}
                  onChange={handleTenantChange}
                  options={(tenants?.items || []).map((t) => ({
                    value: t.id,
                    label: t.name,
                  }))}
                />
              )}
              <Button
                size="small"
                onClick={() => navigate("/chat")}
              >
                Back to Chat
              </Button>
              <Tag color={isAdmin ? "red" : "blue"}>
                {user?.role}
              </Tag>
              <Text>{user?.display_name}</Text>
            </Space>
          </Header>
        )}
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

export default AdminLayout;
