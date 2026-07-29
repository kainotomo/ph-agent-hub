// =============================================================================
// PH Agent Hub — TemplateSelector
// =============================================================================
// Ant Design Select; fetches GET /templates.
// =============================================================================

import React from "react";
import { Select, Space } from "antd";
import { useQuery } from "@tanstack/react-query";
import api from "../../../services/api";

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
  onChange?: (templateId: string | undefined) => void;
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
    <Select
      value={value}
      onChange={onChange}
      loading={isLoading}
      placeholder="Select template"
      style={{ minWidth: 160 }}
      size="small"
      allowClear
      options={(templates || []).map((t) => ({
        label: t.title,
        value: t.id,
      }))}
      notFoundContent={isLoading ? "Loading..." : "No templates available"}
    />
  );
}

export default TemplateSelector;
