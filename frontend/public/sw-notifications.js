/* global self, clients */
// =============================================================================
// PH Agent Hub — notification click handler (Issue #619)
// =============================================================================
// Loaded into the generated Workbox service worker through
// `workbox.importScripts` (see vite.config.ts).  Notifications are shown by
// the page with `registration.showNotification`, so the click event surfaces
// here rather than in the tab that created them.
// =============================================================================

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const data = event.notification.data || {};
  const target = data.url || "/chat";

  event.waitUntil(
    (async () => {
      const absolute = new URL(target, self.location.origin).href;
      const clientList = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });

      // Prefer the tab that is already open: focusing it (and navigating it
      // to the session) beats opening a second copy of the app.
      const appClient = clientList.find((client) =>
        client.url.startsWith(self.location.origin),
      );
      if (appClient) {
        if ("navigate" in appClient && appClient.url !== absolute) {
          try {
            await appClient.navigate(absolute);
          } catch {
            // A client that cannot navigate is still worth focusing.
          }
        }
        await appClient.focus();
        return;
      }

      const opened = await self.clients.openWindow(absolute);
      if (opened) await opened.focus();
    })(),
  );
});
