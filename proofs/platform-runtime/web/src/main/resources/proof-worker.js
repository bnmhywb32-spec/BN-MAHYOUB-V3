const cacheName = "bn-v3-kotlin-offline-proof-v2";
const assets = ["./", "./proof-store.js", "./proof.js", "./sql-wasm.wasm"]
    .map(path => new URL(path, self.registration.scope).href);

self.addEventListener("install", event => {
    event.waitUntil(caches.open(cacheName).then(cache => cache.addAll(assets))
        .then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", event => {
    const request = event.request;
    if (request.method !== "GET" || !assets.includes(request.url)) return;
    event.respondWith(fetch(request).catch(async error => {
        const response = await (await caches.open(cacheName)).match(request);
        if (!response) throw error;
        return response;
    }));
});
