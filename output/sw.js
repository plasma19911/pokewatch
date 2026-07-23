// PokeWatch Service Worker
// Huelle wird gecacht, Daten kommen bevorzugt frisch aus dem Netz.
const VERSION = 'pokewatch-v1';
const SHELL = ['./', './index.html', './app.html', './manifest.webmanifest',
               './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL))
              .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION)
                                  .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Periodic Background Sync gibt es nur unter Android/Chrome und nur fuer
// installierte Apps. Das Intervall bestimmt der Browser, nicht wir.
// Geholt wird bloss die fertige feed.json - suchen darf ein Service Worker
// nicht, er steckt in denselben CORS-Grenzen wie die Seite.
async function warmFeed() {
  try {
    const r = await fetch('./feed.json?t=' + Date.now(), {cache: 'reload'});
    if (r.ok) (await caches.open(VERSION)).put('./feed.json', r.clone());
  } catch (e) {}
}

self.addEventListener('periodicsync', e => {
  if (e.tag === 'pokewatch-refresh') e.waitUntil(warmFeed());
});

self.addEventListener('sync', e => {
  if (e.tag === 'pokewatch-refresh') e.waitUntil(warmFeed());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Daten: erst Netz, dann Cache. Sonst liest man auf der Couch von gestern.
  if (url.pathname.endsWith('feed.json')) {
    e.respondWith(
      fetch(e.request).then(r => {
        const copy = r.clone();
        caches.open(VERSION).then(c => c.put('./feed.json', copy));
        return r;
      }).catch(() => caches.match('./feed.json'))
    );
    return;
  }

  // Huelle: erst Cache, damit der Start auch ohne Netz sofort geht
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
