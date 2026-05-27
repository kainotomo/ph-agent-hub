import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import path from "path";
import fs from "fs";

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
      devOptions: {
        enabled: true,
      },
    }),
    // Serve PWA dev files from dev-dist/ (Vite doesn't auto-serve them)
    {
      name: "serve-dev-dist",
      configureServer(server) {
        const devDist = path.resolve(__dirname, "dev-dist");
        server.middlewares.use((req, res, next) => {
          const url = req.url || "";
          // Only handle known PWA dev files
          if (url === "/registerSW.js" || url === "/sw.js" || url.startsWith("/workbox-")) {
            const filePath = path.join(devDist, url.replace(/^\//, ""));
            if (fs.existsSync(filePath)) {
              res.setHeader("Content-Type", url.endsWith(".js") ? "application/javascript" : "text/javascript");
              res.setHeader("Cache-Control", "no-cache");
              res.end(fs.readFileSync(filePath, "utf-8"));
              return;
            }
          }
          next();
        });
      },
    },
  ],
  server: {
    port: 3000,
  },
});
