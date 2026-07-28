// =============================================================================
// PH Agent Hub — ModelSelector
// =============================================================================
// Ant Design Select; fetches GET /models; pre-selects default;
// supports "Set as default" action via star icon.
// When value is "__auto__", shows "Auto (Recommended)".
// =============================================================================

import React from "react";
import { Select, Space, Button, Tooltip, message } from "antd";
import { StarOutlined, StarFilled, ThunderboltOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { setDefaultModel, getMe } from "../../../services/auth";
import api from "../../../services/api";

export const AUTO_ROUTE_VALUE = "__auto__";

interface ModelData {
  id: string;
  tenant_id: string;
  name: string;
  provider: string;
  base_url: string | null;
  enabled: boolean;
  thinking_enabled: boolean;
  max_tokens: number;
  temperature: number;
  auto_route_eligible?: boolean;
  created_at: string;
  updated_at: string;
}

interface ModelSelectorProps {
  value?: string;
  onChange?: (modelId: string) => void;
  style?: React.CSSProperties;
}

export function ModelSelector({ value, onChange, style }: ModelSelectorProps) {
  const queryClient = useQueryClient();

  const { data: models, isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelData[]>("/models"),
  });

  const { data: userProfile } = useQuery({
    queryKey: ["user-me"],
    queryFn: getMe,
  });

  const setDefaultMutation = useMutation({
    mutationFn: (modelId: string | null) => setDefaultModel(modelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user-me"] });
      message.success("Default model updated");
    },
  });

  const defaultModelId = userProfile?.default_model_id;
  const isAutoRoute = value === AUTO_ROUTE_VALUE;
  const isCurrentDefault = value && value === defaultModelId && !isAutoRoute;

  // I5: Auto-select the only available model when no value is selected
  const effectiveModels = models || [];
  const singleModelId = effectiveModels.length === 1 ? effectiveModels[0].id : undefined;
  const effectiveValue = value || singleModelId;

  // Notify parent when auto-selection happens
  React.useEffect(() => {
    if (singleModelId && !value && onChange) {
      onChange(singleModelId);
    }
  }, [singleModelId, value, onChange]);

  return (
    <Space.Compact style={style}>
        <Select
          value={effectiveValue}
          onChange={onChange}
          loading={isLoading}
          placeholder="Select model"
          size="small"
          style={{ minWidth: 160 }}
          allowClear
          options={[
            // Show Auto option when there is not exactly 1 model
            // (0 models: Auto as placeholder; 2+ models: allow user choice)
            ...(effectiveModels.length !== 1
              ? [
                  {
                    label: "⚡ Auto (Recommended)",
                    value: AUTO_ROUTE_VALUE,
                  },
                ]
              : []),
            ...effectiveModels.map((m) => ({
              label: `${m.name} (${m.provider})`,
              value: m.id,
            })),
          ]}
          notFoundContent={isLoading ? "Loading..." : "No models available"}
        />
        {effectiveValue && effectiveValue !== AUTO_ROUTE_VALUE && (
          <Tooltip
            title={
              isCurrentDefault
                ? "This is your default model"
                : "Set as default model"
            }
          >
            <Button
              size="small"
              icon={isCurrentDefault ? <StarFilled /> : <StarOutlined />}
              onClick={() =>
                setDefaultMutation.mutate(isCurrentDefault ? null : effectiveValue)
              }
              loading={setDefaultMutation.isPending}
              type={isCurrentDefault ? "primary" : "default"}
            />
          </Tooltip>
        )}
        {effectiveValue === AUTO_ROUTE_VALUE && (
          <Tooltip title="Model auto-selected on first message">
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              type="primary"
              style={{ borderLeft: "none" }}
            />
          </Tooltip>
        )}
      </Space.Compact>
  );
}

export default ModelSelector;
