// =============================================================================
// PH Agent Hub — Admin AuditList
// =============================================================================
// Ant Design Table with server-side search, action filter, sorting,
// pagination (admin only, read-only).
// =============================================================================

import { useState, useEffect } from "react";
import {
  Table,
  Input,
  Space,
  Tag,
  Typography,
  Grid,
  List,
  Card,
  Tooltip,
  Select,
} from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { useQuery as _useQuery } from "@tanstack/react-query";
import { listAuditLogs, AuditData } from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../../../shared/hooks/useDebounce";

const { useBreakpoint } = Grid;
const { Text, Paragraph } = Typography;

// Friendly labels for common actions
const ACTION_LABELS: Record<string, { label: string; color: string }> = {
  "user.created": { label: "User Created", color: "green" },
  "user.updated": { label: "User Updated", color: "blue" },
  "user.deleted": { label: "User Deleted", color: "red" },
  "tenant.created": { label: "Tenant Created", color: "green" },
  "tenant.updated": { label: "Tenant Updated", color: "blue" },
  "tenant.deleted": { label: "Tenant Deleted", color: "red" },
  "model.created": { label: "Model Created", color: "green" },
  "model.updated": { label: "Model Updated", color: "blue" },
  "model.deleted": { label: "Model Deleted", color: "red" },
  "tool.created": { label: "Tool Created", color: "green" },
  "tool.updated": { label: "Tool Updated", color: "blue" },
  "tool.deleted": { label: "Tool Deleted", color: "red" },
  "template.created": { label: "Template Created", color: "green" },
  "template.updated": { label: "Template Updated", color: "blue" },
  "template.deleted": { label: "Template Deleted", color: "red" },
  "skill.created": { label: "Skill Created", color: "green" },
  "skill.updated": { label: "Skill Updated", color: "blue" },
  "skill.deleted": { label: "Skill Deleted", color: "red" },
  "group.created": { label: "Group Created", color: "green" },
  "group.updated": { label: "Group Updated", color: "blue" },
  "group.deleted": { label: "Group Deleted", color: "red" },
  "memory.deleted": { label: "Memory Deleted", color: "red" },
  "session.deleted": { label: "Session Deleted", color: "red" },
};

const ACTION_OPTIONS = Object.entries(ACTION_LABELS).map(([value, info]) => ({
  value,
  label: info.label,
}));

export function AuditList() {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const [searchText, setSearchText] = useState("");

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-audit"],
    (p) => listAuditLogs({ ...p }),
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const auditsData = data?.items || [];
  const totalAudits = data?.total || 0;

  const columns = [
    {
      title: "Actor",
      dataIndex: "actor_email",
      key: "actor_email",
      width: 200,
      ellipsis: true,
      sorter: true,
      render: (_: string, record: AuditData) => (
        <Space direction="vertical" size={0}>
          <Text strong>{record.actor_full_name || record.actor_email || record.actor_id.slice(0, 8) + "…"}</Text>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {record.actor_role}
          </Text>
        </Space>
      ),
    },
    {
      title: "Action",
      dataIndex: "action",
      key: "action",
      width: 160,
      sorter: true,
      render: (v: string) => {
        const info = ACTION_LABELS[v];
        return info ? (
          <Tag color={info.color}>{info.label}</Tag>
        ) : (
          <Text code style={{ fontSize: 11 }}>{v}</Text>
        );
      },
    },
    {
      title: "Target",
      key: "target",
      width: 140,
      responsive: ["sm" as const],
      render: (_: unknown, record: AuditData) =>
        record.target_type ? (
          <Space size={4}>
            <Text type="secondary" style={{ fontSize: 11 }}>{record.target_type}</Text>
            {record.target_id && (
              <Text code style={{ fontSize: 10 }}>{record.target_id.slice(0, 8)}…</Text>
            )}
          </Space>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Tenant",
      dataIndex: "tenant_name",
      key: "tenant",
      width: 140,
      ellipsis: true,
      responsive: ["lg" as const],
      render: (v: string | null) =>
        v || <Text type="secondary">—</Text>,
    },
    {
      title: "IP",
      dataIndex: "ip_address",
      key: "ip",
      width: 130,
      responsive: ["xl" as const],
      render: (v: string | null) =>
        v ? <Text code style={{ fontSize: 11 }}>{v}</Text> : <Text type="secondary">—</Text>,
    },
    {
      title: "Details",
      key: "details",
      width: 120,
      responsive: ["lg" as const],
      render: (_: unknown, record: AuditData) =>
        record.payload ? (
          <Tooltip
            title={
              <Paragraph style={{ margin: 0, maxWidth: 360, fontSize: 12 }}>
                {JSON.stringify(record.payload, null, 2)}
              </Paragraph>
            }
          >
            <Text style={{ cursor: "pointer", fontSize: 11 }} type="secondary">
              View payload
            </Text>
          </Tooltip>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Time",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      sorter: true,
      defaultSortOrder: "descend" as const,
      render: (v: string) => (
        <Text style={{ fontSize: 12 }}>
          {new Date(v).toLocaleString()}
        </Text>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="Search by action, actor, target, IP…"
          prefix={<SearchOutlined />}
          allowClear
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value);
            updateParams({ page: 1 });
          }}
          style={{ width: 220 }}
        />
        <Select
          placeholder="Action"
          allowClear
          style={{ width: 180 }}
          value={(params as Record<string, string | undefined>).action}
          onChange={(value) => updateParams({ action: value, page: 1 } as any)}
          options={ACTION_OPTIONS}
        />
      </Space>

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={auditsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalAudits,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          locale={{ emptyText: "No audit log entries found" }}
          renderItem={(item) => {
            const actionInfo = ACTION_LABELS[item.action];
            return (
              <Card size="small" style={{ marginBottom: 8 }}>
                <Card.Meta
                  title={
                    <Space>
                      {actionInfo ? (
                        <Tag color={actionInfo.color}>{actionInfo.label}</Tag>
                      ) : (
                        <Text code style={{ fontSize: 11 }}>{item.action}</Text>
                      )}
                    </Space>
                  }
                  description={
                    <>
                      <Paragraph style={{ margin: 0 }}>
                        <Text strong>
                          {item.actor_full_name || item.actor_email || item.actor_id.slice(0, 8) + "…"}
                        </Text>
                        <Text type="secondary"> ({item.actor_role})</Text>
                      </Paragraph>
                      {item.target_type && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {item.target_type}: {item.target_id?.slice(0, 8)}…
                        </Text>
                      )}
                      <br />
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {new Date(item.created_at).toLocaleString()}
                        {item.ip_address && ` · ${item.ip_address}`}
                      </Text>
                    </>
                  }
                />
              </Card>
            );
          }}
        />
      ) : (
        <Table<AuditData>
          columns={columns}
          dataSource={auditsData}
          rowKey="id"
          loading={isLoading}
          size="small"
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalAudits,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange as any}
          locale={{ emptyText: "No audit log entries found" }}
        />
      )}
    </div>
  );
}

export default AuditList;
