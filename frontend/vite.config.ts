import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icons/icon-192.png", "icons/icon-512.png", "logo.svg"],
      workbox: {
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024, // 4 MB — our main bundle is ~2.2 MB
        runtimeCaching: [
          {
            urlPattern: /\/api\//,
            handler: "NetworkOnly",
            method: "POST",
          },
        ],
        navigateFallbackDenylist: [
          /\/api\//,
          /\.json$/,
          /sw\.js$/,
          /registerSW\.js$/,
        ],
      },
      manifest: {
        name: "PH Agent Hub",
        short_name: "PH Agent",
        description: "AI Agent Hub for ERPNext and Business Operations",
        theme_color: "#1677ff",
        background_color: "#ffffff",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any maskable",
          },
          {
            src: "icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },
      // Dev mode: registration is inlined in index.html to avoid Vite's
      // SPA fallback returning HTML for /registerSW.js (Issue #263).
      devOptions: {
        enabled: false,
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-antd": ["antd", "@ant-design/icons", "@ant-design/cssinjs"],
          "vendor-refine": ["@refinedev/core", "@refinedev/antd", "@refinedev/react-router-v6"],
          "vendor-markdown": ["react-markdown", "remark-gfm", "react-syntax-highlighter"],
          "vendor-codemirror": ["@uiw/react-codemirror", "@uiw/codemirror-theme-dracula", "@codemirror/lang-python"],
          "vendor-virtuoso": ["react-virtuoso"],
          "vendor-core": ["react-router-dom", "@tanstack/react-query"],
          "vendor-utils": ["@microsoft/fetch-event-source"],
        },
      },
    },
  },
  server: {
    port: 3000,
  },
});
