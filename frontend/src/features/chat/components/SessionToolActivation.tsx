// =============================================================================
// PH Agent Hub — SessionToolActivation
// =============================================================================
// Ant Design Drawer+Switch list; GET/POST/DELETE /chat/session/:id/tools.
// Tools are grouped by category with headers.
// =============================================================================

import { useMemo } from "react";
import {
  Drawer,
  List,
  Switch,
  Tag,
  Typography,
  Empty,
  Space,
  message,
  Tooltip,
  Divider,
} from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../../../services/api";
import {
  listSessionTools,
  addSessionTool,
  removeSessionTool,
  setToolAlwaysOn,
  listAlwaysOnTools,
  ToolData,
} from "../services/chat";

const { Text } = Typography;

const CATEGORY_ORDER: Record<string, number> = {
  financial: 1,
  web: 2,
  enterprise: 3,
  utility: 4,
  custom: 5,
  communication: 6,
  devops: 7,
  productivity: 8,
  creative: 9,
  mcp: 10,
  system: 11,
  general: 12,
};

const CATEGORY_LABELS: Record<string, string> = {
  financial: "Financial",
  web: "Web",
  enterprise: "Enterprise",
  utility: "Utility",
  custom: "Custom",
  communication: "Communication",
  devops: "DevOps",
  productivity: "Productivity",
  creative: "Creative",
  mcp: "MCP",
  system: "System",
  general: "General",
};

// System tools are never shown in the UI picker
const HIDDEN_CATEGORIES = new Set(["system"]);

interface SessionToolActivationProps {
  sessionId: string;
  open: boolean;
  onClose: () => void;
  selectedSkillId?: string;
  autoSelectTools?: boolean;
  onAutoSelectToolsChange?: (v: boolean) => void;
  isPending?: boolean;
  pendingActiveToolIds?: string[];
  onPendingToolToggle?: (toolId: string, checked: boolean) => void;
}

export function SessionToolActivation({
  sessionId,
  open,
  onClose,
  selectedSkillId,
  autoSelectTools = true,
  onAutoSelectToolsChange,
  isPending = false,
  pendingActiveToolIds = [],
  onPendingToolToggle,
}: SessionToolActivationProps) {
  const queryClient = useQueryClient();

  // Available tools for the tenant
  const { data: availableTools, isLoading: loadingAvailable } = useQuery({
    queryKey: ["session-tools-available", sessionId],
    queryFn: () => api<ToolData[]>("/chat/session/tools/available"),
    enabled: open,
  });

  // Active tools for this session (skipped when pending — no backend session yet)
  const { data: activeTools, isLoading: loadingActive } = useQuery({
    queryKey: ["session-tools", sessionId],
    queryFn: () => listSessionTools(sessionId),
    enabled: open && !isPending,
  });

  // Always-on tool IDs for this user
  const { data: alwaysOnIds } = useQuery({
    queryKey: ["always-on-tools"],
    queryFn: listAlwaysOnTools,
    enabled: open,
  });

  const alwaysOnSet = new Set(alwaysOnIds || []);

  // Fetch the selected skill's tool IDs
  const { data: allSkills } = useQuery({
    queryKey: ["skills"],
    queryFn: () => api<{ id: string; tool_ids: string[] }[]>("/skills"),
    enabled: open && !!selectedSkillId,
  });
  const selectedSkill = allSkills?.find((s) => s.id === selectedSkillId);
  const skillToolIds = new Set(selectedSkill?.tool_ids ?? []);

  const activeIds = isPending
    ? new Set([...pendingActiveToolIds, ...(skillToolIds || [])])
    : new Set((activeTools || []).map((t) => t.id));

  // Group tools by category, excluding hidden categories
  const groupedTools = useMemo(() => {
    const tools = availableTools || [];
    const groups: Record<string, typeof tools> = {};
    for (const tool of tools) {
      const cat = tool.category || "general";
      if (HIDDEN_CATEGORIES.has(cat)) continue;
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(tool);
    }
    // Sort categories by defined order
    const sorted = Object.entries(groups).sort(([a], [b]) => {
      return (CATEGORY_ORDER[a] || 99) - (CATEGORY_ORDER[b] || 99);
    });
    return sorted;
  }, [availableTools]);

  const addMutation = useMutation({
    mutationFn: (toolId: string) => addSessionTool(sessionId, toolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["session-tools", sessionId] });
    },
    onError: () => message.error("Failed to add tool"),
  });

  const removeMutation = useMutation({
    mutationFn: (toolId: string) => removeSessionTool(sessionId, toolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["session-tools", sessionId] });
    },
    onError: () => message.error("Failed to remove tool"),
  });

  const alwaysOnMutation = useMutation({
    mutationFn: ({ toolId, alwaysOn }: { toolId: string; alwaysOn: boolean }) =>
      setToolAlwaysOn(toolId, alwaysOn),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["always-on-tools"] });
    },
    onError: () => message.error("Failed to update always-on preference"),
  });

  const handleToggle = (toolId: string, checked: boolean) => {
    if (isPending) {
      onPendingToolToggle?.(toolId, checked);
    } else if (checked) {
      addMutation.mutate(toolId);
    } else {
      removeMutation.mutate(toolId);
    }
  };

  return (
    <Drawer
      title="Session Tools"
      open={open}
      onClose={onClose}
      width={420}
    >
      <div style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 8 }}>
        <Switch
          size="small"
          checked={autoSelectTools}
          title="When ON, the LLM can auto-select relevant tools from your tenant-approved pool. When OFF, only manually activated tools are used."
          onChange={(v) => onAutoSelectToolsChange?.(v)}
        />
        <Text style={{ fontSize: 13 }}>
          {autoSelectTools ? "🤖 Auto-select tools" : "🤖 Manual tool selection"}
        </Text>
      </div>
      {groupedTools.length === 0 && !loadingAvailable ? (
        <Empty description="No tools available for your tenant" />
      ) : (
        groupedTools.map(([category, tools]) => (
          <div key={category}>
            <Divider orientation="left" plain style={{ margin: "12px 0 8px" }}>
              <Text strong style={{ fontSize: 13 }}>
                {CATEGORY_LABELS[category] || category}
              </Text>
              <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                ({tools.length})
              </Text>
            </Divider>
            <List
              loading={loadingAvailable || loadingActive}
              dataSource={tools}
              split={false}
              renderItem={(tool) => (
                <List.Item
                  style={{ padding: "6px 0" }}
                  actions={[
                    <Tooltip title="Auto-activate in new sessions" key="always">
                      <Switch
                        size="small"
                        checked={alwaysOnSet.has(tool.id)}
                        onChange={(checked) =>
                          alwaysOnMutation.mutate({
                            toolId: tool.id,
                            alwaysOn: checked,
                          })
                        }
                        loading={alwaysOnMutation.isPending}
                      />
                    </Tooltip>,
                    <Switch
                      key="active"
                      checked={activeIds.has(tool.id)}
                      onChange={(checked) => handleToggle(tool.id, checked)}
                      loading={
                        addMutation.isPending || removeMutation.isPending
                      }
                    />,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space size={4}>
                        {tool.name}
                        {skillToolIds.has(tool.id) && (
                          <Tag color="blue" style={{ fontSize: 11, lineHeight: "18px", margin: 0 }}>
                            from skill
                          </Tag>
                        )}
                      </Space>
                    }
                    description={
                      <Space direction="vertical" size={0}>
                        <Text type="secondary">
                          Type: {tool.type}
                          {alwaysOnSet.has(tool.id) ? " · Always on" : ""}
                        </Text>
                        {tool.enabled ? null : (
                          <Text type="danger">Disabled</Text>
                        )}
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          </div>
        ))
      )}
    </Drawer>
  );
}

export default SessionToolActivation;
