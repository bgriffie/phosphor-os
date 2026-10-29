/* Phosphor OS service worker: caches the app shell so it runs offline.
   Bump CACHE whenever any file changes so installed apps pick up the update. */
const PREFIX = 'phosphor-os-';   // every tool shares bgriffie.github.io, so only touch our own caches
const CACHE = PREFIX + 'v90';
const ASSETS = ['./', './index.html', './manifest.json', './apple-touch-icon.png', './icon-192.png', './icon-512.png',
  './apps/arcade/', './apps/workout/', './apps/kitchen/', './apps/lights/', './apps/pc/', './apps/tv/', './apps/watch/', './apps/home/', './apps/chores/'];

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

/* Chore alerts (from the relay's chores.py): show each push on the lock screen; a tap opens Chores.
   iOS stops sending pushes to an app that receives one without showing a notification, so always show one. */
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Chores', {
    body: d.body || 'A chore is due.', tag: d.tag || 'chores', icon: 'icon-192.png', badge: 'icon-192.png', data: { url: d.url || './' },
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL(e.notification.data && e.notification.data.url || './', self.registration.scope).href;
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const w = list.find(c => c.url.startsWith(self.registration.scope));
    return w ? w.focus().then(() => w.navigate(url)).catch(() => clients.openWindow(url)) : clients.openWindow(url);
  }));
});
