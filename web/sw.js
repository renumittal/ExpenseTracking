/* Service worker: makes the app installable and loads the app files quickly.
   It deliberately does NOT make the app work offline: expenses are only saved on the
   server, so without internet we show a clear "internet chahiye" page instead of
   pretending. Data (API) requests are never cached or intercepted. */
const CACHE = 'expense-shell-v4';
const SHELL = ['./', 'index.html', 'offline.html', 'style.css', 'app.js', 'authz.js', 'config.js', 'manifest.json',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png', 'icons/favicon-32.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);
  // Only our own app files. Never touch the data API, other sites, or anything but GET.
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.includes('/api/')) return;
  const inScope = url.pathname.startsWith(new URL(self.registration.scope).pathname);
  if (!inScope) return;

  if (req.mode === 'navigate') {
    // Always ask the internet (skipping the browser's own saved copy) so people get the newest
    // version; no internet -> clear message.
    e.respondWith(fetch(req.url, { cache: 'no-cache' }).catch(() => caches.match('offline.html')));
    return;
  }
  // App files: newest from the internet, saved copy only so the "no internet" page can look right.
  e.respondWith(fetch(req, { cache: 'no-cache' }).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req)));
});
