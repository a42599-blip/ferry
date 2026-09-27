/* 轉運站 — 跨平台存檔（iOS 相簿 / Android 下載 / 桌機）
 *
 * 為什麼要單獨一支：
 *   各系統「存到相簿」的方式完全不同，而且 v8i8 就踩過「蘋果好用、安卓困難」的坑。
 *   這裡把判斷集中在一個地方，其他模組只呼叫 `saveBlob()`／`saveUrl()`。
 *
 * 策略：
 *   電腦（Win/Mac/Linux） → <a download>（瀏覽器自己問路徑）
 *   Android 一般瀏覽器    → <a download>（存到「下載」）
 *   Android 內建瀏覽器    → 降級走 iOS 流程（WebView 的 download 屬性常失效）
 *   iOS（Safari/Chrome）  → Web Share API → 系統選單 →「儲存到照片」
 *                            不支援時 → 開新頁面，請使用者按分享圖示儲存
 */
'use strict';

const FYEnv = (() => {
  const ua = navigator.userAgent || '';
  const isIOS = /iPad|iPhone|iPod/.test(ua)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isAndroid = /Android/i.test(ua);
  // 微信／LINE／FB／IG／TikTok 等 App 內建瀏覽器：存檔常會失敗
  const inApp = /Line\/|FBAN|FB_IAB|FBAV|Instagram|MicroMessenger|KAKAOTALK|Twitter|SnapChat/i.test(ua);
  const isSafari = /Safari/.test(ua) && !/Chrome|CriOS|Edg|OPR/.test(ua);
  return {
    ua, isIOS, isAndroid, inApp, isSafari,
    isDesktop: !isIOS && !isAndroid,
    touch: (navigator.maxTouchPoints || 0) > 0,
  };
})();

/** 這個平台的「存到相簿」按鈕該寫什麼（各系統用語不同） */
function saveLabel() {
  if (FYEnv.isIOS) return '儲存到照片';
  if (FYEnv.isAndroid || FYEnv.inApp) return '儲存影片 / 下載';
  return '儲存到這台裝置';
}

let _wakeLock = null;
async function _keepAwake(on) {
  try {
    if (on) {
      _wakeLock = await navigator.wakeLock?.request('screen');
    } else {
      _wakeLock?.release();
      _wakeLock = null;
    }
  } catch { /* 不支援就算了 */ }
}

/** 用 <a download> 存檔（桌機／Android 一般瀏覽器） */
function _anchorDownload(blobOrUrl, filename, { revoke = false } = {}) {
  const url = typeof blobOrUrl === 'string' ? blobOrUrl : URL.createObjectURL(blobOrUrl);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noreferrer';
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  if (revoke || typeof blobOrUrl !== 'string') {
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  return true;
}

/**
 * iOS 專用：顯示「儲存到相簿」按鈕，讓使用者自己按。
 *
 * ⚠️ 為什麼要這樣（2026-09-27 實際踩到，小羅說「相簿裡找不到」）：
 *    iOS 的 navigator.share() 需要「使用者手勢」才有效。
 *    我們的流程是「先下載檔案（FB 這種大檔可能幾十秒）→ 再呼叫 share()」，
 *    等到要分享時手勢早就過期了 → iOS 直接拒絕 → 相簿當然沒有東西。
 *    （TikTok 那類小檔跑得快，手勢還沒過期，所以看起來正常）
 *  → 改成下載完顯示一顆按鈕；使用者按下去才是「有效手勢」，分享才會成功。
 */
function showSaveButton(file, filename, onStatus) {
  const box = document.createElement('div');
  box.className = 'savepop';
  box.innerHTML =
    `<div class="sp-t">檔案已下載完成</div>`
    + `<button class="sp-b" type="button">📥 ${_escHtml(saveLabel())}</button>`
    + `<button class="sp-x" type="button">稍後再說</button>`;
  document.body.appendChild(box);
  const close = () => box.remove();
  box.querySelector('.sp-b').addEventListener('click', async () => {
    // 這個點擊就是「使用者手勢」→ share 這下才會成功
    try {
      await navigator.share({ files: [file], title: filename });
      if (onStatus) onStatus('已送出，請確認相簿是否出現', 'ok');
      close();
    } catch (err) {
      if (err && err.name === 'AbortError') { if (onStatus) onStatus('已取消，可再按一次', 'warn'); close(); return; }
      // 分享被拒 → 退回「開新頁面自己存」
      const url = URL.createObjectURL(file);
      const w = window.open(url, '_blank');
      if (onStatus) onStatus(w ? '在新頁面點底部「分享」→「儲存到照片」' : '請允許彈出視窗', 'warn');
      setTimeout(() => URL.revokeObjectURL(url), 120000);
      close();
    }
  });
  box.querySelector('.sp-x').addEventListener('click', close);
  if (onStatus) onStatus('檔案下載完成，請按「📥 儲存到相簿」', 'ok');
}

function _escHtml(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * 存一個 Blob（已抓到記憶體的檔案）。
 * @returns {Promise<{ok:boolean, how:string}>}
 */
async function saveBlob(blob, filename, onStatus) {
  const isMedia = /\.(mp4|mov|m4v|webm|mkv|jpe?g|png|gif|webp)$/i.test(filename);
  const file = new File([blob], filename, { type: blob.type || 'application/octet-stream' });

  // ① iOS／App 內建瀏覽器：改成「顯示按鈕讓使用者自己按」
  //    （直接在這裡呼叫 share 會因為手勢過期而失敗 → 相簿不會有東西）
  if (FYEnv.isIOS || FYEnv.inApp) {
    showSaveButton(file, filename, onStatus);
    return { ok: true, how: 'button' };
    // ② 不支援分享 → 開新頁面讓使用者自己儲存
    const url = URL.createObjectURL(blob);
    const w = window.open(url, '_blank');
    if (onStatus) {
      onStatus(w ? '在新頁面點底部「分享」→「儲存到照片」'
                 : '請允許彈出視窗，或在瀏覽器開啟本頁', 'warn');
    }
    setTimeout(() => URL.revokeObjectURL(url), 120000);
    return { ok: true, how: 'newtab' };
  }

  // ③ Android／桌機：直接下載（瀏覽器會存到「下載」或問路徑）
  _anchorDownload(blob, filename);
  if (onStatus) {
    onStatus(isMedia ? '已開始儲存，請到相簿或下載項目查看' : '已開始下載', 'ok');
  }
  return { ok: true, how: 'download' };
}

/**
 * 存一個網址（CDN 不給 CORS，無法先抓成 blob 時）。
 * iOS 會開新頁面；其他平台用 <a download>。
 */
async function saveUrl(url, filename, onStatus) {
  if (FYEnv.isIOS || FYEnv.inApp) {
    window.open(url, '_blank');
    if (onStatus) onStatus('影片已開啟，請長按畫面選「儲存到照片」', 'warn');
    return { ok: true, how: 'newtab' };
  }
  _anchorDownload(url, filename, { revoke: false });
  if (onStatus) onStatus('已開始下載', 'ok');
  return { ok: true, how: 'download' };
}

/** 抓遠端檔案並附進度（回傳 Blob） */
async function fetchWithProgress(url, { headers = {}, onProgress } = {}) {
  const resp = await fetch(url, { headers });
  if (!resp.ok) throw new Error('伺服器回應 ' + resp.status);
  const total = Number(resp.headers.get('content-length')) || 0;
  if (!resp.body) return await resp.blob();

  const reader = resp.body.getReader();
  const chunks = [];
  let got = 0;
  const t0 = performance.now();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    if (onProgress) {
      const sec = (performance.now() - t0) / 1000;
      onProgress({
        got, total,
        pct: total ? Math.min(99, Math.round(got / total * 100)) : null,
        speed: sec > 0.6 ? got / sec : 0,
      });
    }
  }
  return new Blob(chunks, { type: resp.headers.get('content-type') || 'video/mp4' });
}

/* App 內建瀏覽器提醒（微信／LINE 開連結常常無法存檔） */
function inAppNotice() {
  if (!FYEnv.inApp) return null;
  return {
    text: '你目前是用 App 內建瀏覽器開啟，可能無法存到相簿。請點右上角「⋯」→「用瀏覽器開啟」再下載。',
    copy: location.href,
  };
}

window.FY = Object.assign(window.FY || {}, {
  env: FYEnv, saveBlob, saveUrl, fetchWithProgress, saveLabel, inAppNotice, keepAwake: _keepAwake,
});
