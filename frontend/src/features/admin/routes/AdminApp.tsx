// =============================================================================
// PH Agent Hub — AdminApp (Router)
// =============================================================================
// Route definitions for admin area; uses AdminLayout; maps sub-routes to
// resource list pages and custom pages.
// =============================================================================

import { Routes, Route, Navigate } from "react-router-dom";
import { AdminLayout } from "../layouts/AdminLayout";
import UserList from "../resources/users/UserList";
import TenantList from "../resources/tenants/TenantList";
import ModelList from "../resources/models/ModelList";
import ToolList from "../resources/tools/ToolList";
import McpServerList from "../resources/mcp/McpServerList";
import TemplateList from "../resources/templates/TemplateList";
import SkillList from "../resources/skills/SkillList";
import GroupList from "../resources/groups/GroupList";
import MemoryList from "../resources/memories/MemoryList";
import RagDocumentList from "../resources/rag/RagDocumentList";
import EmbedConfigList from "../resources/embed/EmbedConfigList";
import SessionList from "../resources/sessions/SessionList";
import AuditList from "../resources/audit/AuditList";
import AnalyticsPage from "../pages/analytics/AnalyticsPage";
import SettingsPage from "../pages/settings/SettingsPage";

export function AdminApp() {
  return (
    <Routes>
      <Route element={<AdminLayout />}>
        <Route index element={<Navigate to="users" replace />} />
        <Route path="users" element={<UserList />} />
        <Route path="tenants" element={<TenantList />} />
        <Route path="models" element={<ModelList />} />
        <Route path="tools" element={<ToolList />} />
        <Route path="mcp-servers" element={<McpServerList />} />
        <Route path="templates" element={<TemplateList />} />
        <Route path="skills" element={<SkillList />} />
        <Route path="groups" element={<GroupList />} />
        <Route path="memories" element={<MemoryList />} />
        <Route path="rag-documents" element={<RagDocumentList />} />
        <Route path="embed" element={<EmbedConfigList />} />
        <Route path="sessions" element={<SessionList />} />
        <Route path="audit" element={<AuditList />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}

export default AdminApp;
