// =============================================================================
// PH Agent Hub — Admin RagDocumentList
// =============================================================================
// Ant Design Table with server-side pagination, search, delete + reindex.
// =============================================================================

import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Table,
  Button,
  Tag,
  Popconfirm,
  Input,
  Space,
  message,
  Grid,
  List,
  Card,
  Typography,
} from "antd";
import {
  DeleteOutlined,
  SearchOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listRagDocuments,
  deleteRagDocument,
  reindexRagDocument,
  RagDocumentData,
} from "../../services/admin";
import { useAdminTable } from "../../hooks/useAdminTable";
import { useDebounce } from "../../hooks/useDebounce";

const { useBreakpoint } = Grid;
const { Text } = Typography;

export function RagDocumentList() {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const queryClient = useQueryClient();
  const [searchText, setSearchText] = useState("");
  const [searchParams] = useSearchParams();
  const tenantId = searchParams.get("tenant_id") || undefined;

  const debouncedSearch = useDebounce(searchText, 300);

  const { data, isLoading, params, updateParams, handleTableChange, setSearch } = useAdminTable(
    ["admin-rag-documents"],
    (p) => listRagDocuments({ ...p, tenant_id: tenantId }),
    { tenant_id: tenantId },
  );

  useEffect(() => {
    setSearch(debouncedSearch || undefined);
  }, [debouncedSearch, setSearch]);

  const deleteMutation = useMutation({
    mutationFn: deleteRagDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-rag-documents"] });
      message.success("Document deleted");
    },
  });

  const reindexMutation = useMutation({
    mutationFn: reindexRagDocument,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-rag-documents"] });
      message.success("Document reindexed");
    },
  });

  const docs = data?.items || [];
  const total = data?.total || 0;

  const columns = [
    {
      title: "Title",
      dataIndex: "title",
      key: "title",
      sorter: true,
      render: (v: string, record: RagDocumentData) => (
        <Space>
          <Text strong>{record.original_filename || v}</Text>
        </Space>
      ),
    },
    {
      title: "Filename",
      dataIndex: "original_filename",
      key: "original_filename",
      render: (v: string | null) => v || "-",
    },
    {
      title: "Type",
      dataIndex: "content_type",
      key: "content_type",
      width: 140,
      render: (v: string | null) => v ? <Tag>{v}</Tag> : "-",
    },
    {
      title: "Chunks",
      dataIndex: "chunk_count",
      key: "chunk_count",
      width: 80,
      sorter: true,
    },
    {
      title: "Created",
      dataIndex: "created_at",
      key: "created_at",
      width: 120,
      render: (v: string | null) => v ? new Date(v).toLocaleDateString() : "-",
    },
    {
      title: "Actions",
      key: "actions",
      width: 120,
      render: (_: unknown, record: RagDocumentData) => (
        <Space>
          <Popconfirm
            title="Re-index this document?"
            description="This will re-chunk and re-embed the extracted text."
            onConfirm={() => reindexMutation.mutate(record.file_id)}
          >
            <Button
              icon={<ReloadOutlined />}
              size="small"
              loading={reindexMutation.isPending}
            />
          </Popconfirm>
          <Popconfirm
            title="Delete this document?"
            description="All indexed chunks will be removed."
            onConfirm={() => deleteMutation.mutate(record.file_id)}
          >
            <Button
              icon={<DeleteOutlined />}
              size="small"
              danger
              loading={deleteMutation.isPending}
            />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (isMobile) {
    return (
      <div>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input
            placeholder="Search…"
            prefix={<SearchOutlined />}
            allowClear
            value={searchText}
            onChange={(e) => {
              setSearchText(e.target.value);
              updateParams({ page: 1 });
            }}
            style={{ width: 200 }}
          />
        </Space>

        <List
          loading={isLoading}
          dataSource={docs}
          pagination={{
            current: params.page || 1,
            pageSize: params.page_size || 25,
            total,
            onChange: (p) => updateParams({ page: p }),
          }}
          renderItem={(item) => (
            <Card
              size="small"
              style={{ marginBottom: 8 }}
              actions={[
                <ReloadOutlined
                  key="reindex"
                  onClick={() => reindexMutation.mutate(item.file_id)}
                />,
                <DeleteOutlined
                  key="delete"
                  onClick={() => deleteMutation.mutate(item.file_id)}
                />,
              ]}
            >
              <Text strong>{item.original_filename || item.title}</Text>
              <br />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {item.content_type || "-"} &middot; {item.chunk_count} chunks
                &middot; {item.created_at ? new Date(item.created_at).toLocaleDateString() : "-"}
              </Text>
            </Card>
          )}
        />
      </div>
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="Search by title, filename…"
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

      <Table
        columns={columns}
        dataSource={docs}
        rowKey="file_id"
        loading={isLoading}
        pagination={{
          current: params.page || 1,
          pageSize: params.page_size || 25,
          total,
          showSizeChanger: true,
          pageSizeOptions: ["10", "25", "50", "100"],
        }}
        onChange={handleTableChange}
        scroll={{ x: 600 }}
      />
    </div>
  );
}

export default RagDocumentList;
