// 어데GO 김천 — 알림(웹 푸시)만 다루는 서비스 워커. 화면·자료 캐시는 하지 않음(늘 최신 화면).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "어데GO 김천", {
    body: d.body || "", icon: "icon-192.png", badge: "icon-192.png",
    tag: d.tag || "adego", renotify: true, data: { url: d.url || "./" }
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(ws => {
    for (const w of ws) if (w.url.startsWith(self.registration.scope) && "focus" in w) { if ("navigate" in w) w.navigate(url); return w.focus(); }
    return self.clients.openWindow(url);
  }));
});
