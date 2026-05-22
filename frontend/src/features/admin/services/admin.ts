// =============================================================================
// PH Agent Hub — Admin API Service
// =============================================================================
// All admin API calls with server-side pagination, filtering, sorting.
// =============================================================================

import api from "../../../services/api";

// ---------------------------------------------------------------------------
// Shared pagination & list param types
// ---------------------------------------------------------------------------

export interface ListParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort_by?: string;
  sort_dir?: "asc" | "desc";
  tenant_id?: string;
  // Resource-specific filters
  role?: string;
  is_active?: boolean;
  provider?: string;
  enabled?: boolean;
  type?: string;
  category?: string;
  scope?: string;
  execution_type?: string;
  visibility?: string;
  source?: string;
  is_pinned?: boolean;
  action?: string;
  actor_id?: string;
  user_id?: string;
  date_from?: string;
  date_to?: string;
  tag?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// ---------------------------------------------------------------------------
// Data Types
// ---------------------------------------------------------------------------

export interface UserData {
  id: string;
  email: string;
  display_name: string;
  role: string;
  tenant_id: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost: number;
}

export interface TenantData {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost: number;
}

export interface ModelData {
  id: string;
  tenant_id: string;
  name: string;
  model_id: string;
  provider: string;
  base_url: string | null;
  enabled: boolean;
  is_public: boolean;
  max_tokens: number;
  temperature: number;
  routing_priority: number;
  thinking_enabled: boolean;
  reasoning_effort: string | null;
  follow_up_questions_enabled: boolean;
  context_length: number | null;
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  cache_hit_price_per_1m: number | null;
  created_at: string;
  updated_at: string;
}

export interface ToolData {
  id: string;
  tenant_id: string;
  name: string;
  type: string;
  category: string;
  config: Record<string, unknown> | null;
  code: string | null;
  enabled: boolean;
  is_public: boolean;
  created_at: string;
  updated_at: string;
}

export interface TemplateData {
  id: string;
  tenant_id: string;
  title: string;
  description: string;
  system_prompt: string;
  scope: string;
  assigned_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillData {
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
  cross_session_retrieval_enabled?: boolean;
  cross_session_max_snippets?: number;
  cross_session_min_score?: number;
}

export interface UsageData {
  id: string;
  tenant_id: string;
  tenant_name: string | null;
  user_id: string;
  user_email: string | null;
  user_full_name: string | null;
  model_id: string;
  model_name: string | null;
  provider: string | null;
  tokens_in: number;
  tokens_out: number;
  cache_hit_tokens: number | null;
  cost: number | null;
  created_at: string;
}

export interface SettingsData {
  settings: Record<string, string>;
}

export interface AuditData {
  id: string;
  tenant_id: string | null;
  tenant_name: string | null;
  actor_id: string;
  actor_role: string;
  actor_email: string | null;
  actor_full_name: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  payload: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string;
}

export interface GroupData {
  id: string;
  tenant_id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface GroupMemberData {
  id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface GroupModelData {
  id: string;
  name: string;
  provider: string;
  enabled: boolean;
}

export interface GroupToolData {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
}

export interface MemoryData {
  id: string;
  tenant_id: string;
  user_id: string;
  session_id: string | null;
  key: string;
  value: string;
  source: string;
  created_at: string;
  updated_at: string | null;
}

export interface AdminSessionData {
  id: string;
  tenant_id: string;
  user_id: string;
  title: string;
  is_pinned: boolean;
  is_temporary: boolean;
  tags: { id: string; name: string; color: string | null }[];
  created_at: string;
  updated_at: string;
}

// =============================================================================
// Helper: build query string from params
// =============================================================================

function buildQueryString(
  params: Record<string, string | number | boolean | undefined | null>,
): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
    }
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// =============================================================================
// Users
// =============================================================================

export function listUsers(
  params?: ListParams & { role?: string; is_active?: boolean },
): Promise<PaginatedResponse<UserData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<UserData>>(`/admin/users${qs}`);
}

export function getUser(id: string): Promise<UserData> {
  return api<UserData>(`/admin/users/${id}`);
}

export function createUser(data: Partial<UserData> & { password?: string }): Promise<UserData> {
  return api<UserData>("/admin/users", { method: "POST", body: data });
}

export function updateUser(id: string, data: Partial<UserData> & { password?: string }): Promise<UserData> {
  return api<UserData>(`/admin/users/${id}`, { method: "PUT", body: data });
}

export function deleteUser(id: string): Promise<void> {
  return api<void>(`/admin/users/${id}`, { method: "DELETE" });
}

// =============================================================================
// Tenants
// =============================================================================

export function listTenants(
  params?: ListParams,
): Promise<PaginatedResponse<TenantData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<TenantData>>(`/admin/tenants${qs}`);
}

export function createTenant(data: { name: string }): Promise<TenantData> {
  return api<TenantData>("/admin/tenants", { method: "POST", body: data });
}

export function updateTenant(id: string, data: { name: string }): Promise<TenantData> {
  return api<TenantData>(`/admin/tenants/${id}`, { method: "PUT", body: data });
}

export function deleteTenant(id: string, params?: { force?: boolean }): Promise<void> {
  const qs = params?.force ? "?force=true" : "";
  return api<void>(`/admin/tenants/${id}${qs}`, { method: "DELETE" });
}

// =============================================================================
// Models
// =============================================================================

export function listModels(
  params?: ListParams & { provider?: string; enabled?: boolean },
): Promise<PaginatedResponse<ModelData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<ModelData>>(`/admin/models${qs}`);
}

export function getModel(id: string): Promise<ModelData> {
  return api<ModelData>(`/admin/models/${id}`);
}

export function createModel(data: Partial<ModelData> & { api_key?: string }): Promise<ModelData> {
  return api<ModelData>("/admin/models", { method: "POST", body: data });
}

export function updateModel(id: string, data: Partial<ModelData> & { api_key?: string }): Promise<ModelData> {
  return api<ModelData>(`/admin/models/${id}`, { method: "PUT", body: data });
}

export function deleteModel(id: string): Promise<void> {
  return api<void>(`/admin/models/${id}`, { method: "DELETE" });
}

// =============================================================================
// Tools
// =============================================================================

export function listTools(
  params?: ListParams & { type?: string; category?: string; enabled?: boolean },
): Promise<PaginatedResponse<ToolData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<ToolData>>(`/admin/tools${qs}`);
}

export function getTool(id: string): Promise<ToolData> {
  return api<ToolData>(`/admin/tools/${id}`);
}

export function createTool(data: Partial<ToolData>): Promise<ToolData> {
  return api<ToolData>("/admin/tools", { method: "POST", body: data });
}

export function updateTool(id: string, data: Partial<ToolData>): Promise<ToolData> {
  return api<ToolData>(`/admin/tools/${id}`, { method: "PUT", body: data });
}

export function deleteTool(id: string): Promise<void> {
  return api<void>(`/admin/tools/${id}`, { method: "DELETE" });
}

// =============================================================================
// Templates
// =============================================================================

export function listTemplates(
  params?: ListParams & { scope?: string },
): Promise<PaginatedResponse<TemplateData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<TemplateData>>(`/admin/templates${qs}`);
}

export function getTemplate(id: string): Promise<TemplateData> {
  return api<TemplateData>(`/admin/templates/${id}`);
}

export function createTemplate(data: Partial<TemplateData>): Promise<TemplateData> {
  return api<TemplateData>("/admin/templates", { method: "POST", body: data });
}

export function updateTemplate(id: string, data: Partial<TemplateData>): Promise<TemplateData> {
  return api<TemplateData>(`/admin/templates/${id}`, { method: "PUT", body: data });
}

export function deleteTemplate(id: string): Promise<void> {
  return api<void>(`/admin/templates/${id}`, { method: "DELETE" });
}

// =============================================================================
// Skills
// =============================================================================

export function listSkills(
  params?: ListParams & { execution_type?: string; visibility?: string; enabled?: boolean },
): Promise<PaginatedResponse<SkillData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<SkillData>>(`/admin/skills${qs}`);
}

export function getSkill(id: string): Promise<SkillData> {
  return api<SkillData>(`/admin/skills/${id}`);
}

export function createSkill(data: Partial<SkillData>): Promise<SkillData> {
  return api<SkillData>("/admin/skills", { method: "POST", body: data });
}

export function updateSkill(id: string, data: Partial<SkillData>): Promise<SkillData> {
  return api<SkillData>(`/admin/skills/${id}`, { method: "PUT", body: data });
}

export function deleteSkill(id: string): Promise<void> {
  return api<void>(`/admin/skills/${id}`, { method: "DELETE" });
}

// =============================================================================
// Usage & Audit
// =============================================================================

export function listUsage(
  params?: ListParams & { user_id?: string; provider?: string; date_from?: string; date_to?: string },
): Promise<PaginatedResponse<UsageData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<UsageData>>(`/admin/usage${qs}`);
}

export function getSettings(): Promise<SettingsData> {
  return api<SettingsData>("/admin/settings");
}

export function updateSettings(settings: Record<string, string>): Promise<SettingsData> {
  return api<SettingsData>("/admin/settings", { method: "PUT", body: settings });
}

export function listAuditLogs(
  params?: ListParams & { action?: string; actor_id?: string },
): Promise<PaginatedResponse<AuditData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<AuditData>>(`/admin/audit${qs}`);
}

// =============================================================================
// Groups
// =============================================================================

export function listGroups(
  params?: ListParams,
): Promise<PaginatedResponse<GroupData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<GroupData>>(`/admin/groups${qs}`);
}

export function getGroup(id: string): Promise<GroupData> {
  return api<GroupData>(`/admin/groups/${id}`);
}

export function createGroup(data: { name: string }): Promise<GroupData> {
  return api<GroupData>("/admin/groups", { method: "POST", body: data });
}

export function updateGroup(id: string, data: { name: string }): Promise<GroupData> {
  return api<GroupData>(`/admin/groups/${id}`, { method: "PUT", body: data });
}

export function deleteGroup(id: string): Promise<void> {
  return api<void>(`/admin/groups/${id}`, { method: "DELETE" });
}

export function listGroupMembers(groupId: string): Promise<GroupMemberData[]> {
  return api<GroupMemberData[]>(`/admin/groups/${groupId}/members`);
}

export function addGroupMember(groupId: string, userId: string): Promise<void> {
  return api<void>(`/admin/groups/${groupId}/members`, { method: "POST", body: { user_id: userId } });
}

export function removeGroupMember(groupId: string, userId: string): Promise<void> {
  return api<void>(`/admin/groups/${groupId}/members/${userId}`, { method: "DELETE" });
}

export function listGroupModels(groupId: string): Promise<GroupModelData[]> {
  return api<GroupModelData[]>(`/admin/groups/${groupId}/models`);
}

export function assignModelToGroup(groupId: string, modelId: string): Promise<void> {
  return api<void>(`/admin/groups/${groupId}/models`, { method: "POST", body: { model_id: modelId } });
}

export function removeModelFromGroup(groupId: string, modelId: string): Promise<void> {
  return api<void>(`/admin/groups/${groupId}/models/${modelId}`, { method: "DELETE" });
}

export function listGroupTools(groupId: string): Promise<GroupToolData[]> {
  return api<GroupToolData[]>(`/admin/groups/${groupId}/tools`);
}

export function assignToolToGroup(groupId: string, toolId: string): Promise<void> {
  return api<void>(`/admin/groups/${groupId}/tools`, { method: "POST", body: { tool_id: toolId } });
}

export function removeToolFromGroup(groupId: string, toolId: string): Promise<void> {
  return api<void>(`/admin/groups/${groupId}/tools/${toolId}`, { method: "DELETE" });
}

export function listToolGroups(toolId: string): Promise<GroupData[]> {
  return api<GroupData[]>(`/admin/tools/${toolId}/groups`);
}

export function listUserGroups(userId: string): Promise<GroupData[]> {
  return api<GroupData[]>(`/admin/users/${userId}/groups`);
}

export function listModelGroups(modelId: string): Promise<GroupData[]> {
  return api<GroupData[]>(`/admin/models/${modelId}/groups`);
}

// =============================================================================
// Memory (admin)
// =============================================================================

export function listAdminMemories(
  params?: ListParams & { user_id?: string; source?: string },
): Promise<PaginatedResponse<MemoryData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<MemoryData>>(`/admin/memories${qs}`);
}

export function deleteAdminMemory(id: string): Promise<void> {
  return api<void>(`/admin/memories/${id}`, { method: "DELETE" });
}

// =============================================================================
// Sessions (admin)
// =============================================================================

export function listAdminSessions(
  params?: ListParams & { tag?: string; is_pinned?: boolean; is_temporary?: boolean },
): Promise<PaginatedResponse<AdminSessionData>> {
  const qs = buildQueryString({ ...params });
  return api<PaginatedResponse<AdminSessionData>>(`/admin/sessions${qs}`);
}

export function deleteAdminSession(id: string): Promise<void> {
  return api<void>(`/admin/sessions/${id}`, { method: "DELETE" });
}
