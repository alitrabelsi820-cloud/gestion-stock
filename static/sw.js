// ═══════════════════════════════════════════════════════════
//  Service Worker — Trabelsi
//  Réseau uniquement (jamais de cache bloquant pour HTML/CSS/JS)
//  → les mises à jour sont TOUJOURS visibles immédiatement.
//  Un petit cache sert uniquement de secours hors-ligne.
// ═══════════════════════════════════════════════════════════
const CACHE_NAME = 'trabelsi-v17';

// Installation : prendre le contrôle immédiatement
self.addEventListener('install', event => {
  self.skipWaiting();
});

// Activation : supprimer TOUS les anciens caches + prendre le contrôle
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Fetch : toujours le réseau. Cache utilisé seulement si hors-ligne.
self.addEventListener('fetch', event => {
  const req = event.request;

  // Ne jamais intercepter les API (toujours réseau direct)
  if (req.url.includes('/api/')) return;

  // Pour la navigation + CSS/JS : réseau d'abord, secours offline
  event.respondWith(
    fetch(req)
      .then(resp => {
        // Garder une copie de secours pour le mode hors-ligne
        if (resp && resp.status === 200 && req.method === 'GET') {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(c => c.put(req, clone)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(req))   // hors-ligne uniquement
  );
});
