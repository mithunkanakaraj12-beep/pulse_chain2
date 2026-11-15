/* PulseChain AI service worker: caches the app shell so the dashboard opens offline.
   API data is cached separately by js/api.js (localStorage) so the page can show its last-synced state. */
const VERSION = 'pulsechain-shell-v1';
const SHELL = [
  '/', '/index.html', '/css/app.css', '/manifest.webmanifest', '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png',
  '/js/main.js', '/js/api.js', '/js/ui.js', '/js/charts.js', '/js/map.js',
  '/js/pages/dashboard.js', '/js/pages/clinics.js', '/js/pages/forecast.js', '/js/pages/inventory.js',
  '/js/pages/redistribution.js', '/js/pages/alerts.js', '/js/pages/demo.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;   // API: network only
  // Shell: network first (so updates arrive), fall back to cache when offline.
  event.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match(req).then((hit) => hit || (req.mode === 'navigate' ? caches.match('/index.html') : Response.error())))
  );
});
