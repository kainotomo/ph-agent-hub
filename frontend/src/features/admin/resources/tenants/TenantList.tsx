// =============================================================================
// PH Agent Hub — Admin TenantList
// =============================================================================
// Admin only; Ant Design Table/List with server-side search, sorting, pagination.
// =============================================================================

import { useState, useEffect } from "react";
import {
  Table,
  Button,
  Space,
  Popconfirm,
  Checkbox,
  message,
  Grid,
  List,
  Card,
  Typography,
  Input,
  Alert,
  Tooltip,
} from "antd";
import {
  EditOutlined,
  DeleteOutlined,
  SearchOutlined,
  WarningOutlined,
  KeyOutlined,
} from "@ant-design/icons";
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import {
  listTenants,
  deleteTenant,
  getTenantStatus,
  TenantData,
} from "../../services/admin";
import type { TenantStatusData } from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";
import { TenantForm } from "./TenantForm";
import { formatCurrency } from "../../../../shared/utils/formatCurrency";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function TenantList() {
  const [editingTenant, setEditingTenant] = useState<TenantData | null>(null);
  const [creating, setCreating] = useState(false);
  const [forceDelete, setForceDelete] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();

  // Tenant capacity status (Issue #243)
  const { data: tenantStatus, isLoading: statusLoading } = useQuery<TenantStatusData>({
    queryKey: ["admin-tenant-status"],
    queryFn: getTenantStatus,
    refetchOnWindowFocus: false,
  });

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-tenants"],
    (p) => listTenants({ ...p }),
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTenant(id, { force: forceDelete }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
      queryClient.invalidateQueries({ queryKey: ["admin-tenant-status"] });
      message.success("Tenant deleted");
      setForceDelete(false);
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to delete tenant");
    },
  });

  const columns = [
    { title: "Name", dataIndex: "name", key: "name", sorter: true },
    {
      title: "Cost",
      dataIndex: "total_cost",
      key: "total_cost",
      sorter: true,
      render: (v: number) => formatCurrency(v),
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      sorter: true,
      render: (v: string) => new Date(v).toLocaleDateString(),
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: TenantData) => (
        <Space>
          <Button
            icon={<EditOutlined />}
            size="small"
            onClick={() => setEditingTenant(record)}
          />
          <Popconfirm
            title={
              forceDelete
                ? "⚠️ This will PERMANENTLY delete the tenant AND ALL related data (users, sessions, files, etc.). Continue?"
                : "Delete this tenant?"
            }
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const tenantsData = data?.items || [];
  const totalTenants = data?.total || 0;
  const atLimit = tenantStatus && !tenantStatus.can_create;
  const nearLimit = !atLimit && tenantStatus && tenantStatus.license_status !== "valid"
    && tenantStatus.total_tenants >= tenantStatus.effective_limit - 1;

  return (
    <div>
      {/* Tenant capacity banner (Issue #243) */}
      {!statusLoading && tenantStatus && (
        <div style={{ marginBottom: 16 }}>
          {atLimit ? (
            <Alert
              type={tenantStatus.license_status === "valid" ? "warning" : "info"}
              icon={<WarningOutlined />}
              message={
                tenantStatus.license_status === "valid"
                  ? "Tenant limit reached"
                  : "Free tier limit reached"
              }
              description={
                <span>
                  {tenantStatus.message || ""}
                  {tenantStatus.license_status !== "valid" && (
                    <>
                      {" "}
                      — <a href="/admin/settings"><KeyOutlined /> Upgrade to Pro</a>
                    </>
                  )}
                </span>
              }
              showIcon
              style={{ marginBottom: 12 }}
            />
          ) : nearLimit && (
            <Alert
              type="info"
              message={`${tenantStatus.total_tenants} of ${tenantStatus.effective_limit} tenants used`}
              description={
                <span>
                  You are approaching the free tier limit.{" "}
                  <a href="/admin/settings"><KeyOutlined /> Upgrade to Pro</a> for unlimited tenants.
                </span>
              }
              showIcon
              closable
              style={{ marginBottom: 12 }}
            />
          )}
        </div>
      )}

      <Space style={{ marginBottom: 16 }} wrap>
        <Tooltip title={atLimit ? (tenantStatus?.message || "Tenant limit reached") : undefined}>
          <Button
            type="primary"
            onClick={() => setCreating(true)}
            disabled={atLimit}
          >
            Create Tenant
          </Button>
        </Tooltip>
        <Checkbox
          checked={forceDelete}
          onChange={(e) => setForceDelete(e.target.checked)}
        >
          Force delete (cascade all data)
        </Checkbox>
        <Input
          placeholder="Search by name…"
          prefix={<SearchOutlined />}
          allowClear
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value);
            updateParams({ page: 1 });
          }}
          style={{ width: 220 }}
        />
      </Space>

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={tenantsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalTenants,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          renderItem={(tenant) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Button
                  icon={<EditOutlined />}
                  type="link"
                  onClick={() => setEditingTenant(tenant)}
                />,
                <Popconfirm
                  title={
                    forceDelete
                      ? "⚠️ This will PERMANENTLY delete the tenant AND ALL related data. Continue?"
                      : "Delete?"
                  }
                  onConfirm={() => deleteMutation.mutate(tenant.id)}
                >
                  <Button icon={<DeleteOutlined />} type="link" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={tenant.name}
                description={
                  <Text type="secondary">
                    Created: {new Date(tenant.created_at).toLocaleDateString()}
                  </Text>
                }
              />
            </Card>
          )}
        />
      ) : (
        <Table
          columns={columns}
          dataSource={tenantsData}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalTenants,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange}
        />
      )}

      <TenantForm
        open={!!editingTenant || creating}
        tenant={creating ? null : editingTenant}
        onClose={() => {
          setEditingTenant(null);
          setCreating(false);
        }}
      />
    </div>
  );
}

export default TenantList;
