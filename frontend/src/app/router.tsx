// =============================================================================
// PH Agent Hub — Router
// =============================================================================
// React Router createBrowserRouter; routes: /login, /chat, /chat/:sessionId,
// /admin/*, /widget (embed), all protected via RouteGuard.
// =============================================================================

import React, { Suspense } from "react";
import { createBrowserRouter } from "react-router-dom";
import { RouteGuard } from "../shared/components/RouteGuard";

const LoginPage = React.lazy(() => import("../features/auth/LoginPage"));
const ChatPage = React.lazy(() => import("../features/chat/routes/ChatPage"));
const BackgroundTasksPage = React.lazy(() =>
  import("../features/chat/routes/BackgroundTasksPage").then((m) => ({
    default: m.BackgroundTasksPage,
  })),
);
const ScheduledTasksPage = React.lazy(() =>
  import("../features/chat/routes/ScheduledTasksPage").then((m) => ({
    default: m.ScheduledTasksPage,
  })),
);
const DemoPage = React.lazy(() =>
  import("../features/chat/routes/DemoPage").then((m) => ({
    default: m.DemoPage,
  })),
);
const WidgetPage = React.lazy(() =>
  import("../features/chat/routes/WidgetPage").then((m) => ({
    default: m.WidgetPage,
  })),
);
const AccountSettingsPage = React.lazy(() =>
  import("../features/account/AccountSettingsPage").then((m) => ({
    default: m.AccountSettingsPage,
  })),
);
const AdminApp = React.lazy(() => import("../features/admin/routes/AdminApp"));

/** Lazy-loading wrapper with Suspense fallback. */
function Lazy({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            height: "100dvh",
            color: "#999",
            fontFamily: "system-ui, sans-serif",
            fontSize: 14,
          }}
        >
          Loading…
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

export const router = createBrowserRouter(
  [
    {
      path: "/login",
      element: (
        <Lazy>
          <LoginPage />
        </Lazy>
      ),
    },
    {
      path: "/widget",
      element: (
        <Lazy>
          <WidgetPage />
        </Lazy>
      ),
    },
    {
      path: "/demo",
      element: (
        <Lazy>
          <DemoPage />
        </Lazy>
      ),
    },
    {
      element: <RouteGuard />,
      children: [
        {
          path: "/chat",
          element: (
            <Lazy>
              <ChatPage />
            </Lazy>
          ),
        },
        {
          path: "/chat/:sessionId",
          element: (
            <Lazy>
              <ChatPage />
            </Lazy>
          ),
        },
        {
          path: "/background-tasks",
          element: (
            <Lazy>
              <BackgroundTasksPage />
            </Lazy>
          ),
        },
        {
          path: "/scheduled-tasks",
          element: (
            <Lazy>
              <ScheduledTasksPage />
            </Lazy>
          ),
        },
        {
          path: "/settings",
          element: (
            <Lazy>
              <AccountSettingsPage />
            </Lazy>
          ),
        },
      ],
    },
    {
      element: <RouteGuard adminOnly />,
      children: [
        {
          path: "/admin/*",
          element: (
            <Lazy>
              <AdminApp />
            </Lazy>
          ),
        },
      ],
    },
    {
      path: "*",
      element: (
        <Lazy>
          <LoginPage />
        </Lazy>
      ),
    },
  ],
  {
    // v7_startTransition is supported at runtime in react-router-dom 6.30.4
    // even though it's missing from the FutureConfig type definitions.
    future: {
      v7_relativeSplatPath: true,
      v7_startTransition: true,
    } as { v7_relativeSplatPath: boolean; v7_startTransition: boolean },
  },
);

export default router;
