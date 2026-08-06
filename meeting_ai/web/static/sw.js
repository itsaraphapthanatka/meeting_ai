/* service worker — แคชเฉพาะเปลือกแอปให้เปิดจากมือถือได้เร็ว
   ข้อมูลประชุมและเสียง "ห้ามแคช" เพราะเป็นเนื้อหาส่วนตัวและเปลี่ยนตลอด */

const SHELL = 'mai-shell-v1';
const SHELL_FILES = ['/', '/static/app.js', '/static/style.css', '/static/icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  const bypass = e.request.method !== 'GET'
    || url.origin !== self.location.origin
    || url.pathname.startsWith('/api/')
    || url.pathname.startsWith('/s/');
  if (bypass) return;

  // network-first: ออนไลน์ได้ของใหม่เสมอ ออฟไลน์ค่อยตกไปใช้แคช
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match('/')))
  );
});
