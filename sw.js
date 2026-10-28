const CACHE = 'dafeiyu-shell-v1'
const SHELL = ['/', '/index.html', '/manifest.webmanifest']
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)))
  self.skipWaiting()
})
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return
  const url = new URL(event.request.url)
  // Never cache API/auth responses: they can include private state or credentials.
  if (url.origin !== self.location.origin || url.pathname.startsWith('/api') || url.pathname.startsWith('/auth')) return
  if (!['document', 'script', 'style', 'image', 'font'].includes(event.request.destination)) return
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
    const copy = response.clone()
    caches.open(CACHE).then((cache) => cache.put(event.request, copy))
    return response
  }).catch(() => caches.match('/index.html'))))
})
