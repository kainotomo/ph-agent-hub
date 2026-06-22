// =============================================================================
// PH Agent Hub — Admin A2aCallLogList
// =============================================================================
// Ant Design Table with server/status/date filters, pagination,
// expandable rows for trace_id / error_message (read-only).
// =============================================================================

import { useState } from "react";
import {
  Table,
  Tag,
  Space,
  Typography,
  Grid,
  List,
  Card,
  Select,
  Descriptions,
  DatePicker,
} from "antd";
import dayjs from "dayjs";
import { useQuery } from "@tanstack/react-query";
import { listA2aCallLogs, listA2aServers, A2aCallLogData } from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";

const { useBreakpoint } = Grid;
const { Text } = Typography;
const { RangePicker } = DatePicker;

// ---------------------------------------------------------------------------
// Status tag config
// ---------------------------------------------------------------------------
const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  success: { label: "Success", color: "green" },
  error: { label: "Error", color: "red" },
  timeout: { label: "Timeout", color: "orange" },
  circuit_open: { label: "Circuit Open", color: "purple" },
};

const STATUS_OPTIONS = Object.entries(STATUS_CONFIG).map(([value, info]) => ({
  value,
  label: info.label,
}));

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export function A2aCallLogList() {
  const screens = useBreakpoint();
  const isMobile = !screens.md;

  const { data, isLoading, params, updateParams, handleTableChange } = useAdminTable(
    ["admin-a2a-call-logs"],
    (p) => listA2aCallLogs({ ...p }),
  );

  // Fetch A2A servers for the server filter dropdown
  const { data: serversData } = useQuery({
    queryKey: ["admin-a2a-servers-for-call-logs"],
    queryFn: () => listA2aServers({ page_size: 200 }),
  });

  const serverOptions = (serversData?.items || []).map((s) => ({
    value: s.id,
    label: s.name,
  }));

  const callLogsData = data?.items || [];
  const totalCallLogs = data?.total || 0;

  // Helper to safely read a string param from the raw params object
  const getParam = (key: string): string | undefined =>
    (params as Record<string, unknown>)[key] as string | undefined;

  // -----------------------------------------------------------------------
  // Columns
  // -----------------------------------------------------------------------
  const columns = [
    {
      title: "Timestamp",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      defaultSortOrder: "descend" as const,
      render: (v: string) => (
        <Text style={{ fontSize: 12 }}>{new Date(v).toLocaleString()}</Text>
      ),
    },
    {
      title: "Server",
      dataIndex: "a2a_server_name",
      key: "a2a_server_name",
      width: 160,
      ellipsis: true,
      render: (v: string | null) =>
        v ? <Text>{v}</Text> : <Text type="secondary">—</Text>,
    },
    {
      title: "Skill ID",
      dataIndex: "skill_id",
      key: "skill_id",
      width: 140,
      ellipsis: true,
      responsive: ["md" as const],
      render: (v: string | null) =>
        v ? (
          <Text code style={{ fontSize: 11 }}>{v}</Text>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (v: string) => {
        const cfg = STATUS_CONFIG[v];
        return cfg ? (
          <Tag color={cfg.color}>{cfg.label}</Tag>
        ) : (
          <Tag>{v}</Tag>
        );
      },
    },
    {
      title: "Latency (ms)",
      dataIndex: "latency_ms",
      key: "latency_ms",
      width: 110,
      responsive: ["sm" as const],
      render: (v: number | null) =>
        v !== null && v !== undefined ? (
          <Text>{v.toLocaleString()}</Text>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: "Retries",
      dataIndex: "retry_count",
      key: "retry_count",
      width: 80,
      responsive: ["sm" as const],
      render: (v: number) => <Text>{v}</Text>,
    },
    {
      title: "Error",
      dataIndex: "error_message",
      key: "error_message",
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          <Text
            type="danger"
            style={{ fontSize: 12, maxWidth: 240 }}
            ellipsis={{ tooltip: v }}
          >
            {v}
          </Text>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
  ];

  // -----------------------------------------------------------------------
  // Expandable row
  // -----------------------------------------------------------------------
  const expandedRowRender = (record: A2aCallLogData) => (
    <Descriptions
      size="small"
      column={1}
      bordered
      style={{ margin: 0 }}
    >
      <Descriptions.Item label="Trace ID">
        <Text code style={{ fontSize: 12, wordBreak: "break-all" }}>
          {record.trace_id}
        </Text>
      </Descriptions.Item>
      <Descriptions.Item label="Error Message">
        {record.error_message ? (
          <Text style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {record.error_message}
          </Text>
        ) : (
          <Text type="secondary">—</Text>
        )}
      </Descriptions.Item>
    </Descriptions>
  );

  // -----------------------------------------------------------------------
  // Status tag colour for mobile cards
  // -----------------------------------------------------------------------
  const statusTag = (status: string) => {
    const cfg = STATUS_CONFIG[status];
    return cfg ? (
      <Tag color={cfg.color}>{cfg.label}</Tag>
    ) : (
      <Tag>{status}</Tag>
    );
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  return (
    <div>
      {/* ---- Filter bar ---- */}
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="Server"
          allowClear
          style={{ width: 200 }}
          value={getParam("a2a_server_id")}
          onChange={(value) => updateParams({ a2a_server_id: value, page: 1 })}
          options={serverOptions}
          loading={!serversData}
        />
        <Select
          placeholder="Status"
          allowClear
          style={{ width: 160 }}
          value={getParam("status")}
          onChange={(value) => updateParams({ status: value, page: 1 })}
          options={STATUS_OPTIONS}
        />
        <RangePicker
          value={[
            getParam("date_from") ? dayjs(getParam("date_from")) : null,
            getParam("date_to") ? dayjs(getParam("date_to")) : null,
          ]}
          onChange={(dates) => {
            updateParams({
              date_from: dates?.[0]?.format("YYYY-MM-DD") || undefined,
              date_to: dates?.[1]?.format("YYYY-MM-DD") || undefined,
              page: 1,
            });
          }}
        />
      </Space>

      {/* ---- Mobile card list ---- */}
      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={callLogsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalCallLogs,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          locale={{ emptyText: "No A2A call logs found" }}
          renderItem={(item) => (
            <Card size="small" style={{ marginBottom: 8 }}>
              <Card.Meta
                title={
                  <Space>
                    {statusTag(item.status)}
                    <Text style={{ fontSize: 12 }}>
                      {new Date(item.created_at).toLocaleString()}
                    </Text>
                  </Space>
                }
                description={
                  <>
                    <Text style={{ fontSize: 12 }}>
                      <Text strong>Server:</Text>{" "}
                      {item.a2a_server_name || "—"}
                    </Text>
                    <br />
                    {item.skill_id && (
                      <>
                        <Text style={{ fontSize: 12 }}>
                          <Text strong>Skill:</Text>{" "}
                          <Text code style={{ fontSize: 11 }}>{item.skill_id}</Text>
                        </Text>
                        <br />
                      </>
                    )}
                    <Text style={{ fontSize: 12 }}>
                      <Text strong>Latency:</Text>{" "}
                      {item.latency_ms !== null
                        ? `${item.latency_ms.toLocaleString()} ms`
                        : "—"}
                      {" | "}
                      <Text strong>Retries:</Text> {item.retry_count}
                    </Text>
                    <br />
                    <Text style={{ fontSize: 12 }}>
                      <Text strong>Trace ID:</Text>{" "}
                      <Text code style={{ fontSize: 10 }}>
                        {item.trace_id}
                      </Text>
                    </Text>
                    {item.error_message && (
                      <>
                        <br />
                        <Text type="danger" style={{ fontSize: 12 }}>
                          <Text strong>Error:</Text> {item.error_message}
                        </Text>
                      </>
                    )}
                  </>
                }
              />
            </Card>
          )}
        />
      ) : (
        /* ---- Desktop table ---- */
        <Table<A2aCallLogData>
          columns={columns}
          dataSource={callLogsData}
          rowKey="id"
          loading={isLoading}
          size="small"
          expandable={{
            expandedRowRender,
            rowExpandable: () => true,
          }}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalCallLogs,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange as any}
          locale={{ emptyText: "No A2A call logs found" }}
        />
      )}
    </div>
  );
}

export default A2aCallLogList;
