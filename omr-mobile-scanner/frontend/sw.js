const CACHE='omr-ui-v14';
const ROOT=new URL('./',self.registration.scope).pathname.replace(/\/$/,'');
const appUrl=path=>`${ROOT}${path}`||'/';
const ASSETS=['/','/assets/styles.css?v=14','/assets/app.js?v=14','/assets/manifest.webmanifest','/assets/icon.svg'].map(appUrl);
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{if(event.request.method==='GET')event.respondWith(fetch(event.request).then(response=>{const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy));return response}).catch(()=>caches.match(event.request)))})
