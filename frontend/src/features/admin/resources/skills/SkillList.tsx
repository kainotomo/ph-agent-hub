// =============================================================================
// PH Agent Hub — Admin SkillList
// =============================================================================
// Ant Design Table/List with server-side search, execution_type/visibility/
// enabled filters, sorting, pagination.
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
  listSkills,
  deleteSkill,
  updateSkill,
  listTenants,
  SkillData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";
import { SkillForm } from "./SkillForm";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function SkillList() {
  const [editingSkill, setEditingSkill] = useState<SkillData | null>(null);
  const [duplicatingSkill, setDuplicatingSkill] = useState<SkillData | null>(null);
  const [creating, setCreating] = useState(false);
  const [searchText, setSearchText] = useState("");
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-skills"],
    (p) => listSkills({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: tenants } = useQuery({
    queryKey: ["admin-tenants-skill-list"],
    queryFn: () => listTenants(),
  });

  const tenantNameById = new Map((tenants?.items || []).map((t) => [t.id, t.name]));

  const deleteMutation = useMutation({
    mutationFn: deleteSkill,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-skills"] });
      message.success("Skill deleted");
    },
  });

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateSkill(id, { enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-skills"] });
    },
  });

  const columns = [
    { title: "Title", dataIndex: "title", key: "title", sorter: true },
    {
      title: "Type",
      dataIndex: "execution_type",
      key: "execution_type",
      sorter: true,
      render: (v: string) => {
        const colorMap: Record<string, string> = {
          agent: "blue",
          prompt_based: "blue",
          workflow: "orange",
          workflow_based: "orange",
          goal_based: "green",
        };
        const labelMap: Record<string, string> = {
          agent: "Agent",
          prompt_based: "Prompt Based",
          workflow: "Workflow",
          workflow_based: "Workflow Based",
          goal_based: "Goal Based",
        };
        return <Tag color={colorMap[v] || "default"}>{labelMap[v] || v}</Tag>;
      },
    },
    {
      title: "MAF Key",
      dataIndex: "maf_target_key",
      key: "maf_target_key",
    },
    {
      title: "Visibility",
      dataIndex: "visibility",
      key: "visibility",
      sorter: true,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: "Enabled",
      dataIndex: "enabled",
      key: "enabled",
      render: (_: boolean, record: SkillData) => (
        <Switch
          checked={record.enabled}
          onChange={(checked) =>
            toggleEnabled.mutate({ id: record.id, enabled: checked })
          }
        />
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
      render: (_: unknown, record: SkillData) => (
        <Space>
          <Button
            icon={<EditOutlined />}
            size="small"
            onClick={() => setEditingSkill(record)}
          />
          <Button
            icon={<CopyOutlined />}
            size="small"
            onClick={() => setDuplicatingSkill(record)}
            title="Duplicate"
          />
          <Popconfirm
            title="Delete this skill?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const skillsData = data?.items || [];
  const totalSkills = data?.total || 0;

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Button type="primary" onClick={() => setCreating(true)}>
          Create Skill
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
          placeholder="Type"
          allowClear
          style={{ width: 150 }}
          value={params.execution_type as string | undefined}
          onChange={(value) => updateParams({ execution_type: value, page: 1 })}
          options={[
            { label: "Agent", value: "agent" },
            { label: "Workflow", value: "workflow" },
            { label: "Goal Based", value: "goal_based" },
          ]}
        />
        <Select
          placeholder="Visibility"
          allowClear
          style={{ width: 130 }}
          value={params.visibility as string | undefined}
          onChange={(value) => updateParams({ visibility: value, page: 1 })}
          options={[
            { label: "Tenant", value: "tenant" },
            { label: "User", value: "user" },
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
          dataSource={skillsData}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalSkills,
            onChange: (p) => updateParams({ page: p }),
            showSizeChanger: false,
          }}
          renderItem={(skill) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <Button
                  icon={<EditOutlined />}
                  type="link"
                  onClick={() => setEditingSkill(skill)}
                />,
                <Button
                  icon={<CopyOutlined />}
                  type="link"
                  onClick={() => setDuplicatingSkill(skill)}
                  title="Duplicate"
                />,
                <Popconfirm
                  title="Delete?"
                  onConfirm={() => deleteMutation.mutate(skill.id)}
                >
                  <Button icon={<DeleteOutlined />} type="link" danger />
                </Popconfirm>,
              ]}
            >
              <Card.Meta
                title={skill.title}
                description={
                  <Space direction="vertical" size={2}>
                    <Space>
                      <Tag color="blue">{skill.execution_type}</Tag>
                      <Tag>{skill.visibility}</Tag>
                    </Space>
                    <Text type="secondary">{skill.maf_target_key}</Text>
                    <Switch
                      checked={skill.enabled}
                      onChange={(checked) =>
                        toggleEnabled.mutate({
                          id: skill.id,
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
          dataSource={skillsData}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalSkills,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange}
        />
      )}

      <SkillForm
        open={!!editingSkill || creating}
        skill={creating ? null : editingSkill}
        onClose={() => {
          setEditingSkill(null);
          setCreating(false);
        }}
      />

      <SkillForm
        open={!!duplicatingSkill}
        skill={null}
        duplicateFrom={duplicatingSkill}
        onClose={() => setDuplicatingSkill(null)}
      />
    </div>
  );
}

export default SkillList;
