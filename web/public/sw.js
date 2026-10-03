/* NetMonitor service worker — push notifications ONLY.
 *
 * Deliberately does NO caching / fetch interception: the dashboard deploys
 * every few minutes and a caching SW would serve stale builds. Its only job
 * is showing pushes and focusing/opening the app when one is tapped. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "NetMonitor", body: event.data && event.data.text() };
  }
  const title = data.title || "NetMonitor";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      tag: data.tag || undefined, // same tag replaces (no pile-up per device)
      renotify: !!data.tag,       // ...but a replacement still buzzes
      requireInteraction: !!data.require_interaction, // feed-switch outages stay up
      vibrate: data.priority === "critical" ? [300, 120, 300, 120, 300] : [200],
      timestamp: Date.now(),
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ("focus" in w) {
          w.navigate(url);
          return w.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
