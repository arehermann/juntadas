// Service worker de Juntadas: guarda la "cáscara" de la app para que abra rápido
// y aunque no haya señal. Los datos siempre vienen de la Raspberry (no se guardan acá).
const CACHE = 'juntadas-v5';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return; // API, GitHub y fuentes: directo a internet
  if (url.pathname.endsWith('/backend.json')) return;                 // dirección de la Pi: siempre fresca
  if (req.mode === 'navigate' || url.pathname.endsWith('/index.html')) {
    // primero internet (para tener siempre la última versión); si no hay, la copia guardada
    e.respondWith(fetch(req).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put('index.html', copy)); return r; })
      .catch(() => caches.match('index.html')));
    return;
  }
  e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
});
