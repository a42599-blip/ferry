/* 轉運站 — Service Worker（PWA／離線）
   策略（2026-09-26 修正）：
   - HTML（導覽）：network-first → 永遠拿到新版
   - CSS／JS：**network-first**（fallback 快取）← 之前用 cache-first 會讓使用者卡在舊版面
   - 圖示／LOGO：cache-first（很少變動）
   - API（/api、/admin）：永不快取
*/
'use strict';

const CACHE = 'ferry-v3';
const PRECACHE = [
  '/style.css', '/app.js', '/transfer.js', '/sha256.js',
  '/icon.svg', '/favicon.ico', '/manifest.webmanifest',
  '/locales/zh-Hant.json', '/locales/zh-Hans.json', '/locales/en.json',
  '/logos/douyin.png', '/logos/bilibili.png', '/logos/xiaohongshu.png',
  '/logos/xigua.png', '/logos/weibo.png', '/logos/toutiao.png',
  '/logos/tiktok.png', '/logos/instagram.png', '/logos/facebook.png',
  '/logos/x.png', '/logos/youtube.png', '/logos/threads.png', '/logos/shopee.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request) {
  try {
    const res = await fetch(request);
    if (res && res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
    }
    return res;
  } catch (err) {
    const hit = await caches.match(request);
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(request) {
  const hit = await caches.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res && res.ok) {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
  }
  return res;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) return;

  if (/\.(png|jpg|jpeg|gif|webp|svg|ico)$/i.test(url.pathname)) {
    event.respondWith(cacheFirst(request));
    return;
  }
  event.respondWith(networkFirst(request));
});
