/* 轉運站 — 前台（原生 JS，無框架）
   ⚠️ 所有顯示文字一律走 t()，翻譯要含「按鈕、placeholder、動態訊息」。 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  config: null, info: null, selected: null,
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  lang: 'zh-Hant', L: {},
};

// ── i18n（全域共用，transfer.js 也會用）───────────────
function t(key, fallback) {
  const v = state.L[key];
  if (v === undefined || v === null) return fallback !== undefined ? fallback : key;
  return v;                    // 空字串是「該語言不需要這個字」，不是缺翻譯
}
// ⚠️ 用 Object.assign（不能用 = 覆蓋，否則會蓋掉 save.js 的存檔工具）
window.FY = Object.assign(window.FY || {}, { t: (k, d) => t(k, d), lang: () => state.lang });

function applyLang() {
  // ⚠️ 不能用 if(v)：空的翻譯（例：英文的「次」不需要）也必須套用，否則會殘留中文
  $$('[data-i18n]').forEach((el) => {
    const key = el.dataset.i18n;
    const v = t(key, el.dataset.zh || el.textContent);
    if (v !== undefined && v !== null) el.textContent = v;
  });
  $$('[data-i18n-ph]').forEach((el) => {
    const v = t(el.dataset.i18nPh);
    if (v !== undefined && v !== null) el.placeholder = v;
  });
  $$('#lang button').forEach((b) => b.classList.toggle('on', b.dataset.lang === state.lang));
  $$('#lang button').forEach((b) => {
    // 語言鈕用「各語言自己的寫法」：繁中版顯示「簡」、簡中版顯示「简」、英文版用 CHT/CHS
    const NAME = {
      'zh-Hant': { 'zh-Hant': '繁', 'zh-Hans': '簡', en: 'EN' },
      'zh-Hans': { 'zh-Hant': '繁', 'zh-Hans': '简', en: 'EN' },
      en: { 'zh-Hant': 'CHT', 'zh-Hans': 'CHS', en: 'EN' },
    };
    const name = NAME[state.lang][b.dataset.lang];
    b.textContent = name;
    b.title = { 'zh-Hant': '繁體中文', 'zh-Hans': '简体中文', en: 'English' }[b.dataset.lang];
  });
  document.documentElement.lang = state.lang;
  document.title = t('brand', '轉運站') + ' · ' + t('dl_title');
  buildTeach();
  buildPlans();
  renderPlatforms(state.config?.platforms, state.config?.enabled_platform_count);
  renderQuota(state.quota);
  renderHistory();
  // ⚠️ 動態訊息不會被 data-i18n 涵蓋 → 語言切換時必須重新產生
  renderPayStatus();
  $('.msg:not([hidden])') && clearTransient();
  if (state.info) renderResult(state.info);
  if (currentTab() === 'member') refreshMember();
  updateCtx();
}

async function loadLang(code) {
  state.lang = code || localStorage.getItem('fy_lang') || 'zh-Hant';
  try {
    const r = await fetch('/locales/' + state.lang + '.json');
    state.L = await r.json();
  } catch { state.L = {}; }
  localStorage.setItem('fy_lang', state.lang);
  applyLang();
}
$$('#lang button').forEach((b) => b.addEventListener('click', () => loadLang(b.dataset.lang)));

// ── 平台（分組＋圖示；只顯示「開著」的）────────────────
const PLATFORMS = [
  { id: 'douyin', key: 'plat_douyin', zh: '抖音', logo: '/logos/douyin.png', g: 'cn' },
  { id: 'bilibili', key: 'plat_bilibili', zh: 'B站', logo: '/logos/bilibili.png', g: 'cn' },
  { id: 'xiaohongshu', key: 'plat_xiaohongshu', zh: '小紅書', logo: '/logos/xiaohongshu.png', g: 'cn' },
  { id: 'xigua', key: 'plat_xigua', zh: '西瓜視頻', logo: '/logos/xigua.png', g: 'cn' },
  { id: 'weibo', key: 'plat_weibo', zh: '微博', logo: '/logos/weibo.png', g: 'cn' },
  { id: 'toutiao', key: 'plat_toutiao', zh: '今日頭條', logo: '/logos/toutiao.png', g: 'cn' },
  { id: 'tiktok', key: 'plat_tiktok', zh: 'TikTok', logo: '/logos/tiktok.png', g: 'intl' },
  { id: 'instagram', key: 'plat_instagram', zh: 'Instagram', logo: '/logos/instagram.png', g: 'intl' },
  { id: 'facebook', key: 'plat_facebook', zh: 'Facebook', logo: '/logos/facebook.png', g: 'intl' },
  { id: 'x', key: 'plat_x', zh: 'X', logo: '/logos/x.png', g: 'intl' },
  { id: 'youtube', key: 'plat_youtube', zh: 'YouTube', logo: '/logos/youtube.png', g: 'intl' },
  { id: 'threads', key: 'plat_threads', zh: '脆 Threads', logo: '/logos/threads.png', g: 'intl' },
  { id: 'shopee', key: 'plat_shopee', zh: '蝦皮', logo: '/logos/shopee.png', g: 'intl' },
];
const GROUPS = [{ key: 'cn', label: 'grp_cn' }, { key: 'intl', label: 'grp_intl' }];

function renderPlatforms(plats, count) {
  const on = plats || {};
  const enabled = PLATFORMS.filter((p) => on[p.id]?.enabled !== false);
  const label = t('supported', '支援') + ' ' + (count ?? enabled.length) + ' ' + t('supported_suffix', '個平台');
  $('#plat-label').textContent = label;
  const teachLabel = $('#teach-plat-label');
  if (teachLabel) teachLabel.textContent = label;

  if (!enabled.length) {
    const none = `<div class="pledge">${t('platforms_none')}</div>`;
    $('#plats').innerHTML = none;
    if ($('#plats-teach')) $('#plats-teach').innerHTML = none;
    return;
  }
  const html = GROUPS.map((g) => {
    const list = enabled.filter((p) => p.g === g.key);
    if (!list.length) return '';
    return `<div class="pgrp"><div class="pgh">${t(g.label)}</div><div class="plist">${
      list.map((p) => `<span class="pi" title="${p.zh}"><img src="${p.logo}" alt="${p.zh}" loading="lazy"><i>${t(p.key, p.zh)}</i></span>`).join('')
    }</div></div>`;
  }).join('');
  $('#plats').innerHTML = html;
  if ($('#plats-teach')) $('#plats-teach').innerHTML = html;
}

// ── 教學 / 方案（內容來自語系檔）───────────────────────
function buildTeach() {
  const dl = t('teach_dl_steps', []);
  const tr = t('teach_tr_steps', []);
  const osl = t('teach_os_list', []);
  const faq = t('teach_faq', []);
  const steps = (arr) => (arr || []).map((s) => `<li>${s}</li>`).join('');
  if ($('#teach-steps-dl')) $('#teach-steps-dl').innerHTML = steps(dl);
  if ($('#teach-steps-tr')) $('#teach-steps-tr').innerHTML = steps(tr);
  const svl = t('teach_save_list', []);
  if ($('#teach-save-list')) $('#teach-save-list').innerHTML = (svl || []).map(
    ([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  if ($('#teach-save-note')) $('#teach-save-note').textContent = t('teach_save_note', '');
  if ($('#teach-os-list')) $('#teach-os-list').innerHTML = (osl || []).map(
    ([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  if ($('#teach-faq')) $('#teach-faq').innerHTML = (faq || []).map(
    ([q, a]) => `<div class="r"><div class="k">${q}</div><div class="v">${a}</div></div>`).join('');
}
function buildPlans() {
  const n = { dl: state.config?.quota?.download_per_day ?? 5, tr: state.config?.quota?.transfer_per_day ?? 5 };
  const fill = (arr) => (arr || []).map((s) => `<li>${String(s).replace('{dl}', n.dl).replace('{tr}', n.tr)}</li>`).join('');
  if ($('#plan-free-list')) $('#plan-free-list').innerHTML = fill(t('plan_free_list', []));
  if ($('#plan-monthly-list')) $('#plan-monthly-list').innerHTML = fill(t('plan_monthly_list', []));
  if ($('#plan-lifetime-list')) $('#plan-lifetime-list').innerHTML = fill(t('plan_lifetime_list', []));
}

// ── 開關連動：關掉的功能，前台整個消失 ─────────────────
const FEATURE_TAB = {
  download: 'feature.download',   // ← 關掉＝整個下載模組消失
  transfer: 'feature.transfer',
  teach: 'feature.teach',
  plans: 'feature.plans',
  member: 'feature.member',
};
const AVAILABLE_TABS = ['download', 'transfer', 'teach', 'plans', 'member'];

function applyFlags(cfg) {
  const f = cfg.features || {};
  // 分頁／面板
  AVAILABLE_TABS.forEach((tab) => {
    const need = FEATURE_TAB[tab];
    const ok = !need || f[need] !== false;
    $$(`[data-tab="${tab}"]`).forEach((b) => { b.hidden = !ok; });
    const panel = $('#p-' + tab);
    if (panel) panel.dataset.disabled = ok ? '' : '1';
  });
  // 面板內容區塊
  if ($('#report-box')) $('#report-box').hidden = f['feature.report'] === false;
  if ($('#history-box')) $('#history-box').hidden = f['feature.history'] === false;
  if ($('#go')) $('#go').disabled = f['feature.maintenance'] === true;
  if ($('#url')) $('#url').disabled = f['feature.maintenance'] === true;

  if (f['feature.maintenance']) {
    msg('#status', t('maintenance'), 'err');
  }
  // 目前分頁若被關掉 → 跳到第一個可用的
  const cur = currentTab();
  if (!tabAvailable(cur)) go(AVAILABLE_TABS.find(tabAvailable) || 'download');
  else go(cur);
  renderPlatforms(cfg.platforms, cfg.enabled_platform_count);
}

const tabAvailable = (tab) => {
  const need = FEATURE_TAB[tab];
  return !need || state.config?.features?.[need] !== false;
};
const currentTab = () => ($('.panel.on')?.id || 'p-download').slice(2);

// ── 分頁 ─────────────────────────────────────────
function go(tab) {
  if (!tabAvailable(tab)) return;
  $$('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  $$('.panel').forEach((p) => p.classList.toggle('on', p.id === 'p-' + tab));
  updateCtx();
  if (tab === 'member') { refreshMember(); renderHistory(); }
  if (tab === 'plans') loadPlans();
}
function updateCtx() {
  const tab = currentTab();
  $('#ctx').textContent = t('tab_' + tab, '');
}
$$('[data-tab]').forEach((b) => b.addEventListener('click', () => go(b.dataset.tab)));
$('#logo')?.addEventListener('click', (e) => { e.preventDefault(); go('download'); });

// ── 工具 ─────────────────────────────────────────
const fmtSize = (n) => {
  if (!n && n !== 0) return '';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
};
const fmtDur = (s) => {
  if (!s && s !== 0) return '';
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const msg = (sel, text, kind = '') => {
  const el = $(sel); if (!el) return;
  el.hidden = !text; el.className = 'msg ' + kind; el.textContent = text || '';
};
const deviceId = () => {
  let id = localStorage.getItem('fy_device_id');
  if (!id) { id = crypto.randomUUID(); localStorage.setItem('fy_device_id', id); }
  return id;
};
const MKEY = 'fy_member_token';
const memberToken = () => localStorage.getItem(MKEY) || '';

// ⚠️ 其他模組（transfer.js）也要用同一組標頭 —— 否則裝置身份不一致，
//    免費次數會扣到「不存在的裝置」上（2026-09-27 實際踩到）
window.FY_HEADERS = () => ({
  'Content-Type': 'application/json',
  'X-Device-Id': deviceId(),
  'X-Timezone': state.tz,
  ...(memberToken() ? { 'X-Member-Token': memberToken() } : {}),
});

const api = async (url, opt = {}) => {
  // 伺服器若掛住，最久等 90 秒就放棄（避免使用者一直看轉圈）
  const ctl = new AbortController();
  const timeout = setTimeout(() => ctl.abort(), 90000);
  let r;
  try {
    r = await fetch(url, {
      ...opt,
      signal: ctl.signal,
      headers: {
        'Content-Type': 'application/json', 'X-Device-Id': deviceId(), 'X-Timezone': state.tz,
        ...(memberToken() ? { 'X-Member-Token': memberToken() } : {}),
        ...(opt.headers || {}),
      },
    });
  } catch (err) {
    clearTimeout(timeout);
    if (err && err.name === 'AbortError') {
      const e = new Error(t('err_TIMEOUT'));
      e.code = 'TIMEOUT';
      throw e;
    }
    const e = new Error(t('err_NETWORK'));
    e.code = 'NETWORK';
    throw e;
  }
  clearTimeout(timeout);

  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw apiError(j, r.status);
  return j;
};

// 後端錯誤 → 依錯誤碼翻成使用者語言（後端訊息一律當後備）
function apiError(j, status) {
  const code = j.code || (j.detail && j.detail.code) || '';
  // ① 先找錯誤碼翻譯（例：err_QUOTA_EXCEEDED）
  let message = code ? t('err_' + code, '') : '';
  // ② 再找 HTTP 狀態碼翻譯（例：502 被 Cloudflare 擋下時沒有 JSON）
  if (!message) message = t('err_HTTP_' + status, '');
  // ③ 最後才用後端訊息
  if (!message) {
    const raw = j.detail?.message || j.detail || j.message;
    message = (typeof raw === 'string' && raw) ? raw : `HTTP ${status}`;
  }
  const e = new Error(message);
  e.code = code || String(status);
  return e;
}

// ── 設定 / 次數 ───────────────────────────────────
async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  applyFlags(cfg);
  buildPlans();
}
function renderQuota(q) {
  if (!q) return;
  state.quota = q;
  const unlimited = q.unlimited || (q.download?.remaining ?? 0) >= 9999;
  // ⚠️ 小羅 2026-09-27：「已使用次數」要**照實寫**，不要顯示一橫線。
  //    沒用過就是 0，用過 1 次就是 1（∞ 只用在「剩餘」那一格）
  const n = (v) => String(Number(v) || 0);
  const fill = (left, used, reset, part) => {
    if (!left || !used) return;
    used.textContent = n(part?.used);                    // 永遠是實際數字
    left.textContent = unlimited ? '∞' : n(part?.remaining);
    if (reset) {
      reset.textContent = unlimited
        ? t('quota_unlimited')
        : t('quota_reset_pre') + ' ' + (part?.reset_hint || '00:00') + ' ' + t('quota_reset_suf');
    }
  };
  // 下載頁的面板
  fill($('#q-left'), $('#q-used'), $('#q-reset'), q.download);
  // 傳輸頁的面板（與下載**分開**計算，各自 5 次／日）
  fill($('#t-left'), $('#t-used'), $('#t-reset'), q.transfer);
}
async function loadQuota() {
  try { renderQuota((await api('/api/quota')).quota); } catch { /* 忽略 */ }
}
// 無損傳輸扣完次數後也要更新面板 → 讓 transfer.js 也能呼叫
window._loadQuota = loadQuota;

// ── 解析 ─────────────────────────────────────────
// ── 貼上流程（小羅 2026-09-27 逐字複述，完全照做）─────────
//   ① 點網址欄 → 自動清空舊連結 → 出現「📋 貼上」
//   ② 按「貼上」→ 讀剪貼簿 → **自動解析**（不用再按任何按鈕）
//   ③ 手機原生的「貼上」泡泡也通 → paste 事件一樣自動解析
//   ④ 下面的「🔍 重新解析」只在解析失敗時按（重試用）
//
// ⚠️ 教訓：不要自己去讀 event.clipboardData —— iOS Safari 常讀不到，
//    讀到空的就 return，等於「貼上後完全不動」。要讓瀏覽器正常貼上，
//    再從輸入框取值（v8i8 的做法）。
let _autoPasteTimer = null;

function revealPaste(show) {
  const b = $('#paste');
  if (b) b.hidden = !show;
}

// ① 點網址欄：清空舊的 ＋ 出現「貼上」按鈕（小羅不用自己刪上一筆）
$('#url').addEventListener('focus', () => {
  if ($('#url').value) $('#url').value = '';
  revealPaste(true);
});
$('#url').addEventListener('blur', () => {
  // 延遲收起，否則點「貼上」按鈕會先觸發 blur 而按不到
  setTimeout(() => { if (document.activeElement !== $('#url')) revealPaste(false); }, 220);
});

// ② 📋 貼上：讀剪貼簿 → 填入 → 自動解析
$('#paste').addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (!text || !text.trim()) { msg('#status', t('paste_empty'), 'err'); return; }
    $('#url').value = extractUrl(text);
    msg('#status', '');
    doResolve();
  } catch {
    // 剪貼簿被拒（iOS 有時要使用者手勢）→ 聚焦讓使用者自己長按貼上
    $('#url').focus();
    msg('#status', t('paste_denied'), 'err');
  }
});

// ③ 手機原生貼上（長按→貼上／Ctrl+V）：讓瀏覽器正常貼上，完成後自動解析
$('#url').addEventListener('paste', () => {
  $('#url').value = '';                 // 先清空 → 新連結直接覆蓋舊的
  clearTimeout(_autoPasteTimer);
  _autoPasteTimer = setTimeout(() => {
    const val = $('#url').value.trim();
    if (val.includes('http')) doResolve();
  }, 320);
});

$('#url').addEventListener('keydown', (e) => { if (e.key === 'Enter') doResolve(); });

// ④ 🔍 重新解析：解析失敗時再按一次（不會去讀剪貼簿）
$('#go').addEventListener('click', () => {
  if (!$('#url').value.trim()) { msg('#status', t('paste_empty'), 'err'); return; }
  doResolve();
});

/**
 * 從任意文字抽出網址（支援抖音/TikTok 的整段分享文字）。
 * 參考 v8i8 的 extractUrl()：抓第一個 http… 並去掉尾端標點。
 */
function extractUrl(text) {
  const t = (text || '').trim();
  const m = t.match(/https?:\/\/[^\s一-鿿　-〿＀-￯]+/);
  if (m) return m[0].replace(/[,，。！？、)）\]]+$/, '');
  return t;
}

/** 從剪貼簿讀取網址（手機上要使用者手勢，所以只能綁在按鈕上） */
async function readClipboard() {
  try {
    if (!navigator.clipboard?.readText) return '';
    const text = await navigator.clipboard.readText();
    return (text || '').trim();
  } catch {
    return '';
  }
}

let _resolveTimer = null;

/** 解析進度：伺服器端在做，我們無法知道真實百分比 → 用不確定進度條 ＋ 已等待秒數 ＋ 階段文案 */
function startResolveProgress() {
  const t0 = performance.now();
  $('#rtrack').hidden = false; $('#rpm').hidden = false;
  $('#status').hidden = true;
  const setStage = () => {
    const sec = (performance.now() - t0) / 1000;
    $('#rmsg').textContent = sec < 3 ? t('resolve_s1') : sec < 10 ? t('resolve_s2') : t('resolve_s3');
    $('#rsec').textContent = `${t('resolve_elapsed')} ${sec.toFixed(0)}s`;
  };
  setStage();
  clearInterval(_resolveTimer);
  _resolveTimer = setInterval(setStage, 300);
}
function stopResolveProgress() {
  clearInterval(_resolveTimer); _resolveTimer = null;
  $('#rtrack').hidden = true; $('#rpm').hidden = true;
}

async function doResolve() {
  let url = extractUrl($('#url').value);
  // 輸入框空的 → 自動讀剪貼簿（一鍵「貼上並解析」）
  if (!url) {
    const text = await readClipboard();
    if (!text) {
      msg('#status', t('paste_denied'), 'err');
      return;
    }
    url = extractUrl(text);
    $('#url').value = url;
  }
  $('#go').disabled = true;
  startResolveProgress();
  try {
    const res = await api('/api/resolve', { method: 'POST', body: JSON.stringify({ url }) });
    state.info = res.data;
    state.urlUsed = url;                 // 記住這次用的網址（判斷要不要清空用）
    stopResolveProgress();
    renderResult(res.data);
    loadQuota();
  } catch (err) {
    stopResolveProgress();
    msg('#status', err.message, 'err');
    // ⚠️ 失敗也要回報 —— 手機若在「請求還沒到伺服器」就逾時／被 Cloudflare 擋，
    //    伺服器端完全不會有紀錄，後台成功率就會假性 100%。
    api('/api/track/resolve', { method: 'POST', body: JSON.stringify({
      result: 'fail', code: err.code || 'CLIENT_ERROR',
      error: String(err.message).slice(0, 90), url }) }).catch(() => {});
  } finally {
    $('#go').disabled = false;
  }
}

function renderResult(info) {
  msg('#status', '');
  const v = $('#pvid');
  v.hidden = true; v.removeAttribute('src'); v.dataset.src = '';   // 清掉上一支
  const cover = $('#cover');
  cover.onerror = () => { cover.hidden = true; $('#shade').hidden = true; };
  if (info.cover) { cover.src = info.cover; cover.hidden = false; $('#shade').hidden = false; }
  else { cover.hidden = true; $('#shade').hidden = true; }
  $('#pv-title').textContent = info.title || t('pv_notitle');
  $('#pv-plat').textContent = info.platform || '';
  $('#pv-dur').textContent = fmtDur(info.duration);

  const box = $('#qs');
  box.innerHTML = '';
  state.selected = null;
  const list = info.formats || [];
  list.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'q';
    el.innerHTML = `<span class="lb">${esc(qlabel(f.label))}</span><span class="s">${f.size ? fmtSize(f.size) : ''} ${f.ext || ''}</span>`;
    el.addEventListener('click', () => selectFormat(i));
    el.dataset.i = i;
    box.appendChild(el);
  });
  if (!list.length) box.innerHTML = `<div class="q"><span class="lb">${t('no_formats')}</span></div>`;
  // 自動選最高畫質（可播放的優先）→ 使用者解析完立刻就能按播放／下載
  const best = list.findIndex((f) => playUrl(f));
  const pick = best >= 0 ? best : (list.length ? 0 : -1);
  if (pick >= 0) selectFormat(pick);
  else { $('#download').disabled = true; $('#download').textContent = t('btn_choose_quality'); }
}
// 後端回傳的畫質名稱（中文）→ 依語言顯示
const QMAP = { '原畫': 'q_origin', '高清': 'q_hd', '標清': 'q_sd', '純音訊': 'q_audio',
               '下載版': 'q_download', '備援線路': 'q_backup' };
function qlabel(label) {
  if (state.lang === 'zh-Hant' || !label) return label;
  if (QMAP[label]) return t(QMAP[label], label);
  const m = /^線路 (\d+)$/.exec(label);
  if (m) return t('q_line', 'Line') + ' ' + m[1];
  if (/^圖 \d+$/.test(label)) return label.replace('圖', state.lang === 'en' ? 'Image' : '图');
  return label;
}

/**
 * 這個格式「在站內播放」要用的網址。
 * 優先順序：relay（伺服器轉發，Referer 一定對）> 直連 > 代理。
 * 純音訊不回傳（用播放器播聲音沒意義，讓使用者直接下載）。
 */
function playUrl(f) {
  if (!f || f.audio) return '';
  if (f.mode === 'relay' && f.relay_key) return '/api/proxy-video?k=' + encodeURIComponent(f.relay_key);
  if (f.url && /^https?:/i.test(f.url)) return f.url;
  return '';
}

/** 設定播放器來源；播不出來就退回封面（不要讓使用者看到黑色破圖） */
function setPlayer(f) {
  const v = $('#pvid'), cover = $('#cover');
  const src = playUrl(f);
  if (!src) {
    v.hidden = true;
    v.removeAttribute('src');
    return;
  }
  const wasHidden = v.hidden;
  if (v.dataset.src !== src) {
    v.dataset.src = src;
    v.src = src;
    v.hidden = false;
    v.load();
  }
  v.onerror = () => {                 // 平台擋掉 → 退回封面
    v.hidden = true;
    cover.hidden = false;
    $('#shade').hidden = false;
  };
  // 第一次顯示時把封面收起來（影片本身有畫面）
  if (wasHidden) { cover.hidden = true; $('#shade').hidden = true; }
}

function selectFormat(i) {
  state.selected = i;
  $$('#qs .q').forEach((el) => el.classList.toggle('on', Number(el.dataset.i) === i));
  $('#download').disabled = false;
  $('#download').textContent = t('btn_download');
  setPlayer(state.info?.formats?.[i]);      // 點畫質 → 播放器跟著換
}

// ── 下載（跨平台：iOS 存相簿／Android 下載／桌機選路徑）──
$('#download').addEventListener('click', async () => {
  const f = state.info?.formats?.[state.selected];
  if (!f) return;
  const track = $('#track'), bar = $('#bar'), pm = $('#pm');
  track.hidden = false; pm.hidden = false; bar.style.width = '0%';
  $('#pct').textContent = '0%'; $('#pspeed').textContent = '';
  const env = window.FY?.env || {};
  const setPct = (p) => { bar.style.width = p + '%'; $('#pct').textContent = p + '%'; };
  const title = (state.info.title || 'video').slice(0, 60);
  const ext = f.audio ? (f.ext || 'm4a') : (f.ext || 'mp4');
  const filename = `${title}.${ext}`;

  window.FY?.keepAwake?.(true);
  try {
    if (f.mode === 'relay') {
      // 經本站轉發（平台 CDN 檢查 Referer；DASH 平台還會順便合併影音軌）
      msg('#status', '準備中…');
      const url = '/api/proxy-video?k=' + encodeURIComponent(f.relay_key || '');
      const blob = await window.FY.fetchWithProgress(url, {
        onProgress: ({ pct, speed }) => {
          if (pct !== null) setPct(pct);
          if (speed) $('#pspeed').textContent = fmtSize(speed) + t('tr_per_sec');
        },
      });
      setPct(100);
      await window.FY.saveBlob(blob, filename, (text, kind) => msg('#status', text, kind || ''));
    } else if (f.mode === 'proxy') {
      const q = new URLSearchParams({ src: state.info.source_url, name: filename });
      if (f.audio) q.set('audio', 'true'); else if (f.height) q.set('h', String(f.height));
      msg('#status', '伺服器取得檔案中…');
      const blob = await window.FY.fetchWithProgress('/api/download?' + q.toString(), {
        onProgress: ({ pct, speed }) => {
          if (pct !== null) setPct(pct);
          if (speed) $('#pspeed').textContent = fmtSize(speed) + t('tr_per_sec');
        },
      });
      setPct(100);
      await window.FY.saveBlob(blob, filename, (text, kind) => msg('#status', text, kind || ''));
    } else if (f.mode === 'direct') {
      // CDN 擋 CORS → 無法先抓成 blob；桌機用 <a download>，手機開新頁面存相簿
      msg('#status', '準備下載…');
      await window.FY.saveUrl(f.url, filename, (text, kind) => msg('#status', text, kind || ''));
      setPct(100);
    } else {
      const blob = await window.FY.fetchWithProgress(f.url, {
        headers: f.headers || {},
        onProgress: ({ pct, speed }) => {
          if (pct !== null) setPct(pct);
          if (speed) $('#pspeed').textContent = fmtSize(speed) + t('tr_per_sec');
        },
      });
      setPct(100);
      await window.FY.saveBlob(blob, filename, (text, kind) => msg('#status', text, kind || ''));
    }
    saveHistory(state.info, f);
    api('/api/track/download', { method: 'POST', body: JSON.stringify({
      platform: state.info.platform, quality: f.label, size: f.size || null,
      mode: f.mode, url: state.info.source_url }) }).catch(() => {});
    // 參考 v8i8：下載完成後清空輸入框 ＋ 聚焦 → 使用者直接貼下一個就行
    const isTouch = window.FY?.env ? (window.FY.env.isIOS || window.FY.env.isAndroid) : false;
    setTimeout(() => {
      $('#url').value = '';
      if (!isTouch) $('#url').focus();
    }, isTouch ? 400 : 1600);
  } catch (err) {
    // CORS 失敗 → 退回直接開連結（比整個失敗好）
    if (f.mode === 'fetch' || f.mode === 'direct') {
      try {
        await window.FY.saveUrl(f.url, filename, (text, kind) => msg('#status', text, kind || ''));
        return;
      } catch { /* 繼續往下報錯 */ }
    }
    // 回報下載失敗（後台「平台表現」才看得到失敗率）
    api('/api/track/download', { method: 'POST', body: JSON.stringify({
      platform: state.info.platform, quality: f.label, size: f.size || null,
      mode: f.mode, ok: false, error: String(err.message).slice(0, 80),
      url: state.info.source_url }) }).catch(() => {});
    msg('#status', t('dl_fail') + err.message, 'err');
    $('#pspeed').textContent = '';
  } finally {
    window.FY?.keepAwake?.(false);
  }
});

// ── 歷史 ─────────────────────────────────────────
const HKEY = 'fy_history';
const getHistory = () => { try { return JSON.parse(localStorage.getItem(HKEY) || '[]'); } catch { return []; } };
function saveHistory(info, fmt) {
  const list = getHistory();
  const entry = { title: info.title, platform: info.platform, cover: info.cover,
                  label: fmt.label, size: fmt.size || null, at: Date.now() };
  const i = list.findIndex((h) => h.title === info.title && h.platform === info.platform);
  if (i >= 0) list.splice(i, 1);
  list.unshift(entry);
  localStorage.setItem(HKEY, JSON.stringify(list.slice(0, state.config?.history_limit || 50)));
}
function renderHistory() {
  const box = $('#h-list');
  if (!box) return;
  const list = getHistory();
  if (!list.length) { box.innerHTML = `<div class="hrow"><span class="dim">${t('history_empty')}</span></div>`; return; }
  box.innerHTML = list.map((h) => `<div class="hrow">
    <img src="${esc(h.cover || '')}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
    <div class="m"><div class="t">${esc(h.title)}</div>
    <div class="s">${esc(h.platform)} · ${esc(h.label)}${h.size ? ' · ' + fmtSize(h.size) : ''} · ${new Date(h.at).toLocaleString()}</div></div>
  </div>`).join('');
}
$('#h-clear').addEventListener('click', () => {
  if (confirm(t('confirm_clear'))) { localStorage.removeItem(HKEY); renderHistory(); }
});

// ── 會員 ─────────────────────────────────────────
async function refreshMember() {
  try {
    const me = await api('/api/member/me');
    if (me.logged_in) {
      $('#m-guest').hidden = true; $('#m-info').hidden = false;
      const m = me.member || {};
      const planName = t('plan_' + (me.plan || 'free'));
      $('#m-detail').innerHTML = `
        <div class="r"><div class="k">${t('m_email')}</div><div class="v">${esc(m.email)}</div></div>
        <div class="r"><div class="k">${t('m_plan')}</div><div class="v">${planName}${me.unlimited ? ' ' + t('m_unlimited') : ''}</div></div>
        <div class="r"><div class="k">${t('m_expires')}</div><div class="v">${m.expires_at ? new Date(m.expires_at * 1000).toLocaleDateString() : '—'}</div></div>`;
      return;
    }
  } catch { /* 未登入 */ }
  $('#m-guest').hidden = false; $('#m-info').hidden = true;
}
$('#m-login').addEventListener('click', async () => {
  try {
    const j = await api('/api/member/login', { method: 'POST', body: JSON.stringify({
      email: $('#m-email').value, password: $('#m-pass').value, tz: state.tz }) });
    localStorage.setItem(MKEY, j.token);
    msg('#m-msg', t('login_ok'), 'ok');
    await refreshMember(); await loadQuota();
  } catch (e) { msg('#m-msg', e.message, 'err'); }
});
$('#m-register').addEventListener('click', async () => {
  try {
    const j = await api('/api/member/register', { method: 'POST', body: JSON.stringify({
      email: $('#m-email').value, password: $('#m-pass').value, tz: state.tz }) });
    localStorage.setItem(MKEY, j.token);
    msg('#m-msg', t('register_ok'), 'ok');
    await refreshMember(); await loadQuota();
  } catch (e) { msg('#m-msg', e.message, 'err'); }
});
$('#m-logout').addEventListener('click', async () => {
  localStorage.removeItem(MKEY); await refreshMember(); await loadQuota();
});
// 回報問題（任何人都能送）→ 自動附帶「目前平台」與「輸入框連結」方便重現
$('#rp-send').addEventListener('click', async () => {
  const text = $('#rp-msg').value.trim();
  if (text.length < 4) { msg('#rp-status', t('report_short'), 'err'); return; }
  $('#rp-send').disabled = true;
  try {
    const j = await api('/api/report', {
      method: 'POST',
      body: JSON.stringify({
        message: text,
        contact: $('#rp-contact').value.trim(),
        platform: state.info?.platform || '',
        url: state.urlUsed || $('#url').value.trim(),
      }),
    });
    msg('#rp-status', j.message || 'OK', 'ok');
    $('#rp-msg').value = '';
  } catch (e) { msg('#rp-status', e.message, 'err'); }
  finally { $('#rp-send').disabled = false; }
});

// ── 方案 ─────────────────────────────────────────
let payReady = null;      // null=尚未載入；語言切換時要靠它重繪
function renderPayStatus() {
  if (payReady === null) return;
  msg('#pay-status', payReady ? t('pay_ready') : t('pay_preparing'));
}
function clearTransient() {
  // 一次性的提示訊息（解析中、登入成功…）在換語言時直接清掉，避免殘留舊語言
  ['#status', '#m-msg', '#rp-status'].forEach((sel) => msg(sel, ''));
}
async function loadPlans() {
  try {
    const j = await api('/api/pay/plans');
    Object.entries(j.plans || {}).forEach(([id, p]) => {
      const el = document.querySelector(`[data-price="${id}"]`);
      if (el) el.textContent = p.price;
    });
    payReady = Object.values(j.providers || {}).filter((p) => p.ready).length > 0;
  renderPayStatus();
  } catch { /* 忽略 */ }
}
$$('[data-buy]').forEach((b) => b.addEventListener('click', async () => {
  try {
    const j = await api('/api/pay/checkout', { method: 'POST', body: JSON.stringify({ plan: b.dataset.buy, provider: 'ecpay' }) });
    if (j.checkout_url) window.location.href = j.checkout_url;
  } catch (e) { msg('#pay-status', e.message, 'err'); }
}));

// ── 啟動 ─────────────────────────────────────────
(async function init() {
  await loadLang();
  try { await loadConfig(); } catch (e) { console.warn(e); }
  await loadQuota();
  renderHistory();
})();
