// =============================================================================
// PH Agent Hub — Admin EmbedConfigList
// =============================================================================
// Table of embed widget configurations with create, edit, delete, token
// regeneration, and snippet copy.
// =============================================================================

import { useState } from "react";
import {
  Table,
  Button,
  Space,
  Tag,
  Popconfirm,
  message,
  Grid,
  Typography,
  Tooltip,
  Modal,
} from "antd";
import {
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  KeyOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listEmbedConfigs,
  deleteEmbedConfig,
  regenerateEmbedToken,
  EmbedConfigData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { EmbedConfigForm } from "./EmbedConfigForm";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function EmbedConfigList() {
  const [editingConfig, setEditingConfig] = useState<EmbedConfigData | null>(null);
  const [creating, setCreating] = useState(false);
  const [regeneratedToken, setRegeneratedToken] = useState<string | null>(null);
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const { data, isLoading, params, handleTableChange, setSearch } = useAdminTable(
    ["admin-embed-configs"],
    (p) => listEmbedConfigs({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteEmbedConfig(id),
    onSuccess: () => {
      message.success("Embed config deleted");
      queryClient.invalidateQueries({ queryKey: ["admin-embed-configs"] });
    },
    onError: (err: Error) => message.error(err.message),
  });

  const regenerateMutation = useMutation({
    mutationFn: (id: string) => regenerateEmbedToken(id),
    onSuccess: (data) => {
      message.success("Token regenerated");
      if (data.guest_token) {
        setRegeneratedToken(data.guest_token);
      }
      queryClient.invalidateQueries({ queryKey: ["admin-embed-configs"] });
    },
    onError: (err: Error) => message.error(err.message),
  });

  const columns = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      sorter: true,
      render: (name: string, record: EmbedConfigData) => (
        <Space>
          {name}
          {!record.is_active && <Tag color="default">Inactive</Tag>}
        </Space>
      ),
    },
    {
      title: "Active",
      dataIndex: "is_active",
      key: "is_active",
      width: 80,
      render: (active: boolean) => (
        <Tag color={active ? "green" : "default"}>{active ? "Yes" : "No"}</Tag>
      ),
    },
    {
      title: "Token",
      key: "token",
      width: 200,
      render: (_: unknown, record: EmbedConfigData) =>
        record.guest_token ? (
          <Text code copyable style={{ fontSize: 11 }}>
            {record.guest_token.slice(0, 20)}...
          </Text>
        ) : (
          <Text type="secondary">Hidden</Text>
        ),
    },
    {
      title: "Actions",
      key: "actions",
      width: 280,
      render: (_: unknown, record: EmbedConfigData) => (
        <Space size="small">
          <Tooltip title="Edit config">
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => setEditingConfig(record)}
            />
          </Tooltip>
          <Tooltip title="Regenerate token (old one stops working)">
            <Popconfirm
              title="Regenerate token?"
              description="The current token will stop working immediately."
              onConfirm={() => regenerateMutation.mutate(record.id)}
            >
              <Button
                size="small"
                icon={<KeyOutlined />}
                loading={regenerateMutation.isPending}
              />
            </Popconfirm>
          </Tooltip>
          <Popconfirm
            title="Delete this embed config?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <Text strong style={{ fontSize: 18 }}>
          Embed Widget Configurations
        </Text>
        <Space>
          <Button
            icon={<SearchOutlined />}
            onClick={() => {
              const val = prompt("Search by name:");
              if (val !== null) setSearch(val);
            }}
          >
            Search
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreating(true)}
          >
            New Embed Config
          </Button>
        </Space>
      </div>

      <Table
        columns={columns}
        dataSource={data?.items}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: params.page || 1,
          pageSize: params.page_size || 25,
          total: data?.total,
          showSizeChanger: true,
          showTotal: (total) => `Total ${total} configs`,
        }}
        onChange={handleTableChange}
        scroll={{ x: isMobile ? 600 : undefined }}
        locale={{ emptyText: "No embed configs yet. Create one to get started." }}
      />

      <EmbedConfigForm
        open={creating || !!editingConfig}
        config={editingConfig}
        onClose={() => {
          setCreating(false);
          setEditingConfig(null);
          queryClient.invalidateQueries({ queryKey: ["admin-embed-configs"] });
        }}
        onSuccess={() => {
          setCreating(false);
          setEditingConfig(null);
          queryClient.invalidateQueries({ queryKey: ["admin-embed-configs"] });
        }}
      />

      {/* Regenerate token modal */}
      <Modal
        title="Token Regenerated"
        open={!!regeneratedToken}
        onCancel={() => setRegeneratedToken(null)}
        onOk={() => setRegeneratedToken(null)}
        okText="Done"
        cancelText={null}
      >
        {regeneratedToken && (
          <>
            <p style={{ marginBottom: 12, color: "#666" }}>
              The old token has been invalidated. Copy the new snippet below:
            </p>
            <div
              style={{
                background: "#f5f5f5",
                padding: 12,
                borderRadius: 6,
                border: "1px solid #d9d9d9",
              }}
            >
              <Text
                code
                copyable
                style={{ fontSize: 12, wordBreak: "break-all" }}
              >
                {`<script src="/embed.js" data-ph-token="${regeneratedToken}"></script>`}
              </Text>
            </div>
          </>
        )}
      </Modal>
    </div>
  );
}

export default EmbedConfigList;
