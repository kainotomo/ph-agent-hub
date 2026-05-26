// =============================================================================
// PH Agent Hub — Router
// =============================================================================
// React Router createBrowserRouter; routes: /login, /chat, /chat/:sessionId,
// /admin/*, /widget (embed), all protected via RouteGuard.
// =============================================================================

import { createBrowserRouter } from "react-router-dom";
import { RouteGuard } from "../shared/components/RouteGuard";
import LoginPage from "../features/auth/LoginPage";
import ChatPage from "../features/chat/routes/ChatPage";
import { DemoPage } from "../features/chat/routes/DemoPage";
import { WidgetPage } from "../features/chat/routes/WidgetPage";
import AdminApp from "../features/admin/routes/AdminApp";

export const router = createBrowserRouter([
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/widget",
    element: <WidgetPage />,
  },
  {
    path: "/demo",
    element: <DemoPage />,
  },
  {
    element: <RouteGuard />,
    children: [
      {
        path: "/chat",
        element: <ChatPage />,
      },
      {
        path: "/chat/:sessionId",
        element: <ChatPage />,
      },
    ],
  },
  {
    element: <RouteGuard adminOnly />,
    children: [
      {
        path: "/admin/*",
        element: <AdminApp />,
      },
    ],
  },
  {
    path: "*",
    element: <LoginPage />,
  },
]);

export default router;
