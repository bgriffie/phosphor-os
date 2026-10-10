/* Phosphor OS service worker: caches the app shell so it runs offline.
   Bump CACHE whenever any file changes so installed apps pick up the update. */
const PREFIX = 'phosphor-os-';   // every tool shares bgriffie.github.io, so only touch our own caches
const CACHE = PREFIX + 'v12';
const ASSETS = ['./', './index.html', './manifest.json', './apple-touch-icon.png', './icon-192.png', './icon-512.png',
  './apps/arcade/', './apps/workout/', './apps/kitchen/', './apps/lights/'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith(PREFIX) && k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  e.respondWith(
    caches.open(CACHE).then(c => c.match(req, { ignoreSearch: true })).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => req.mode === 'navigate' ? caches.open(CACHE).then(c => c.match('./index.html')) : Response.error()))
  );
});
