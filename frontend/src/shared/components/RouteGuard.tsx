// =============================================================================
// PH Agent Hub — RouteGuard
// =============================================================================
// Wraps React Router Outlet; redirects unauthenticated to /login;
// redirects user role away from /admin/*.
// =============================================================================

import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Spin } from "antd";
import { useAuth } from "../../providers/AuthProvider";

interface RouteGuardProps {
  adminOnly?: boolean;
}

export function RouteGuard({ adminOnly }: RouteGuardProps) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100dvh",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  // Not authenticated → redirect to login
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  // Admin-only routes: only admin and manager roles
  if (adminOnly && user.role === "user") {
    return <Navigate to="/chat" replace />;
  }

  return <Outlet />;
}

export default RouteGuard;
