// Minimal service worker — enables PWA install prompt, no offline cache
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
