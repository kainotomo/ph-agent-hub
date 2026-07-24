// =============================================================================
// PH Agent Hub — TemplateSelector
// =============================================================================
// Ant Design Select; fetches GET /templates.
// =============================================================================

import React from "react";
import { Select, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import api from "../../../services/api";

const { Text } = Typography;

interface TemplateData {
  id: string;
  tenant_id: string;
  title: string;
  description: string;
  system_prompt: string;
  scope: string;
  assigned_user_id: string | null;
  created_at: string;
  updated_at: string;
  tool_ids: string[];
}

interface TemplateSelectorProps {
  value?: string;
  onChange?: (templateId: string) => void;
  style?: React.CSSProperties;
}

export function TemplateSelector({
  value,
  onChange,
  style,
}: TemplateSelectorProps) {
  const { data: templates, isLoading } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api<TemplateData[]>("/templates"),
  });

  const hasTemplates = (templates || []).length > 0;

  // I6: Hide selector when no templates exist
  if (!isLoading && !hasTemplates) {
    return null;
  }

  return (
    <Space direction="vertical" size={0} style={style}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        Template
      </Text>
      <Select
        value={value}
        onChange={onChange}
        loading={isLoading}
        placeholder="Select template"
        style={{ minWidth: 160 }}
        allowClear
        options={(templates || []).map((t) => ({
          label: t.title,
          value: t.id,
        }))}
        notFoundContent={isLoading ? "Loading..." : "No templates available"}
      />
    </Space>
  );
}

export default TemplateSelector;
