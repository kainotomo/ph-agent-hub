// =============================================================================
// PH Agent Hub — Admin ModelList
// =============================================================================
// Ant Design Table/List with server-side search, provider/enabled filters,
// sorting, pagination. api_key field masked.
// =============================================================================

import { useState, useEffect } from "react";
import {
  Table,
  Button,
  Space,
  Tag,
  Popconfirm,
  Switch,
  message,
  Grid,
  List,
  Card,
  Typography,
  Select,
  Input,
} from "antd";
import { EditOutlined, DeleteOutlined, CopyOutlined, SearchOutlined } from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listModels,
  deleteModel,
  updateModel,
  listTenants,
  ModelData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";
import { ModelForm } from "./ModelForm";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function ModelList() {
  const [editingModel, setEditingModel] = useState<ModelData | null>(null);
  const [duplicatingModel, setDuplicatingModel] = useState<ModelData | null>(null);
  const [creating, setCreating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-models"],
    (p) => listModels({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-model-list"],
    queryFn: () => listTenants(),
  });

  const tenantNameById = new Map((tenants?.items || []).map((t) => [t.id, t.name]));

  const deleteMutation = useMutation({
    mutationFn: deleteModel,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
      message.success("Model deleted");
    },
    onError: (error: Error) => {
      message.error(error.message || "Failed to delete model");
    },
  });

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateModel(id, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
    },
  });

  const toggleFollowUp = useMutation({
    mutationFn: ({
      id,
      follow_up_questions_enabled,
    }: {
      id: string;
      follow_up_questions_enabled: boolean;
    }) => updateModel(id, { follow_up_questions_enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-models"] });
    },
  });

  const columns = [
    { title: "Name", dataIndex: "name", key: "name", sorter: true },
    {
      title: "Provider",
      dataIndex: "provider",
      key: "provider",
      sorter: true,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: "Model ID",
      dataIndex: "model_id",
      key: "model_id",
      render: (v: string | null) =>
        v ? <Text code>{v}</Text> : <Text type="secondary">—</Text>,
    },
    {
      title: "API Key",
      key: "api_key",
      render: () => <Text type="secondary">••••••••</Text>,
    },
    {
      title: "Max Tokens",
      dataIndex: "max_tokens",
      key: "max_tokens",
      sorter: true,
    },
    {
      title: "Enabled",
      dataIndex: "enabled",
      key: "enabled",
      render: (_: boolean, record: ModelData) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) =>
            toggleEnabled.mutate({ id: record.id, enabled: checked })
          }
        />
      ),
    },
    {
      title: "Follow-ups",
      dataIndex: "follow_up_questions_enabled",
      key: "follow_up_questions_enabled",
      render: (_: boolean, record: ModelData) => (
        <Switch
          checked={record.follow_up_questions_enabled}
          size="small"
          onChange={(checked) =>
            toggleFollowUp.mutate({
              id: record.id,
              follow_up_questions_enabled: checked,
            })
          }
        />
      ),
    },
    {
      title: "Auto-Route",
      dataIndex: "auto_route_eligible",
      key: "auto_route_eligible",
      render: (eligible: boolean) =>
        eligible ? (
          <Tag color="green">Eligible</Tag>
        ) : (
          <Tag>Not Eligible</Tag>
        ),
    },
    {
      title: "Tenant",
      dataIndex: "tenant_id",
      key: "tenant_id",
      width: 130,
      ellipsis: true,
      responsive: ["lg" as const],
      render: (v: string) => {
        const tenantName = tenantNameById.get(v);
        return tenantName ? <Text>{tenantName}</Text> : <Text type="secondary">Unknown tenant</Text>;
      },
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: ModelData) => (
        <Space>
          <Button
            icon={<EditOutlined />}
            size="small"
            onClick={() => setEditingModel(record)}
          />
          <Button
            icon={<CopyOutlined />}
            size="small"
            onClick={() => setDuplicatingModel(record)}
            title="Duplicate"
          />
          <Popconfirm
            title="Delete this model?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const modelsData = data?.items || [];
  const totalModels = data?.total || 0;

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={() => setCreating(true)}>
          Create Model
        </Button>
        <Input
          placeholder="Search by name, model ID…"
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
          placeholder="Provider"
          allowClear
          style={{ width: 140 }}
          value={params.provider as string | undefined}
          onChange={(value) => updateParams({ provider: value, page: 1 })}
          options={[
            { label: "OpenAI", value: "openai" },
            { label: "Anthropic", value: "anthropic" },
            { label: "DeepSeek", value: "deepseek" },
          ]}
        />
        <Select
          placeholder="Status"
          allowClear
          style={{ width: 120 }}
          value={params.enabled !== undefined ? String(params.enabled) : undefined}
          onChange={(value) =>
            updateParams({
              enabled: value !== undefined ? value === "true" : undefined,
              page: 1,
            })
          }
          options={[
            { label: "Enabled", value: "true" },
            { label: "Disabled", value: "false" },
          ]}
        />
      </Space>

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={modelsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalModels,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          renderItem={(model) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Button
                  icon={<EditOutlined />}
                  type="link"
                  onClick={() => setEditingModel(model)}
                />,
                <Button
                  icon={<CopyOutlined />}
                  type="link"
                  onClick={() => setDuplicatingModel(model)}
                  title="Duplicate"
                />,
                <Popconfirm
                  title="Delete?"
                  onConfirm={() => deleteMutation.mutate(model.id)}
                >
                  <Button icon={<DeleteOutlined />} type="link" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={model.name}
                description={
                  <Space direction="vertical" size={2}>
                    <Tag>{model.provider}</Tag>
                    <Text type="secondary">
                      Max tokens: {model.max_tokens} · Temp: {model.temperature}
                    </Text>
                    {model.auto_route_eligible ? (
                      <Tag color="green" style={{ fontSize: 11 }}>Auto-Route</Tag>
                    ) : (
                      <Tag style={{ fontSize: 11 }}>No Auto-Route</Tag>
                    )}
                    <Switch
                      checked={model.enabled}
                      onChange={(checked) =>
                        toggleEnabled.mutate({
                          id: model.id,
                          enabled: checked,
                        })
                      }
                      size="small"
                    />
                  </Space>
                }
              />
            </Card>
          )}
        />
      ) : (
        <Table
          columns={columns}
          dataSource={modelsData}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalModels,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange}
        />
      )}

      <ModelForm
        open={!!editingModel || creating}
        model={creating ? null : editingModel}
        onClose={() => {
          setEditingModel(null);
          setCreating(false);
        }}
      />

      <ModelForm
        open={!!duplicatingModel}
        model={null}
        duplicateFrom={duplicatingModel}
        onClose={() => setDuplicatingModel(null)}
      />
    </div>
  );
}

export default ModelList;
