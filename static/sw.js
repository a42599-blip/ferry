/* 轉運站 — 極簡 Service Worker（PWA／離線）
   策略：
   - 靜態資源（css/js/svg/manifest）：cache-first（快，且不影響功能）
   - 導覽（HTML）：network-first，失敗才回快取（避免拿到舊版）
   - API（/api、/admin）：永不快取（一律走網路，資料必須即時） */
'use strict';

const CACHE = 'ferry-v2';
const STATIC_ASSETS = [
  '/style.css', '/app.js', '/transfer.js', '/sha256.js',
  '/icon.svg', '/favicon.ico', '/manifest.webmanifest',
  '/locales/zh-Hant.json', '/locales/zh-Hans.json', '/locales/en.json',
  '/logos/douyin.png', '/logos/bilibili.png', '/logos/xiaohongshu.png',
  '/logos/xigua.png', '/logos/weibo.png', '/logos/toutiao.png',
  '/logos/tiktok.png', '/logos/instagram.png', '/logos/facebook.png',
  '/logos/x.png', '/logos/youtube.png', '/logos/threads.png', '/logos/shopee.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATIC_ASSETS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) return;

  const isStatic = /\.(css|js|svg|ico|webmanifest|json)$/.test(url.pathname);
  if (isStatic) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
        return res;
      }))
    );
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() => caches.match('/') || caches.match(request))
    );
  }
});
