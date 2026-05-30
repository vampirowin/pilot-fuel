var CACHE_STATIC = 'uifuel-static-v1';
var CACHE_CDN = 'uifuel-cdn-v1';

var STATIC_URLS = [
  '/',
  '/static/css/style.css',
  '/static/manifest.json',
  '/static/icons/icon-192.svg',
  '/static/icons/icon-512.svg',
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_STATIC).then(function(cache) {
      return cache.addAll(STATIC_URLS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(names) {
      return Promise.all(
        names.filter(function(n) { return n !== CACHE_STATIC && n !== CACHE_CDN; })
          .map(function(n) { return caches.delete(n); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // CDN resources: stale-while-revalidate
  if (url.hostname === 'unpkg.com' || url.hostname === 'cdn.jsdelivr.net') {
    event.respondWith(
      caches.open(CACHE_CDN).then(function(cache) {
        return cache.match(event.request).then(function(cached) {
          var fetchPromise = fetch(event.request).then(function(response) {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          });
          return cached || fetchPromise;
        });
      })
    );
    return;
  }

  // Static resources: cache-first
  if (STATIC_URLS.includes(url.pathname) || url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_STATIC).then(function(cache) {
        return cache.match(event.request).then(function(cached) {
          if (cached) return cached;
          return fetch(event.request).then(function(response) {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          });
        });
      })
    );
    return;
  }

  // Navigations (HTML pages): network-first
  if (event.request.mode === 'navigate' || (event.request.method === 'GET' && url.pathname !== '/' && !url.pathname.startsWith('/static/') && !url.pathname.startsWith('/api/'))) {
    event.respondWith(
      fetch(event.request).then(function(response) {
        var copy = response.clone();
        caches.open(CACHE_STATIC).then(function(cache) {
          cache.put(event.request, copy);
        });
        return response;
      }).catch(function() {
        return caches.match(event.request).then(function(cached) {
          return cached || caches.match('/');
        });
      })
    );
    return;
  }

  // API and everything else: network only
  return;
});
