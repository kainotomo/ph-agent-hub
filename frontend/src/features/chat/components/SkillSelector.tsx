// =============================================================================
// PH Agent Hub — SkillSelector
// =============================================================================
// Ant Design Select; fetches GET /skills (tenant+personal);
// launches PersonalSkillEditor.
// =============================================================================

import React, { useState } from "react";
import { Select, Space, Tag, Button } from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import api from "../../../services/api";
import { PersonalSkillEditor } from "./PersonalSkillEditor";

interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

interface SkillData {
  id: string;
  tenant_id: string;
  user_id: string | null;
  title: string;
  description: string;
  execution_type: string;
  maf_target_key: string;
  visibility: string;
  template_id: string | null;
  default_prompt_id: string | null;
  default_model_id: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  tool_ids: string[];
  goal?: string;
  constraints?: string[];
  success_criteria?: string;
  agent_config?: { max_turns?: number; model?: string };
}

interface SkillSelectorProps {
  value?: string;
  onChange?: (skillId: string | undefined) => void;
  style?: React.CSSProperties;
}

export function SkillSelector({
  value,
  onChange,
  style,
}: SkillSelectorProps) {
  const [editorOpen, setEditorOpen] = useState(false);

  const { data: skills, isLoading } = useQuery({
    queryKey: ["skills"],
    queryFn: () => api<PaginatedResponse<SkillData>>("/skills"),
  });

  const skillsList = skills?.items || [];

  // I6: Hide selector when no skills exist
  if (!isLoading && skillsList.length === 0) {
    return null;
  }

  return (
    <>
      <Space.Compact style={style}>
        <Select
          value={value}
          onChange={onChange}
          loading={isLoading}
          placeholder="Select skill"
          style={{ minWidth: 160 }}
          size="small"
          allowClear
          options={skillsList.map((s) => ({
            label: (
              <Space size={4}>
                {s.title}
                {s.execution_type === "goal_based" && (
                  <Tag color="green" style={{ fontSize: 11, lineHeight: "18px", margin: 0 }}>
                    Goal
                  </Tag>
                )}
                {s.tool_ids && s.tool_ids.length > 0 && (
                  <Tag color="blue" style={{ fontSize: 11, lineHeight: "18px", margin: 0 }}>
                    {s.tool_ids.length} tool{s.tool_ids.length !== 1 ? "s" : ""}
                  </Tag>
                )}
              </Space>
            ),
            value: s.id,
          }))}
          notFoundContent={isLoading ? "Loading..." : "No skills available"}
        />
        <Button
          size="small"
          icon={<SettingOutlined />}
          onClick={() => setEditorOpen(true)}
          title="Manage personal skills"
        />
      </Space.Compact>

      <PersonalSkillEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
      />
    </>
  );
}

export default SkillSelector;
