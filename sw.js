/* 江戸川双六 Service Worker
 *
 * 役割は2つだけです。
 *  1) PWAとして「インストール」できるようにする（Chrome系はfetchハンドラを持つSWが必須）
 *  2) アプリ本体（HTML）とアイコンをキャッシュして、2回目以降の起動を速く・
 *     電波が悪い時でもトップ画面までは開けるようにする
 *
 * 地図タイル・OSRM・Firebase など外部APIはキャッシュせず、常にネットワークを使います
 * （古い地図やゲーム状態をキャッシュから返してしまうのを防ぐため）。
 *
 * アプリを更新した時は、下の CACHE_VERSION の数字を1つ上げてアップロードしてください。
 * 次回起動時に古いキャッシュを破棄して新しいHTMLを取り込みます。
 */
const CACHE_VERSION = 'v3';
const CACHE_NAME = `edogawa-sugoroku-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  './sugoroku.html',
  './manifest.json',
  // データは /data/ に、アイコンは /img/ に置いています
  '../data/sugoroku-data.json',
  '../data/edogawa-opendata.json',
  '../data/quiz_data.json',
  '../img/icon-192.png',
  '../img/icon-512.png',
  '../img/icon-maskable-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    // 1つでも失敗するとinstallごと失敗するため、個別にベストエフォートで入れる
    await Promise.all(PRECACHE_URLS.map(async (url) => {
      try { await cache.add(new Request(url, { cache: 'reload' })); } catch (e) {}
    }));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(k => (k.startsWith('edogawa-sugoroku-') && k !== CACHE_NAME) ? caches.delete(k) : null));
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // 外部（地図タイル/API/CDN）は素通し。キャッシュしない。
  if (!sameOrigin) return;

  // HTMLは「ネットワーク優先・失敗したらキャッシュ」。
  // 更新をすぐ反映しつつ、オフラインでも起動できるようにする。
  // JSON（manifest.json・江戸川区オープンデータ）も同じくネットワーク優先にして、
  // 差し替えたデータがすぐ反映されるようにする。
  const isHtml = req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html') ||
    url.pathname.endsWith('.html') ||
    url.pathname.endsWith('.json');

  if (isHtml) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const cache = await caches.open(CACHE_NAME);
        cache.put(req, fresh.clone());
        return fresh;
      } catch (e) {
        const cached = await caches.match(req) ||
          (url.pathname.endsWith('.json') ? null : await caches.match('./sugoroku.html'));
        if (cached) return cached;
        throw e;
      }
    })());
    return;
  }

  // それ以外の同一オリジン資産（アイコン等）は「キャッシュ優先」。
  event.respondWith((async () => {
    const cached = await caches.match(req);
    if (cached) return cached;
    try {
      const fresh = await fetch(req);
      const cache = await caches.open(CACHE_NAME);
      cache.put(req, fresh.clone());
      return fresh;
    } catch (e) {
      throw e;
    }
  })());
});
