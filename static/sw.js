/* 轉運站 — Service Worker（極簡）
   2026-09-26 修正：**不再快取 HTML／CSS／JS／JSON**。
   之前用 cache-first 會讓使用者卡在舊版面（修了很多次都還是舊畫面），
   所以在這裡只留一件事：快取「不會變的圖片」（平台 LOGO、icon）。

   HTML／CSS／JS → 直接走網路（伺服器已送 no-cache，一定拿到最新版）
   離線時 → 找不到就讓瀏覽器自己處理（本站沒網路本來就不能用）
*/
'use strict';

const CACHE = 'ferry-img-v49';  // v49：2026-10-04 公測說明依「次數限制／廣告」顯示（訪客3/會員5）
const IMAGES = [
  '/icon.svg', '/favicon.ico',
  '/logos/douyin.png', '/logos/bilibili.png', '/logos/xiaohongshu.png',
  '/logos/xigua.png', '/logos/weibo.png', '/logos/toutiao.png',
  '/logos/tiktok.png', '/logos/instagram.png', '/logos/facebook.png',
  '/logos/x.png', '/logos/youtube.png', '/logos/threads.png', '/logos/shopee.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(IMAGES)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  // 只處理圖片；其餘（HTML/CSS/JS/JSON/API）一律交給網路
  if (!/\.(png|jpg|jpeg|gif|webp|svg|ico)$/i.test(url.pathname)) return;
  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((res) => {
      if (res && res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
      }
      return res;
    }))
  );
});
