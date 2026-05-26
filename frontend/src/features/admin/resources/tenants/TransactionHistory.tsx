// =============================================================================
// PH Agent Hub — Transaction History Drawer
// =============================================================================
// Admin only; paginated table of balance transactions for a tenant.
// =============================================================================

import { useState } from "react";
import { Drawer, Table, Typography, Tag } from "antd";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import {
  getTenantBalanceTransactions,
  BalanceTransactionData,
  TenantData,
} from "../../services/admin";
import { formatCurrency } from "../../../../shared/utils/formatCurrency";

const { Text } = Typography;

interface TransactionHistoryProps {
  tenant: TenantData | null;
  onClose: () => void;
}

export function TransactionHistory({ tenant, onClose }: TransactionHistoryProps) {
  const [page, setPage] = useState(1);
  const pageSize = 25;

  const { data, isLoading } = useQuery({
    queryKey: ["tenant-balance-transactions", tenant?.id, page],
    queryFn: () =>
      getTenantBalanceTransactions(tenant!.id, { page, page_size: pageSize }),
    enabled: !!tenant,
    placeholderData: keepPreviousData,
  });

  const columns = [
    {
      title: "Date",
      dataIndex: "created_at",
      key: "created_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: "Amount",
      dataIndex: "amount_eur",
      key: "amount_eur",
      width: 120,
      render: (v: number) => {
        const color = v > 0 ? "green" : v < 0 ? "red" : undefined;
        const prefix = v > 0 ? "+" : "";
        return (
          <Text strong style={{ color }}>
            {prefix}{formatCurrency(v)}
          </Text>
        );
      },
    },
    {
      title: "Balance After",
      dataIndex: "balance_after",
      key: "balance_after",
      width: 130,
      render: (v: number) => formatCurrency(v),
    },
    {
      title: "Reason",
      dataIndex: "reason",
      key: "reason",
      render: (v: string) => {
        const colorMap: Record<string, string> = {
          admin_top_up: "blue",
          admin_adjustment: "orange",
          usage_deduction: "default",
          admin_disabled: "red",
        };
        return (
          <Tag color={colorMap[v] || "default"}>
            {v.replace(/_/g, " ")}
          </Tag>
        );
      },
    },
  ];

  return (
    <Drawer
      title={`Transaction History — ${tenant?.name || ""}`}
      open={!!tenant}
      onClose={onClose}
      width={640}
      destroyOnClose
    >
      <Table
        columns={columns}
        dataSource={data?.items || []}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: data?.page || 1,
          pageSize: data?.page_size || pageSize,
          total: data?.total || 0,
          onChange: (p) => setPage(p),
          showTotal: (total, range) =>
            `${range[0]}-${range[1]} of ${total} transactions`,
        }}
        size="small"
      />
    </Drawer>
  );
}
