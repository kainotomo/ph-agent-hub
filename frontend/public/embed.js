// =============================================================================
// PH Agent Hub — Embed Loader Script
// =============================================================================
// Lightweight vanilla JS (< 3 KB) that users paste on their website.
// Supports floating bubble mode and inline mode.
//
// Usage:
//   <script src="https://your-domain.com/embed.js"
//           data-ph-token="embed_xxxx"
//           data-ph-position="bubble"
//           data-ph-api-url="https://your-domain.com/api"
//   ></script>
// =============================================================================

(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var token = script.getAttribute("data-ph-token");
  var position = script.getAttribute("data-ph-position") || "bubble";
  var apiUrl = script.getAttribute("data-ph-api-url") || "/api";
  var baseUrl = apiUrl.replace(/\/api\/?$/, ""); // derive app base URL

  if (!token) {
    console.error("[PH Widget] Missing data-ph-token attribute");
    return;
  }

  var iframeId = "ph-widget-iframe-" + Math.random().toString(36).slice(2, 8);

  // ---- Create iframe element ----
  function createIframe() {
    var iframe = document.createElement("iframe");
    iframe.id = iframeId;
    iframe.src = baseUrl + "/widget?token=" + encodeURIComponent(token);
    iframe.style.border = "none";
    iframe.style.width = "100%";
    iframe.style.height = "0";
    iframe.style.overflow = "hidden";
    iframe.title = "PH Agent Chat";
    iframe.setAttribute("allow", "clipboard-write");
    return iframe;
  }

  // ---- Floating bubble mode ----
  function initBubble() {
    // Bubble button
    var bubble = document.createElement("button");
    bubble.id = iframeId + "-bubble";
    bubble.innerHTML =
      '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
    bubble.style.position = "fixed";
    bubble.style.bottom = "24px";
    bubble.style.right = "24px";
    bubble.style.width = "56px";
    bubble.style.height = "56px";
    bubble.style.borderRadius = "50%";
    bubble.style.background = "#1677ff";
    bubble.style.color = "white";
    bubble.style.border = "none";
    bubble.style.cursor = "pointer";
    bubble.style.boxShadow = "0 4px 12px rgba(0,0,0,0.2)";
    bubble.style.zIndex = "2147483646";
    bubble.style.display = "flex";
    bubble.style.alignItems = "center";
    bubble.style.justifyContent = "center";
    bubble.style.transition = "transform 0.2s";
    bubble.setAttribute("aria-label", "Open chat");
    document.body.appendChild(bubble);

    // Drawer container
    var drawer = document.createElement("div");
    drawer.id = iframeId + "-drawer";
    drawer.style.position = "fixed";
    drawer.style.bottom = "96px";
    drawer.style.right = "24px";
    drawer.style.width = "380px";
    drawer.style.maxWidth = "calc(100vw - 48px)";
    drawer.style.height = "600px";
    drawer.style.maxHeight = "calc(100vh - 120px)";
    drawer.style.borderRadius = "12px";
    drawer.style.overflow = "hidden";
    drawer.style.boxShadow = "0 8px 32px rgba(0,0,0,0.2)";
    drawer.style.zIndex = "2147483646";
    drawer.style.display = "none";
    drawer.style.background = "white";
    document.body.appendChild(drawer);

    var iframe = createIframe();
    iframe.style.height = "100%";
    iframe.style.borderRadius = "12px";
    drawer.appendChild(iframe);

    var isOpen = false;

    bubble.addEventListener("click", function () {
      isOpen = !isOpen;
      drawer.style.display = isOpen ? "block" : "none";
      bubble.innerHTML = isOpen
        ? '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>'
        : '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
    });

    // Listen for resize events from iframe
    window.addEventListener("message", function (event) {
      if (event.data?.type === "widget:resize") {
        // Resize not used in bubble mode (fixed drawer size)
      }
      if (event.data?.type === "widget:close") {
        isOpen = false;
        drawer.style.display = "none";
        bubble.innerHTML =
          '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>';
      }
    });
  }

  // ---- Inline mode ----
  function initInline() {
    var container = document.createElement("div");
    container.id = iframeId + "-container";
    container.style.width = "100%";
    container.style.height = "600px";
    container.style.maxHeight = "80vh";
    container.style.overflow = "hidden";
    container.style.borderRadius = "8px";
    container.style.border = "1px solid #e5e7eb";

    var iframe = createIframe();
    iframe.style.height = "100%";
    iframe.style.borderRadius = "8px";
    container.appendChild(iframe);

    script.parentNode?.insertBefore(container, script.nextSibling);

    // Listen for resize events from iframe
    window.addEventListener("message", function (event) {
      if (event.data?.type === "widget:resize") {
        var h = Math.min(event.data.height, window.innerHeight * 0.8);
        container.style.height = h + "px";
        iframe.style.height = h + "px";
      }
    });
  }

  // ---- Init based on position ----
  if (position === "inline") {
    initInline();
  } else {
    initBubble();
  }
})();
