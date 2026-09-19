// =============================================================================
// PH Agent Hub — Admin GroupList
// =============================================================================
// TanStack Query + Ant Design Table with server-side search, sorting,
// pagination; inline expandable panels for member/model/tool management.
// =============================================================================

import { useState, useEffect } from "react";
import {
  Table,
  Button,
  Space,
  Tag,
  Card,
  Grid,
  Popconfirm,
  Select,
  message,
  Typography,
  Row,
  Col,
  List,
  Empty,
  Input,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
  SearchOutlined,
  UsergroupAddOutlined,
  ApiOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listGroups,
  deleteGroup,
  listGroupMembers,
  addGroupMember,
  removeGroupMember,
  listGroupModels,
  assignModelToGroup,
  removeModelFromGroup,
  listGroupTools,
  assignToolToGroup,
  removeToolFromGroup,
  listUsers,
  listModels,
  listTools,
  GroupData,
  GroupMemberData,
  GroupModelData,
  GroupToolData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../../../shared/hooks/useDebounce";
import { GroupForm } from "./GroupForm";

const { Text } = Typography;
const { useBreakpoint } = Grid;

export function GroupList() {
  const queryClient = useQueryClient();
  const screens = useBreakpoint();
  const isMobile = !screens.md;

  const [formOpen, setFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<GroupData | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [searchText, setSearchText] = useState("");
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-groups"],
    (p) => listGroups({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const { data: usersData } = useQuery({
    queryKey: ["admin-users", tenantId],
    queryFn: () => listUsers({ tenant_id: tenantId, page_size: 500 }),
  });

  const { data: modelsData } = useQuery({
    queryKey: ["admin-models", tenantId],
    queryFn: () => listModels({ tenant_id: tenantId, page_size: 500 }),
  });

  const { data: toolsData } = useQuery({
    queryKey: ["admin-tools", tenantId],
    queryFn: () => listTools({ tenant_id: tenantId, page_size: 500 }),
  });

  const users = usersData?.items || [];
  const models = modelsData?.items || [];
  const tools = toolsData?.items || [];

  const deleteMutation = useMutation({
    mutationFn: deleteGroup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-groups"] });
      message.success("Group deleted");
    },
  });

  const handleEdit = (group: GroupData) => {
    setEditingGroup(group);
    setFormOpen(true);
  };

  const handleCreate = () => {
    setEditingGroup(null);
    setFormOpen(true);
  };

  const columns = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      render: (name: string, record: GroupData) => (
        <Button
          type="link"
          style={{ padding: 0 }}
          onClick={() =>
            setExpandedKey(expandedKey === record.id ? null : record.id)
          }
        >
          {name}
        </Button>
      ),
    },
    {
      title: "# Members",
      key: "member_count",
      width: 120,
      render: (_: unknown, record: GroupData) => (
        <MemberCount groupId={record.id} />
      ),
    },
    {
      title: "# Models",
      key: "model_count",
      width: 120,
      render: (_: unknown, record: GroupData) => (
        <ModelCount groupId={record.id} />
      ),
    },
    {
      title: "# Tools",
      key: "tool_count",
      width: 120,
      render: (_: unknown, record: GroupData) => (
        <ToolCount groupId={record.id} />
      ),
    },
    {
      title: "Actions",
      key: "actions",
      width: 160,
      render: (_: unknown, record: GroupData) => (
        <Space>
          <Button size="small" onClick={() => handleEdit(record)}>
            Edit
          </Button>
          <Popconfirm
            title="Delete this group?"
            onConfirm={() => deleteMutation.mutate(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const expandedRowRender = (record: GroupData) => (
    <ExpandablePanels groupId={record.id} users={users || []} models={models || []} tools={tools || []} />
  );

  const groupsData = data?.items || [];
  const totalGroups = data?.total || 0;

  return (
    <div style={{ padding: isMobile ? 8 : 24 }}>
      <Space
        style={{
          marginBottom: 16,
          width: "100%",
          justifyContent: "space-between",
        }}
        wrap
      >
        <Space wrap>
          <Typography.Title level={isMobile ? 5 : 4} style={{ margin: 0 }}>
            Groups
          </Typography.Title>
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
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          Create Group
        </Button>
      </Space>

      {isMobile ? (
        <div>
          {groupsData.map((group) => (
            <Card
              key={group.id}
              size="small"
              style={{ marginBottom: 12 }}
              title={
                <Button
                  type="link"
                  style={{ padding: 0 }}
                  onClick={() =>
                    setExpandedKey(expandedKey === group.id ? null : group.id)
                  }
                >
                  {group.name}
                </Button>
              }
              extra={
                <Space>
                  <Button size="small" onClick={() => handleEdit(group)}>
                    Edit
                  </Button>
                  <Popconfirm
                    title="Delete this group?"
                    onConfirm={() => deleteMutation.mutate(group.id)}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              }
            >
              <Space>
                <Tag>
                  <MemberCount groupId={group.id} /> members
                </Tag>
                <Tag>
                  <ModelCount groupId={group.id} /> models
                </Tag>
                <Tag>
                  <ToolCount groupId={group.id} /> tools
                </Tag>
              </Space>
              {expandedKey === group.id && (
                <ExpandablePanels
                  groupId={group.id}
                  users={users || []}
                  models={models || []}
                  tools={tools || []}
                />
              )}
            </Card>
          ))}
        </div>
      ) : (
        <Table
          dataSource={groupsData}
          columns={columns}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 25,
            total: totalGroups,
            showSizeChanger: true,
            pageSizeOptions: ["10", "25", "50", "100"],
            showTotal: (total, range) => `${range[0]}-${range[1]} of ${total}`,
          }}
          onChange={handleTableChange as any}
          expandable={{
            expandedRowRender,
            expandedRowKeys: expandedKey ? [expandedKey] : [],
            onExpand: (_, record) =>
              setExpandedKey(expandedKey === record.id ? null : record.id),
          }}
        />
      )}

      <GroupForm
        open={formOpen}
        group={editingGroup}
        onClose={() => {
          setFormOpen(false);
          setEditingGroup(null);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper: Member count badge
// ---------------------------------------------------------------------------

function MemberCount({ groupId }: { groupId: string }) {
  const { data: members } = useQuery({
    queryKey: ["admin-group-members", groupId],
    queryFn: () => listGroupMembers(groupId),
  });
  return <>{members?.length ?? 0}</>;
}

function ModelCount({ groupId }: { groupId: string }) {
  const { data: models } = useQuery({
    queryKey: ["admin-group-models", groupId],
    queryFn: () => listGroupModels(groupId),
  });
  return <>{models?.length ?? 0}</>;
}

function ToolCount({ groupId }: { groupId: string }) {
  const { data: tools } = useQuery({
    queryKey: ["admin-group-tools", groupId],
    queryFn: () => listGroupTools(groupId),
  });
  return <>{tools?.length ?? 0}</>;
}

// ---------------------------------------------------------------------------
// Expandable panels: members + models
// ---------------------------------------------------------------------------

function ExpandablePanels({
  groupId,
  users,
  models,
  tools,
}: {
  groupId: string;
  users: { id: string; email: string; display_name: string }[];
  models: { id: string; name: string; provider: string }[];
  tools: { id: string; name: string; type: string; enabled: boolean }[];
}) {
  const queryClient = useQueryClient();

  const { data: members, isLoading: membersLoading } = useQuery({
    queryKey: ["admin-group-members", groupId],
    queryFn: () => listGroupMembers(groupId),
  });

  const { data: groupModels, isLoading: modelsLoading } = useQuery({
    queryKey: ["admin-group-models", groupId],
    queryFn: () => listGroupModels(groupId),
  });

  const { data: groupTools, isLoading: toolsLoading } = useQuery({
    queryKey: ["admin-group-tools", groupId],
    queryFn: () => listGroupTools(groupId),
  });

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [selectedToolId, setSelectedToolId] = useState<string | null>(null);

  const addMemberMutation = useMutation({
    mutationFn: (userId: string) => addGroupMember(groupId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-group-members", groupId] });
      setSelectedUserId(null);
      message.success("Member added");
    },
  });

  const removeMemberMutation = useMutation({
    mutationFn: (userId: string) => removeGroupMember(groupId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-group-members", groupId] });
      message.success("Member removed");
    },
  });

  const assignModelMutation = useMutation({
    mutationFn: (modelId: string) => assignModelToGroup(groupId, modelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-group-models", groupId] });
      setSelectedModelId(null);
      message.success("Model assigned");
    },
  });

  const removeModelMutation = useMutation({
    mutationFn: (modelId: string) => removeModelFromGroup(groupId, modelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-group-models", groupId] });
      message.success("Model removed");
    },
  });

  const assignToolMutation = useMutation({
    mutationFn: (toolId: string) => assignToolToGroup(groupId, toolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-group-tools", groupId] });
      setSelectedToolId(null);
      message.success("Tool assigned");
    },
  });

  const removeToolMutation = useMutation({
    mutationFn: (toolId: string) => removeToolFromGroup(groupId, toolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-group-tools", groupId] });
      message.success("Tool removed");
    },
  });

  const memberIds = new Set((members || []).map((m) => m.id));
  const modelIds = new Set((groupModels || []).map((m) => m.id));
  const toolIds = new Set((groupTools || []).map((t) => t.id));

  const availableUsers = users.filter((u) => !memberIds.has(u.id));
  const availableModels = models.filter((m) => !modelIds.has(m.id));
  const availableTools = tools.filter((t) => !toolIds.has(t.id));

  return (
    <Row gutter={16} style={{ marginTop: 8 }}>
      <Col xs={24} md={8}>
        <Card
          size="small"
          title={
            <Space>
              <UsergroupAddOutlined />
              <span>Members</span>
            </Space>
          }
        >
          <Space direction="vertical" style={{ width: "100%" }}>
            <Space.Compact style={{ width: "100%" }}>
              <Select
                placeholder="Add user..."
                value={selectedUserId}
                onChange={setSelectedUserId}
                style={{ flex: 1 }}
                showSearch
                optionFilterProp="label"
                options={availableUsers.map((u) => ({
                  label: `${u.display_name} (${u.email})`,
                  value: u.id,
                }))}
              />
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!selectedUserId}
                loading={addMemberMutation.isPending}
                onClick={() =>
                  selectedUserId && addMemberMutation.mutate(selectedUserId)
                }
              />
            </Space.Compact>

            {membersLoading ? (
              <Text type="secondary">Loading...</Text>
            ) : (members?.length ?? 0) === 0 ? (
              <Empty description="No members" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              <List
                size="small"
                dataSource={members}
                renderItem={(item: GroupMemberData) => (
                  <List.Item
                    actions={[
                      <Button
                        key="remove"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removeMemberMutation.isPending}
                        onClick={() => removeMemberMutation.mutate(item.id)}
                      />,
                    ]}
                  >
                    <List.Item.Meta
                      title={item.display_name || item.email}
                      description={item.role}
                    />
                  </List.Item>
                )}
              />
            )}
          </Space>
        </Card>
      </Col>

      <Col xs={24} md={8}>
        <Card
          size="small"
          title={
            <Space>
              <ApiOutlined />
              <span>Models</span>
            </Space>
          }
        >
          <Space direction="vertical" style={{ width: "100%" }}>
            <Space.Compact style={{ width: "100%" }}>
              <Select
                placeholder="Assign model..."
                value={selectedModelId}
                onChange={setSelectedModelId}
                style={{ flex: 1 }}
                showSearch
                optionFilterProp="label"
                options={availableModels.map((m) => ({
                  label: `${m.name} (${m.provider})`,
                  value: m.id,
                }))}
              />
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!selectedModelId}
                loading={assignModelMutation.isPending}
                onClick={() =>
                  selectedModelId && assignModelMutation.mutate(selectedModelId)
                }
              />
            </Space.Compact>

            {modelsLoading ? (
              <Text type="secondary">Loading...</Text>
            ) : (groupModels?.length ?? 0) === 0 ? (
              <Empty
                description="No models assigned"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              <List
                size="small"
                dataSource={groupModels}
                renderItem={(item: GroupModelData) => (
                  <List.Item
                    actions={[
                      <Button
                        key="remove"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removeModelMutation.isPending}
                        onClick={() => removeModelMutation.mutate(item.id)}
                      />,
                    ]}
                  >
                    <List.Item.Meta
                      title={item.name}
                      description={
                        <Space>
                          <Tag>{item.provider}</Tag>
                          {!item.enabled && <Tag color="red">Disabled</Tag>}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Space>
        </Card>
      </Col>

      <Col xs={24} md={8}>
        <Card
          size="small"
          title={
            <Space>
              <ToolOutlined />
              <span>Tools</span>
            </Space>
          }
        >
          <Space direction="vertical" style={{ width: "100%" }}>
            <Space.Compact style={{ width: "100%" }}>
              <Select
                placeholder="Assign tool..."
                value={selectedToolId}
                onChange={setSelectedToolId}
                style={{ flex: 1 }}
                showSearch
                optionFilterProp="label"
                options={availableTools.map((t) => ({
                  label: `${t.name} (${t.type})`,
                  value: t.id,
                }))}
              />
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!selectedToolId}
                loading={assignToolMutation.isPending}
                onClick={() =>
                  selectedToolId && assignToolMutation.mutate(selectedToolId)
                }
              />
            </Space.Compact>

            {toolsLoading ? (
              <Text type="secondary">Loading...</Text>
            ) : (groupTools?.length ?? 0) === 0 ? (
              <Empty
                description="No tools assigned"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              <List
                size="small"
                dataSource={groupTools}
                renderItem={(item: GroupToolData) => (
                  <List.Item
                    actions={[
                      <Button
                        key="remove"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={removeToolMutation.isPending}
                        onClick={() => removeToolMutation.mutate(item.id)}
                      />,
                    ]}
                  >
                    <List.Item.Meta
                      title={item.name}
                      description={
                        <Space>
                          <Tag>{item.type}</Tag>
                          {!item.enabled && <Tag color="red">Disabled</Tag>}
                        </Space>
                      }
                    />
                  </List.Item>
                )}
              />
            )}
          </Space>
        </Card>
      </Col>
    </Row>
  );
}

export default GroupList;
