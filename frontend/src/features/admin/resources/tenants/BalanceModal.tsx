// =============================================================================
// PH Agent Hub — Balance Modal
// =============================================================================
// Admin only; top-up or deduct funds from a tenant's balance.
// Positive amount = add funds (may enable limit for first time).
// Negative amount = deduct funds.
// =============================================================================

import React from "react";
import { Modal, Form, InputNumber, Input, Button, message, Typography, Space } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  updateTenantBalance,
  disableTenantBalanceLimit,
  TenantData,
} from "../../services/admin";
import { formatCurrency } from "../../../../shared/utils/formatCurrency";

const { Text } = Typography;

interface BalanceModalProps {
  tenant: TenantData | null;
  onClose: () => void;
}

export function BalanceModal({ tenant, onClose }: BalanceModalProps) {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();

  React.useEffect(() => {
    if (tenant) {
      form.resetFields();
    }
  }, [tenant, form]);

  const balanceMutation = useMutation({
    mutationFn: ({
      id,
      amount_eur,
      reason,
    }: {
      id: string;
      amount_eur: number;
      reason: string;
    }) => updateTenantBalance(id, { amount_eur, reason }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
      message.success("Balance updated");
      onClose();
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to update balance");
    },
  });

  const disableMutation = useMutation({
    mutationFn: (id: string) => disableTenantBalanceLimit(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
      message.success("Balance limit removed — tenant is now unlimited");
      onClose();
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to disable balance limit");
    },
  });

  return (
    <Modal
      title={`Balance — ${tenant?.name || ""}`}
      open={!!tenant}
      onCancel={onClose}
      footer={null}
      destroyOnClose
    >
      {tenant && (
        <>
          {/* Current balance display */}
          <div style={{ marginBottom: 16, textAlign: "center" }}>
            <Text type="secondary">Current Balance</Text>
            <div style={{ fontSize: 24, fontWeight: 600 }}>
              {tenant.balance_euros !== null
                ? formatCurrency(tenant.balance_euros)
                : "Unlimited"}
            </div>
            {tenant.warning_threshold_eur !== null && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                Warning threshold: {formatCurrency(tenant.warning_threshold_eur)}
              </Text>
            )}
          </div>

          <Form
            form={form}
            layout="vertical"
            onFinish={(values) => {
              balanceMutation.mutate({
                id: tenant.id,
                amount_eur: values.amount_eur,
                reason: values.reason || "admin_adjustment",
              });
            }}
          >
            <Form.Item
              name="amount_eur"
              label="Amount (€)"
              rules={[
                { required: true, message: "Please enter an amount" },
                {
                  type: "number",
                  min: 0.01,
                  message: "Amount must be at least €0.01",
                },
              ]}
            >
              <InputNumber
                style={{ width: "100%" }}
                min={0.01}
                step={10}
                precision={2}
                placeholder="e.g. 100.00"
                prefix="€"
              />
            </Form.Item>

            <Form.Item
              name="reason"
              label="Reason"
              rules={[{ required: true, message: "Please enter a reason" }]}
            >
              <Input placeholder="e.g. Monthly top-up, Customer credit" />
            </Form.Item>

            <Space style={{ width: "100%", justifyContent: "flex-end" }}>
              <Button
                type="primary"
                htmlType="submit"
                loading={balanceMutation.isPending}
              >
                Add Funds
              </Button>
              <Button
                danger
                onClick={() => {
                  Modal.confirm({
                    title: "Remove balance limit?",
                    content:
                      "This will make the tenant unlimited again. Current balance will be cleared.",
                    onOk: () => disableMutation.mutate(tenant.id),
                  });
                }}
                loading={disableMutation.isPending}
              >
                Remove Limit
              </Button>
              <Button onClick={onClose}>Cancel</Button>
            </Space>
          </Form>
        </>
      )}
    </Modal>
  );
}