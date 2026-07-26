// =============================================================================
// PH Agent Hub — AdminApp (Router)
// =============================================================================
// Route definitions for admin area; uses AdminLayout; maps sub-routes to
// resource list pages and custom pages.
// =============================================================================

import React, { Suspense } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { AdminLayout } from "../layouts/AdminLayout";

const UserList = React.lazy(() => import("../resources/users/UserList"));
const TenantList = React.lazy(() => import("../resources/tenants/TenantList"));
const ModelList = React.lazy(() => import("../resources/models/ModelList"));
const ToolList = React.lazy(() => import("../resources/tools/ToolList"));
const McpServerList = React.lazy(() => import("../resources/mcp/McpServerList"));
const A2aServerList = React.lazy(() => import("../resources/a2a/A2aServerList"));
const A2aCallLogList = React.lazy(
  () => import("../resources/a2a/A2aCallLogList"),
);
const TemplateList = React.lazy(
  () => import("../resources/templates/TemplateList"),
);
const SkillList = React.lazy(() => import("../resources/skills/SkillList"));
const GroupList = React.lazy(() => import("../resources/groups/GroupList"));
const MemoryList = React.lazy(() => import("../resources/memories/MemoryList"));
const RagDocumentList = React.lazy(
  () => import("../resources/rag/RagDocumentList"),
);
const EmbedConfigList = React.lazy(
  () => import("../resources/embed/EmbedConfigList"),
);
const SessionList = React.lazy(
  () => import("../resources/sessions/SessionList"),
);
const AuditList = React.lazy(() => import("../resources/audit/AuditList"));
const AnalyticsPage = React.lazy(
  () => import("../pages/analytics/AnalyticsPage"),
);
const SettingsPage = React.lazy(
  () => import("../pages/settings/SettingsPage"),
);

/** Fallback while a lazy admin route loads. */
function AdminFallback() {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        height: "100%",
        minHeight: 200,
        color: "#999",
        fontFamily: "system-ui, sans-serif",
        fontSize: 14,
      }}
    >
      Loading…
    </div>
  );
}

export function AdminApp() {
  return (
    <Routes>
      <Route element={<AdminLayout />}>
        <Route index element={<Navigate to="users" replace />} />
        <Route
          path="users"
          element={
            <Suspense fallback={<AdminFallback />}>
              <UserList />
            </Suspense>
          }
        />
        <Route
          path="tenants"
          element={
            <Suspense fallback={<AdminFallback />}>
              <TenantList />
            </Suspense>
          }
        />
        <Route
          path="models"
          element={
            <Suspense fallback={<AdminFallback />}>
              <ModelList />
            </Suspense>
          }
        />
        <Route
          path="tools"
          element={
            <Suspense fallback={<AdminFallback />}>
              <ToolList />
            </Suspense>
          }
        />
        <Route
          path="mcp-servers"
          element={
            <Suspense fallback={<AdminFallback />}>
              <McpServerList />
            </Suspense>
          }
        />
        <Route
          path="a2a-servers"
          element={
            <Suspense fallback={<AdminFallback />}>
              <A2aServerList />
            </Suspense>
          }
        />
        <Route
          path="a2a-call-logs"
          element={
            <Suspense fallback={<AdminFallback />}>
              <A2aCallLogList />
            </Suspense>
          }
        />
        <Route
          path="templates"
          element={
            <Suspense fallback={<AdminFallback />}>
              <TemplateList />
            </Suspense>
          }
        />
        <Route
          path="skills"
          element={
            <Suspense fallback={<AdminFallback />}>
              <SkillList />
            </Suspense>
          }
        />
        <Route
          path="groups"
          element={
            <Suspense fallback={<AdminFallback />}>
              <GroupList />
            </Suspense>
          }
        />
        <Route
          path="memories"
          element={
            <Suspense fallback={<AdminFallback />}>
              <MemoryList />
            </Suspense>
          }
        />
        <Route
          path="rag-documents"
          element={
            <Suspense fallback={<AdminFallback />}>
              <RagDocumentList />
            </Suspense>
          }
        />
        <Route
          path="embed"
          element={
            <Suspense fallback={<AdminFallback />}>
              <EmbedConfigList />
            </Suspense>
          }
        />
        <Route
          path="sessions"
          element={
            <Suspense fallback={<AdminFallback />}>
              <SessionList />
            </Suspense>
          }
        />
        <Route
          path="audit"
          element={
            <Suspense fallback={<AdminFallback />}>
              <AuditList />
            </Suspense>
          }
        />
        <Route
          path="analytics"
          element={
            <Suspense fallback={<AdminFallback />}>
              <AnalyticsPage />
            </Suspense>
          }
        />
        <Route
          path="settings"
          element={
            <Suspense fallback={<AdminFallback />}>
              <SettingsPage />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  );
}

export default AdminApp;
