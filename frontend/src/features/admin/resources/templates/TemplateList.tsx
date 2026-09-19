// =============================================================================
// PH Agent Hub — Admin TemplateList
// =============================================================================
// Ant Design Table/List with server-side search, scope filtering,
// sorting, pagination.
// =============================================================================

import { useState, useEffect } from "react";
import {
  Table,
  Button,
  Space,
  Tag,
  Popconfirm,
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
  listTemplates,
  deleteTemplate,
  listTenants,
  TemplateData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../../../shared/hooks/useDebounce";
import { TemplateForm } from "./TemplateForm";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function TemplateList() {
  const [editingTemplate, setEditingTemplate] = useState<TemplateData | null>(null);
  const [duplicatingTemplate, setDuplicatingTemplate] = useState<TemplateData | null>(null);
  const [creating, setCreating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-templates"],
    (p) => listTemplates({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-template-list"],
    queryFn: () => listTenants(),
  });

  const tenantNameById = new Map((tenants?.items || []).map((t) => [t.id, t.name]));

  const deleteMutation = useMutation({
    mutationFn: deleteTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-templates"] });
      message.success("Template deleted");
    },
  });

  const columns = [
    { title: "Title", dataIndex: "title", key: "title", sorter: true },
    {
      title: "Scope",
      dataIndex: "scope",
      key: "scope",
      sorter: true,
      render: (v: string) => <Tag>{v}</Tag>,
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
        return tenantName ? (
          <Text>{tenantName}</Text>
        ) : (
          <Text type="secondary">Unknown tenant</Text>
        );
      },
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, record: TemplateData) => (
        <Space>
          <Button
            icon={<EditOutlined />}
            size="small"
            onClick={() => setEditingTemplate(record)}
          />
          <Button
            icon={<CopyOutlined />}
            size="small"
            onClick={() => setDuplicatingTemplate(record)}
            title="Duplicate"
          />
          <Popconfirm
            title="Delete this template?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const templatesData = data?.items || [];
  const totalTemplates = data?.total || 0;

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={() => setCreating(true)}>
          Create Template
        </Button>
        <Input
          placeholder="Search by title…"
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
          placeholder="Scope"
          allowClear
          style={{ width: 120 }}
          value={params.scope as string | undefined}
          onChange={(value) => updateParams({ scope: value, page: 1 })}
          options={[
            { label: "Tenant", value: "tenant" },
            { label: "User", value: "user" },
            { label: "Role", value: "role" },
          ]}
        />
      </Space>

      {isMobile ? (
        <List
          loading={isLoading}
          dataSource={templatesData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalTemplates,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          renderItem={(template) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Button
                  icon={<EditOutlined />}
                  type="link"
                  onClick={() => setEditingTemplate(template)}
                />,
                <Button
                  icon={<CopyOutlined />}
                  type="link"
                  onClick={() => setDuplicatingTemplate(template)}
                  title="Duplicate"
                />,
                <Popconfirm
                  title="Delete?"
                  onConfirm={() => deleteMutation.mutate(template.id)}
                >
                  <Button icon={<DeleteOutlined />} type="link" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={template.title}
                description={
                  <Space direction="vertical" size={2}>
                    <Tag>{template.scope}</Tag>
                  </Space>
                }
              />
            </Card>
          )}
        />
      ) : (
        <Table
          columns={columns}
          dataSource={templatesData}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalTemplates,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange}
        />
      )}

      <TemplateForm
        open={!!editingTemplate || creating}
        template={creating ? null : editingTemplate}
        onClose={() => {
          setEditingTemplate(null);
          setCreating(false);
        }}
      />

      <TemplateForm
        open={!!duplicatingTemplate}
        template={null}
        duplicateFrom={duplicatingTemplate}
        onClose={() => setDuplicatingTemplate(null)}
      />
    </div>
  );
}

export default TemplateList;
