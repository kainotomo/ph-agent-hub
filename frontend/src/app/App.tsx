// =============================================================================
// PH Agent Hub — App Root Component
// =============================================================================
// Wraps QueryProvider > AuthProvider > TenantProvider > RouterProvider.
// =============================================================================

import { RouterProvider } from "react-router-dom";
import { ConfigProvider, App as AntApp } from "antd";
import { QueryProvider } from "../providers/QueryProvider";
import { AuthProvider } from "../providers/AuthProvider";
import { TenantProvider } from "../providers/TenantProvider";
import { StreamingProvider } from "../providers/StreamingProvider";
import { router } from "./router";

function App() {
  return (
    <ConfigProvider
      theme={{
        cssVar: true,
        token: {
          colorPrimary: "#1677ff",
          borderRadius: 6,
        },
      }}
    >
      <AntApp>
        <QueryProvider>
          <AuthProvider>
            <TenantProvider>
              <StreamingProvider>
                <RouterProvider
                  router={router}
                  future={{ v7_startTransition: true }}
                />
              </StreamingProvider>
            </TenantProvider>
          </AuthProvider>
        </QueryProvider>
      </AntApp>
    </ConfigProvider>
  );
}

export default App;
