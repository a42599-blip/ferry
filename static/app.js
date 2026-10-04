/* 轉運站 — 前台（原生 JS，無框架）
   ⚠️ 所有顯示文字一律走 t()，翻譯要含「按鈕、placeholder、動態訊息」。 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  config: null, info: null, selected: null,
  loggedIn: false, email: '',            // 目前登入的會員（付款核實用）
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  lang: 'zh-Hant', L: {},
};

// ── i18n（全域共用，transfer.js 也會用）───────────────
// 方案價格（後台可改）：說明文字用 {pm}／{pl} 代入「目前價格」（小羅 2026-10-04）
const PLAN_PRICES = { monthly: 88, lifetime: 988 };
// 免費次數（後台可改）：文字用 {g}／{m} 代入「訪客／免費會員每日次數」（小羅 2026-10-04）
const LIMITS = { g: 3, m: 5 };
function px(v) {
  if (typeof v === 'string') {
    return v.replace(/\{pm\}/g, String(PLAN_PRICES.monthly))
            .replace(/\{pl\}/g, String(PLAN_PRICES.lifetime))
            .replace(/\{g\}/g, String(LIMITS.g))
            .replace(/\{m\}/g, String(LIMITS.m));
  }
  if (Array.isArray(v)) return v.map(px);
  if (v && typeof v === 'object') {
    const o = {};
    Object.keys(v).forEach((k) => { o[k] = px(v[k]); });
    return o;
  }
  return v;
}
function t(key, fallback) {
  const v = state.L[key];
  if (v === undefined || v === null) return fallback !== undefined ? fallback : key;
  return px(v);                // 空字串是「該語言不需要這個字」，不是缺翻譯
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
  // ⚠️ data-i18n-html：允許翻譯字串夾 HTML（小羅 2026-09-29：
  //    「跟次數有關的數字要用紅字標出來，不然看起來會漏掉」）
  $$('[data-i18n-html]').forEach((el) => {
    const v = t(el.dataset.i18nHtml);
    if (v !== undefined && v !== null) el.innerHTML = v;
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
  // 小羅 2026-10-05：切換語言時，JS「動態寫上去」的文字（例：無損傳輸的
  // 「已連上」綠框、連線狀態、連線裝置、狀態訊息）也要跟著重翻。
  try { window.dispatchEvent(new CustomEvent('fy:lang', { detail: { lang: state.lang } })); } catch (_) { /* 忽略 */ }
  buildTeach();
  buildPlans();
  renderPlanButtons();
  renderAbout();
  renderPlatforms(state.config?.platforms, state.config?.enabled_platform_count);
  renderQuota(state.quota);
  renderHistory();
  $('.msg:not([hidden])') && clearTransient();
  if (state.info) renderResult(state.info);
  renderWho(state.me);          // 身分顯示（訪客／暱稱）也要跟著換語言
  if (currentTab() === 'member') refreshMember();
  updateCtx();
}

async function loadLang(code) {
  state.lang = code || localStorage.getItem('fy_lang') || 'zh-Hant';
  try {
    const r = await fetch('/locales/' + state.lang + '.json?v=69');
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
  const ql = t('teach_quota_list', []);
  if ($('#teach-quota-list')) $('#teach-quota-list').innerHTML = (ql || []).map((x) => `<li>${x}</li>`).join('');
  if ($('#teach-faq')) $('#teach-faq').innerHTML = (faq || []).map(
    ([q, a]) => `<div class="r"><div class="k">${q}</div><div class="v">${a}</div></div>`).join('');
}
function buildPlans() {
  const n = { dl: state.config?.quota?.download_per_day ?? 5, tr: state.config?.quota?.transfer_per_day ?? 5 };
  const fill = (arr) => (arr || []).map((s) => `<li>${String(s).replace('{dl}', n.dl).replace('{tr}', n.tr)}</li>`).join('');
  if ($('#plan-free-list')) $('#plan-free-list').innerHTML = fill(t('plan_free_list', []));
  if ($('#plan-monthly-list')) $('#plan-monthly-list').innerHTML = fill(t('plan_monthly_list', []));
  if ($('#plan-lifetime-list')) $('#plan-lifetime-list').innerHTML = fill(t('plan_lifetime_list', []));
  // 怎麼付款／怎麼退款（小羅 2026-09-30：金流商審核要看到，客戶也要一次看懂）
  const kv = (arr) => (arr || []).map(
    ([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  if ($('#plans-pay-list')) $('#plans-pay-list').innerHTML = kv(t('plans_pay_list', []));
  if ($('#plans-refund-list')) $('#plans-refund-list').innerHTML = kv(t('plans_refund_list', []));
  if ($('#plans-fx-list')) $('#plans-fx-list').innerHTML = kv(t('plans_fx_list', []));
}

// ── 關於我們（小羅 2026-09-30）───────────────────────
//   前台「關於我們」頁（在「方案」與「會員」中間）的聯絡資料。
//   資料來源＝後台「關於我們」（settings 表）→ /api/config 帶下來；改後台、前台即時生效。
//   `hours` 若後台沒填 → 用「該語言」的內建值，繁／簡／英才都顯示得對。
function renderAbout() {
  const a = state.config?.about || {};
  const email = a.email || 'a42599@gmail.com';
  const phone = a.phone || '0980-222196';
  const hours = a.hours || t('about_hours_val');
  const rows = [
    [t('about_email'), `<a href="mailto:${esc(email)}">${esc(email)}</a>`],
    [t('about_phone'), esc(phone)],
    [t('about_hours'), esc(hours)],
  ];
  if ($('#about-contact')) {
    $('#about-contact').innerHTML = rows.map(
      ([k, v]) => `<div class="r"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  }
  // 方案頁那一行也吃同一份資料（小羅換 Email 時，兩邊會一起換，不會漏）
  if ($('#plans-contact')) {
    $('#plans-contact').textContent = t('about_email') + ' ' + email + '｜'
      + t('about_phone') + ' ' + phone + '｜' + hours;
  }
}

// ── 開關連動：關掉的功能，前台整個消失 ─────────────────
const FEATURE_TAB = {
  download: 'feature.download',   // ← 關掉＝整個下載模組消失
  transfer: 'feature.transfer',
  teach: 'feature.teach',
  plans: 'feature.plans',
  member: 'feature.member',
};
const AVAILABLE_TABS = ['download', 'transfer', 'teach', 'plans', 'about', 'member'];

// 付費訂閱是否開啟（feature.billing 沒設 → 當成開啟，維持原本行為）
const billingOn = (f) => (f || {})['feature.billing'] !== false;

// 方案卡片的按鈕狀態（小羅 2026-10-04）：
//   免費 → 免費卡「目前使用中」；月／終身都可買
//   月會員 → 月卡「目前使用中」＋按鈕變「續訂」（可重複訂閱）；終身仍可買（升級）
//   終身會員 → 終身卡「目前使用中」；**月／終身兩顆付費按鈕都消失**（不需再付費）
function renderPlanButtons() {
  const tier = (state.me && state.me.tier) || 'guest';
  const billing = billingOn(state.config && state.config.features);
  const isFree = (tier === 'free' || tier === 'guest');
  const freeBtn = $('#plan-free-btn');
  const mBtn = $('#plan-monthly-btn');
  const lBtn = $('#plan-lifetime-btn');
  const mCur = $('#cur-monthly');
  const lCur = $('#cur-lifetime');
  if (freeBtn) freeBtn.hidden = !isFree;
  if (mCur) mCur.hidden = (tier !== 'monthly');
  if (lCur) lCur.hidden = (tier !== 'lifetime');
  if (mBtn) {
    mBtn.hidden = !billing || tier === 'lifetime';
    mBtn.dataset.i18n = (tier === 'monthly') ? 'btn_renew' : 'btn_subscribe';
    mBtn.textContent = t(mBtn.dataset.i18n);
  }
  if (lBtn) lBtn.hidden = !billing || tier === 'lifetime';
}

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

  // ── 會員登入開關（feature.auth）─────────────────────────
  //   關掉 → 會員頁只顯示「籌備中」，登入／註冊表單整個收起來。
  //   （小羅 2026-09-27：之前切這個開關前台完全沒反應 → 這裡補上聯動）
  const authOn = f['feature.auth'] !== false;
  if ($('#m-auth')) $('#m-auth').hidden = !authOn;
  if ($('#m-off')) $('#m-off').hidden = authOn;

  // ── 付費訂閱開關（feature.billing）───────────────────────
  //   關掉 → 方案頁的「立即開通」按鈕收起來，改成「公測期間暫不收費」。
  renderPlanButtons();
  if ($('#paybox')) $('#paybox').hidden = !billingOn(f);
  if ($('#pay-off')) $('#pay-off').hidden = billingOn(f);
  // 正式收費（billing 開）→ 次數列的公測說明（.beta-open）收起來（小羅 2026-10-04）
  $$('.beta-open').forEach((el) => { el.hidden = billingOn(f); });

  // ── 廣告開關聯動（小羅 2026-09-29）───────────────────────
  //   廣告開關一打開（任一等級要開始看廣告）→ 全站「完全不會有廣告／尚未啟用」那些字**自動消失**；
  //   兩顆都關掉 → 再出現。不只首頁：所有帶 .ads-off-note 的說明都會跟著（用 class 統一控制）。
  //   ⚠️ 將來正式上線（付費網站）時，這段公測說明要改成「付費會員福利」的說法（等小羅決定）。
  // 「全站有沒有廣告」的說明文字用（任一廣告開關開著 → 公測「沒有廣告」的說明就收起來）
  // 「實際有沒有廣告」：彈窗（15 秒）廣告要「次數限制 + 廣告」兩組都開才會真的跳（小羅 2026-10-04）；
  //   底部固定廣告只要開，就算「有廣告」。
  const _limitOn = f['feature.free_limit_download'] !== false || f['feature.free_limit_transfer'] !== false;
  const _adLive = !!(f['feature.ads_guest'] || f['feature.ads_member']) && _limitOn;
  _adsOn = _adLive || !!f['feature.ads_bottom'];
  applyAdCode('popup', cfg.ad_codes?.popup);          // 彈窗廣告（後台「彈窗廣告」那格；何時彈由 ads_guest／ads_member 決定）
  if (f['feature.ads_bottom']) applyBottomAd(cfg.ad_codes?.bottom);  // 底部固定廣告（獨立開關，只負責顯示／不顯示；小羅 2026-10-02）
  applyAdsNotes();

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
  if (tab === 'about') renderAbout();
  applyAdsNotes();                       // 動態產生的說明也要跟著廣告開關（小羅 2026-09-29）
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

// ── 廣告開關 → 說明文字聯動（小羅 2026-09-29）──────────────
//   廣告開關一打開（任一等級要開始看廣告）→ 全站「完全不會有廣告／尚未啟用」那些字
//   **自動消失**；兩顆都關掉 → 再出現（不只首頁，所有帶 .ads-off-note 的說明都會跟著）。
//   ⚠️ 因為有些說明是「後來才動態產生」（例如方案頁）→ 一定要在每次換頁／渲染後**重套一次**。
//   ⚠️ 將來正式上線（付費網站）時，這段公測說明要改成「付費會員福利」的說法（等小羅決定）。
let _adsOn = false;
//: 已經套用過的廣告位置（同一個位置不重複插）
const _adApplied = new Set();

// ── 廣告碼（完全以「後台 → 📺 廣告」的那兩個框為準）─────────
//   小羅 2026-10-01：分兩個位置 —— `popup`（15 秒彈窗／次數補回）、`bottom`（頁面底部固定）。
//   ⚠️ 框空著＝那個位置沒有廣告。
//   ⚠️ 一次只放「後台貼的那一段」（不做 ad stacking，避免封號）。

/** 把一段廣告碼解析成節點序列（script 與 div／img 都要，缺一顆都會壞）。
 *  ⚠️ DOMParser 會把單獨的 <script> 放進 <head>（不是 <body>）→ 兩邊都要收，
 *     否則只有 script 的碼（如 Social Bar）會被當成空的（2026-10-01 實測踩到）。 */
function adNodes(html) {
  const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
  return [...doc.querySelectorAll('head > *, body > *')];
}

/** 把廣告碼放進某個容器（script 依序執行；div 等原樣插入）。
 *  ⚠️ 不能只用 insertAdjacentHTML —— 插進來的 <script> 不會執行（小羅 2026-09-30 的空框真因）。
 *  ⚠️ Native Banner 需要它自己的 <div id="container-…"> 容器，所以非 script 的節點也要一起放。 */
function injectAdCode(container, html) {
  adNodes(html).forEach((n) => {
    let el;
    if (n.tagName === 'SCRIPT') {
      el = document.createElement('script');
      if (n.getAttribute('src')) { el.src = n.getAttribute('src'); el.async = true; }
      else { el.textContent = n.textContent || ''; }
    } else {
      el = n.cloneNode(true);
    }
    container.appendChild(el);
  });
}

function applyAdCode(slotName, raw) {
  const slot = $('#ad-slot');
  if (!slot || _adApplied.has(slotName)) return;
  if (!adNodes(raw).length) { slot.hidden = true; return; }  // 後台那個框空著 → 收起來
  _adApplied.add(slotName);
  slot.hidden = false;
  injectAdCode(slot, raw);
  watchAdLoaded();
}

/** 底部固定廣告（後台「底部固定廣告」那格；空＝不顯示）。
 *  小羅 2026-10-01：要「固定在頁面最底部、不亂飄、不擋畫面」→ 放進 `#ad-bottom` 固定條，
 *  並把廣告條高度補成 body 的下內距（上面的功能頁與下面的紅字都不會被蓋到）。
 *  ⚠️ 這裡請貼「Banner／Native Banner」（會顯示在容器裡）；
 *     Social Bar 會自己飄到角落（擋畫面），不適合放這裡。 */
function applyBottomAd(raw) {
  const bar = $('#ad-bottom');
  if (!bar || _adApplied.has('bottom')) return;
  if (!adNodes(raw).length) return;
  _adApplied.add('bottom');
  bar.hidden = false;
  injectAdCode(bar, raw);
  // 廣告高度不一定（Banner 50/90、Native 更高）→ 量多少就留多少下內距，絕不擋到內容
  const fit = () => {
    const h = Math.ceil(bar.getBoundingClientRect().height);
    document.body.style.paddingBottom = h ? h + 'px' : '';
  };
  fit();
  if (window.ResizeObserver) new ResizeObserver(fit).observe(bar);
  setTimeout(fit, 1200);
  setTimeout(fit, 3000);
}

/** 廣告有沒有真的進來？進來才把「廣告載入中…」收起來（每 1 秒看一次，最多 10 秒）。 */
function watchAdLoaded() {
  let n = 0;
  const timer = setInterval(() => {
    applyAdsNotes();
    if (++n >= 10) clearInterval(timer);
  }, 1000);
}

function applyAdsNotes() {
  // 廣告格裡若有真的廣告（廣告商塞進來的 iframe／img／ins）→ 收起「廣告載入中…」佔位字
  const slot = $('#ad-slot'), ph = $('#ad-ph');
  if (slot && ph) {
    // ⚠️ 只算「真的廣告」(iframe／img／ins／video)。插進去的 <script> 不算——
    //    否則廣告還沒載完就先把「廣告載入中…」收起來，看起來就像一個空框（小羅 2026-09-30 回報）。
    const hasAd = [...slot.children].some(
      (el) => el !== ph && /^(iframe|img|ins|video)$/i.test(el.tagName));
    ph.hidden = hasAd;
  }
  $$('.ads-off-note').forEach((el) => { el.hidden = _adsOn; });
  // 公測說明（單句，依開關組合；小羅 2026-10-04）：
  //   · 次數限制組（下載／傳輸任一開）＝有限制次數
  //   · 彈窗廣告實際會跳 ＝ 次數限制組 AND 廣告組 都開
  const _f = state.config?.features || {};
  const gateOn = !!(_f['feature.ads_guest'] || _f['feature.ads_member']);
  const limitOn = _f['feature.free_limit_download'] !== false || _f['feature.free_limit_transfer'] !== false;
  const adLive = gateOn && limitOn;
  const sentence = adLive ? t('beta_pub_ads') : (limitOn ? t('beta_pub_limit') : t('beta_pub_free'));
  $$('.beta-sentence').forEach((el) => { el.textContent = sentence; });
  $$('[data-i18n="ads_body"]').forEach((el) => {
    el.textContent = adLive ? t('ads_body_live') : t('ads_body');
  });
}
window.FY = Object.assign(window.FY || {}, { applyAdsNotes });

// ── 設定 / 次數 ───────────────────────────────────
async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  LIMITS.g = cfg.quota?.guest_per_day ?? 3;
  LIMITS.m = cfg.quota?.member_per_day ?? 5;
  applyFlags(cfg);
  buildPlans();
  renderAbout();
}
function renderQuota(q) {
  if (!q) return;
  state.quota = q;

  // ⚠️ 小羅 2026-09-27：「已使用次數」要**照實寫**，不要顯示一橫線。
  //    沒用過就是 0，用過 1 次就是 1（∞ 只用在「剩餘」那一格）
  const n = (v) => String(Number(v) || 0);
  const fill = (left, used, reset, part) => {
    if (!left || !used) return;
    // ⚠️ 每個模組有自己的 unlimited（免費次數開關是分開的）
    const unlimited = part?.unlimited || (part?.remaining ?? 0) >= 9999;
    used.textContent = n(part?.used);                    // 永遠是實際數字
    left.textContent = unlimited ? '∞' : n(part?.remaining);
    if (reset) {
      reset.textContent = unlimited
        ? t('quota_unlimited')
        : t('quota_reset_pre') + ' ' + (part?.reset_hint || '00:00') + ' ' + t('quota_reset_suf');
    }
  };
  // 依等級顯示（小羅 2026-09-29）：
  //   訪客／免費會員 → 今日剩餘次數；月會員 → 效期；永久會員 → 永久免費
  const tier = q.tier || 'guest';
  const paid = (tier === 'monthly' || tier === 'lifetime');
  const nums = $('#q-nums'), line = $('#q-tier');
  if (nums) nums.hidden = paid;
  if (line) {
    line.hidden = !paid;
    if (paid) {
      const exp = q.expires_at ? new Date(q.expires_at * 1000).toLocaleDateString() : '—';
      line.textContent = tier === 'lifetime'
        ? t('quota_lifetime')
        : t('quota_monthly_pre') + ' ' + exp;
    }
  }
  // 廣告：不主動彈出（改成「下一次動作時」由 adGate() 攔，見上方說明）

  // 下載頁的面板
  fill($('#q-left'), $('#q-used'), $('#q-reset'), q.download);
  // 傳輸頁的面板（與下載**合併**計算，共用每日額度；小羅 2026-10-04）
  fill($('#t-left'), $('#t-used'), $('#t-reset'), q.transfer);
}
// ── 前台公告（系統更新／平台故障／平台取消…）──────────────
async function loadAnnouncements() {
  const bar = $('#annbar');
  if (!bar) return;
  let items = [];
  try { items = (await api('/api/announcements')).items || []; } catch { return; }
  if (!items.length) { bar.hidden = true; return; }
  const ICON = { info: 'ℹ️', warn: '⚠️', critical: '🔴' };
  // 明寫 class 名稱（不要字串拼接，否則看不出用了哪些樣式、工具也掃不到）
  const LV = { info: 'lv-info', warn: 'lv-warn', critical: 'lv-critical' };
  bar.innerHTML = items.map((a) => `
    <div class="ann ${LV[a.level] || LV.info}">
      <span class="ai">${ICON[a.level] || 'ℹ️'}</span>
      <div class="ac"><b>${esc(a.title)}</b><div class="ab">${esc(a.body)}</div></div>
    </div>`).join('');
  bar.hidden = false;
}

// ── 客服給我的回覆（固定在畫面底部、可收合、可滾動）──────────
//   小羅 2026-09-27：
//   「框框做小一點，要可以收起來、可以上下滑動，
//     固定在下面客戶回報那個地方，不然頁面一直被拖下去。」
let _replyItems = [];
let _replyPending = 0;
let _unread = 0;
const REPLY_READ_KEY = 'fy_reply_read_at';    // 最後「已讀」的時間

/** 算出有幾則是「還沒讀過」的（比上次已讀時間新的） */
function unreadCount() {
  let readAt = 0;
  try { readAt = Number(localStorage.getItem(REPLY_READ_KEY) || 0); } catch (e) { readAt = 0; }
  const fresh = _replyItems.filter((x) => Number(x.replied_at || 0) > readAt).length;
  // 沒有新回覆但還有「處理中」的回報 → 也算 1 則提示（讀過就不再顯示）
  const pend = (_replyPending && !readAt) ? 1 : 0;
  return fresh || pend;
}

/** 標記全部已讀（打開回報問題時呼叫） */
function markRepliesRead() {
  const latest = _replyItems.reduce((a, x) => Math.max(a, Number(x.replied_at || 0)), 0);
  const val = Math.max(latest, Math.floor(Date.now() / 1000));
  try { localStorage.setItem(REPLY_READ_KEY, String(val)); } catch (e) { /* 忽略 */ }
  _unread = 0;
}

function renderReplyBar() {
  const bar = document.getElementById('replybar');
  if (!bar) return;

  // 「回報問題」標題後面的**未讀**徽章（小羅 2026-09-28：
  //  「外層有數字，點開後數字要消失，就表示沒有未讀訊息了」）
  const badge = document.getElementById('rep-badge');
  if (badge) {
    badge.hidden = !_unread;
    badge.textContent = _unread ? `💬 ${_unread}` : '';
    badge.title = _replyItems.length ? '有客服回覆你（點開查看）' : '你的回報處理中';
  }

  if (!_replyItems.length) {
    if (_replyPending) {                       // 沒有回覆但還有處理中的回報 → 細提示
      bar.hidden = false;
      bar.innerHTML = '<div class="repbar mini"><span class="ri">⏳</span>'
        + '<span class="rt">' + t('reply_pending') + '（' + _replyPending + '）</span></div>';
      return;
    }
    bar.hidden = true;
    bar.innerHTML = '';
    return;
  }

  const items = _replyItems.map((x) => {
    const when = x.replied_at ? new Date(x.replied_at * 1000).toLocaleString() : '';
    return '<div class="ritem">'
      + '<div class="rr">' + esc(x.reply) + '</div>'
      + (x.action ? '<div class="ra">' + t('reply_action') + '：' + esc(x.action) + '</div>' : '')
      + (when ? '<div class="rd">' + esc(when) + '</div>' : '')
      + '</div>';
  }).join('');

  // ⚠️ 這裡**不顯示數字**（小羅 2026-09-28：
  //    「外層『回報問題』後面已經有綠色圈圈顯示未讀數字，內層不用重複」）。
  //    內容區固定只看得到約 2 筆，超過在框內往下滑 → 框不會越長越長。
  bar.hidden = false;
  bar.innerHTML = '<div class="repbar">'
    + '<div class="rhead"><span class="ri">💬</span>'
    + '<span class="rt">' + t('reply_title') + '</span>'
    + '<span class="rhint">' + t('reply_scroll') + '</span></div>'
    + '<div class="rbody">' + items + '</div>'
    + '</div>';
}

// 前台即時通知：每 20 秒查一次「有沒有新的客服回覆」
//   小羅 2026-09-28：「我要按刷新才看得到未讀提示，有沒有辦法即時看到？」
const REPLY_POLL_EVERY = 20000;
let _lastReplyCount = -1;

async function pollMyReplies() {
  const bar = document.getElementById('replybar');
  if (!bar) return;
  let j;
  try { j = await api('/api/my-replies'); } catch (err) { return; }
  const items = (j.items || []).slice(0, 30);
  const n = items.length;
  // 有新回覆 → 跳提示（只在「增加」時提示，避免每次輪詢都吵）
  if (_lastReplyCount >= 0 && n > _lastReplyCount) {
    msg('#rp-status', t('reply_new'), 'ok');
    const box = document.getElementById('report-box');
    if (box) box.open = true;          // 自動打開讓客戶看到
  }
  _lastReplyCount = n;
  _replyItems = items;
  _replyPending = j.pending || 0;
  _unread = unreadCount();
  renderReplyBar();
}

async function loadMyReplies() {
  const bar = document.getElementById('replybar');
  if (!bar) return;
  let j;
  try { j = await api('/api/my-replies'); } catch (err) { return; }
  _replyItems = (j.items || []).slice(0, 30);
  _replyPending = j.pending || 0;
  _unread = unreadCount();
  renderReplyBar();
}

// 版本標記（讓小羅確認自己看到的是不是最新版；畫面上看不到，可從 console 查）
window.FY_UI_VERSION = 'v24 · 2026-09-28';

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

// 手機貼上後收起鍵盤：鍵盤一直開著，解析結果出現時畫面會被推來推去（小羅 2026-09-28）
function settleMobileView() {
  const env = window.FY?.env;
  if (env && (env.isIOS || env.isAndroid)) $('#url').blur();
}

// ① 點網址欄：清空舊的 ＋ 出現「貼上」按鈕（小羅不用自己刪上一筆）
$('#url').addEventListener('focus', () => {
  if ($('#url').value) $('#url').value = '';
  revealPaste(true);
  // ⚠️ 不要用 blur 隱藏按鈕！（2026-09-27 實際踩到）
  //    點「貼上」時輸入框會先失焦 → 按鈕被隱藏 → click 根本來不及觸發
  //    → 使用者看到「按了沒反應」。按鈕就讓它一直顯示，解析完成後自然不需要。
});

// ② 📋 貼上：讀剪貼簿 → 填入 → 自動解析
//    ⚠️ 用 pointerdown（手機/電腦都通）而不是 click：
//       pointerdown 比 blur 更早發生，不會被任何隱藏邏輯吃掉。
async function doPaste() {
  $('#paste').disabled = true;
  try {
    const text = await navigator.clipboard.readText();
    if (!text || !text.trim()) { msg('#status', t('paste_empty'), 'err'); return; }
    $('#url').value = extractUrl(text);
    msg('#status', '');
    settleMobileView();
    doResolve();
  } catch {
    // 剪貼簿被拒（iOS／瀏覽器限制）→ 聚焦讓使用者自己長按貼上
    $('#url').focus();
    msg('#status', t('paste_denied'), 'err');
  } finally {
    $('#paste').disabled = false;
  }
}
$('#paste').addEventListener('click', doPaste);
$('#paste').addEventListener('pointerdown', (e) => e.preventDefault());   // 不要讓輸入框失焦

// ③ 手機原生貼上（長按→貼上／Ctrl+V）：讓瀏覽器正常貼上，完成後自動解析
$('#url').addEventListener('paste', () => {
  $('#url').value = '';                 // 先清空 → 新連結直接覆蓋舊的
  clearTimeout(_autoPasteTimer);
  _autoPasteTimer = setTimeout(() => {
    const val = $('#url').value.trim();
    if (val.includes('http')) { settleMobileView(); doResolve(); }
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
  if (adGate('download', () => doResolve())) return;   // 該看廣告 → 先看完再解析
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
    // 次數用完 → 若其實是該看廣告，直接跳廣告（看完自動重做這次解析）
    if ((err.code === 'AD_REQUIRED' || err.code === 'QUOTA_EXCEEDED')
        && await quotaAdRecover('download', () => doResolve())) return;
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
  if (adGate('download', () => $('#download').click())) return;   // 該看廣告 → 先看完再下載
  const f = state.info?.formats?.[state.selected];
  if (!f) return;
  const track = $('#track'), bar = $('#bar'), pm = $('#pm');
  track.hidden = false; pm.hidden = false; bar.style.width = '0%';
  $('#pct').textContent = '0%'; $('#pspeed').textContent = '';
  const env = window.FY?.env || {};
  const setPct = (p) => { bar.style.width = p + '%'; $('#pct').textContent = p + '%'; };
  // ⚠️ 檔名一律先清洗（抖音標題可能含換行 → iOS 認不出是影片、存不進相簿，2026-09-30 修）
  const title = (window.FY?.cleanName?.(state.info.title) || 'video').slice(0, 60);
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
                  url: info.source_url || '',            // 原始頁面連結（可再解析）
                  label: fmt.label, size: fmt.size || null, at: Date.now() };
  const i = list.findIndex((h) => h.title === info.title && h.platform === info.platform);
  if (i >= 0) list.splice(i, 1);
  list.unshift(entry);
  localStorage.setItem(HKEY, JSON.stringify(list.slice(0, state.config?.history_limit || 50)));
}
// 點歷史記錄的封面 → 跳回「無水印下載」頁、自動填連結並解析
//  小羅 2026-09-29：「直接跳轉回首页便開始解析，解析完跟原本首頁功能一樣」
function useHistory(i) {
  const h = getHistory()[i];
  if (!h || !h.url) return;
  go('download');
  $('#url').value = h.url;
  doResolve();
}
function renderHistory() {
  const box = $('#h-list');
  if (!box) return;
  const list = getHistory();
  if (!list.length) { box.innerHTML = `<div class="hrow"><span class="dim">${t('history_empty')}</span></div>`; return; }
  box.innerHTML = list.map((h, i) => `<div class="hrow${h.url ? ' clickable' : ''}" data-h="${h.url ? i : ''}">
    <img src="${esc(h.cover || '')}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'">
    <div class="m"><div class="t">${esc(h.title)}</div>
    <div class="s">${esc(h.platform)} · ${esc(h.label)}${h.size ? ' · ' + fmtSize(h.size) : ''} · ${new Date(h.at).toLocaleString()}</div></div>
    ${h.url ? `<button class="hgo" data-h="${i}">${t('history_use')}</button>` : ''}
  </div>`).join('');
  // 舊資料沒有連結 → 不加點擊，維持不能點（避免誤會）
  $$('#h-list .hrow.clickable').forEach((row) => row.addEventListener('click', () => useHistory(Number(row.dataset.h))));
}
$('#h-clear').addEventListener('click', () => {
  if (confirm(t('confirm_clear'))) { localStorage.removeItem(HKEY); renderHistory(); }
});

// ── 身分顯示（小羅 2026-09-29：「讓客戶一看就知道自己是訪客還是會員」）──
//   未登入 → 訪客；登入 → 暱稱（沒設就用 Email）＋等級標籤＋頭像（暱稱第一個字）
//   頭像用文字＋顏色產生，不需要上傳圖片。
function renderWho(me) {
  state.me = me || null;
  const logged = !!(me && me.logged_in);
  const nick = logged ? ((me.nickname || (me.member || {}).email || '').trim()) : '';
  const tier = (me && me.tier) || 'guest';
  const ava = $('#who-ava'), name = $('#who-name'), tag = $('#who-tag'), out = $('#who-out');
  if (ava) {
    ava.textContent = logged ? (nick[0] || '會') : t('who_guest_short', '訪');
    ava.classList.toggle('is-member', logged);
    // 用暱稱算出固定顏色（同一個人顏色不會變，方便辨認）
    let h = 0;
    for (const ch of nick || 'guest') h = (h * 31 + ch.codePointAt(0)) % 360;
    ava.style.setProperty('--avah', String(logged ? h : 210));
  }
  if (name) name.textContent = logged ? nick : t('who_guest', '訪客');
  if (tag) tag.textContent = logged ? t('tier_' + tier, '') : '';
  if (out) out.hidden = !logged;
  // 「會員」分頁按鈕：登入後直接顯示暱稱（一眼就知道已登入）
  const tab = $('[data-tab="member"]');
  if (tab) tab.textContent = logged ? nick : t('nav_member', '會員');
  renderPlanButtons();          // 方案卡片的「目前使用中」＋付費按鈕要跟著身分變
}

// ── 廣告（預留）────────────────────────────────────────
//   小羅 2026-09-29 定案：「用滿第 3 次之後，**第 4 次**要看廣告」；
//   免費會員是「用滿第 5 次之後，第 6 次要看廣告」。
//   → 廣告不是「用完當下」跳出來，而是**下一次要動作時**擋下來先看廣告。
//   開關：後台「廣告 ── 訪客／免費會員」兩個（預設關閉 → 完全不會出現）。
let _adClearedAt = -1;      // 這一輪（同一個 used 值）已經看過廣告
let _adPending = null;      // 被廣告擋下來的動作（看完廣告後要接著做）

/** 次數用完時的保險（小羅 2026-09-29：「用完之後要再跳廣告，無限輪迴」）：
 *  伺服器回 QUOTA_EXCEEDED 時，先更新次數狀態；如果其實是「該看廣告」→
 *  直接把廣告彈出來，看完自動重做剛剛被擋下的動作。
 *  （為什麼需要：前台狀態萬一過期，使用者才不會只看到「今天次數用完」而卡死。）
 *  @returns true＝已經跳廣告（呼叫端直接 return） */
async function quotaAdRecover(kind, resume) {
  try { await loadQuota(); } catch { /* 忽略 */ }
  const a = state.quota && state.quota.ads;
  if (!a || !a.enabled || !a.due) return false;
  _adClearedAt = -1;
  return adGate(kind, resume);
}
window.FY = Object.assign(window.FY || {}, { quotaAdRecover: (k, r) => quotaAdRecover(k, r) });

/** 動作前檢查：需要看廣告就顯示彈窗、記住「被擋下的動作」，回傳 true（呼叫端直接 return）。
 *  看完廣告按「繼續」→ 打 /api/ads/reward 依等級把次數加回來 → 再接著做原本的動作。 */
function adGate(kind, resume) {
  const a = state.quota && state.quota.ads;
  if (!a || !a.enabled || !a.due) return false;
  if (a.used === _adClearedAt) return false;      // 這一輪已經看過 → 放行
  _adPending = { kind: kind || 'download', resume: typeof resume === 'function' ? resume : null };
  const box = $('#adsbox');
  if (box) box.hidden = false;                    // 先看廣告，看完才能繼續
  startAdCountdown();                             // 看滿 N 秒才能按「繼續」（伺服器也會驗）
  return true;
}

/** 廣告倒數：跟伺服器要秒數 → 期間「繼續」不能按（伺服器端也會驗，改前端沒用）。 */
let _adTimer = null;
async function startAdCountdown() {
  const btn = $('#ads-continue'), left = $('#ad-left');
  let sec = 15;
  try {
    const j = await api('/api/ads/start', { method: 'POST' });     // 記錄「開始看廣告」的時間
    if (j && j.min_seconds) sec = Number(j.min_seconds) || 15;
  } catch { /* 拿不到就用預設 15 秒 */ }
  if (btn) { btn.disabled = true; btn.textContent = t('ads_waiting', '請稍候…'); }
  clearInterval(_adTimer);
  _adTimer = setInterval(() => {
    sec -= 1;
    if (left) left.textContent = sec > 0 ? t('ads_countdown', '廣告播放中，還剩 {s} 秒').replace('{s}', String(sec)) : '';
    if (sec <= 0) {
      clearInterval(_adTimer);
      if (btn) { btn.disabled = false; btn.textContent = t('ads_continue', '繼續使用'); }
    }
  }, 1000);
  if (left) left.textContent = t('ads_countdown', '廣告播放中，還剩 {s} 秒').replace('{s}', String(sec));
}

// 給其他模組用（transfer.js 的「開始傳送」也要走同一個廣告規則）
window.FY = Object.assign(window.FY || {}, { adGate: (k, r) => adGate(k, r) });

// ── 會員 ─────────────────────────────────────────
async function refreshMember() {
  try {
    const me = await api('/api/member/me');
    renderWho(me);
    if (me.logged_in) {
      $('#m-guest').hidden = true; $('#m-info').hidden = false;
      const m = me.member || {};
      // 記住「現在是誰登入」→ 付款前後都要核實開通對象（小羅 2026-09-30）
      state.loggedIn = true; state.email = m.email || '';
      const tierName = t('tier_' + (me.tier || 'free'), '');
      $('#m-detail').innerHTML = `
        <div class="r"><div class="k">${t('m_nickname')}</div><div class="v">${esc(me.nickname || t('nickname_unset'))}<button class="linkbtn" id="m-nick-go">${t('nickname_change')}</button></div></div>
        <div class="r"><div class="k">${t('m_email')}</div><div class="v">${esc(m.email)}</div></div>
        <div class="r"><div class="k">${t('m_plan')}</div><div class="v">${tierName}${me.unlimited ? ' ' + t('m_unlimited') : ''}</div></div>
        <div class="r"><div class="k">${t('m_expires')}</div><div class="v">${m.expires_at ? new Date(m.expires_at * 1000).toLocaleDateString() : (me.tier === 'lifetime' ? t('quota_lifetime') : '—')}</div></div>`;
      const nb = $('#m-nick');
      if (nb) nb.value = me.nickname || '';
      return;
    }
  } catch { /* 未登入 */ }
  renderWho(null);
  state.loggedIn = false; state.email = '';
  $('#m-guest').hidden = false; $('#m-info').hidden = true;
}

// 點「更改」→ 捲到暱稱輸入框並聚焦（讓使用者一眼知道去哪裡改）
document.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'm-nick-go') {
    const el = $('#m-nick');
    if (el) { el.focus(); el.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
  }
});

// 儲存暱稱（會員自己改；留空白＝改回用 Email 顯示）
$('#m-nick-save').addEventListener('click', async () => {
  const btn = $('#m-nick-save');
  btn.disabled = true;
  try {
    const j = await api('/api/member/nickname', { method: 'POST',
      body: JSON.stringify({ nickname: $('#m-nick').value }) });
    msg('#m-nick-msg', t('nickname_saved') + (j.nickname ? '' : '（' + t('nickname_empty_hint') + '）'), 'ok');
    await refreshMember();
  } catch (e) { msg('#m-nick-msg', e.message, 'err'); }
  finally { btn.disabled = false; }
});

// 更改密碼（小羅 2026-09-29：「前後台也要有改帳密的按鈕和邏輯」）
$('#m-pw-save').addEventListener('click', async () => {
  const btn = $('#m-pw-save');
  const cur = $('#m-pw-cur').value;
  const nw = $('#m-pw-new').value;
  if (!cur || !nw) { msg('#m-pw-msg', t('password_desc'), 'err'); return; }
  btn.disabled = true;
  try {
    await api('/api/member/password', { method: 'POST',
      body: JSON.stringify({ current: cur, password: nw }) });
    msg('#m-pw-msg', t('password_saved'), 'ok');
    $('#m-pw-cur').value = ''; $('#m-pw-new').value = '';
  } catch (e) { msg('#m-pw-msg', e.message, 'err'); }
  finally { btn.disabled = false; }
});

// ── 忘記密碼 ／ 忘記帳號 ／ 重設密碼（小羅 2026-09-29）─────────────
//   小羅：「會員登錄的時候是不是也應該有一個忘記密碼？
//          這個忘記密碼也要真實有效…忘記密碼跟忘記帳號都要。」
const showForgot = (on) => {
  $('#m-forgot').hidden = !on;
  if (on) { $('#m-findacc').hidden = true; $('#m-forgot-email').focus(); setupPasswordEyes(); }
};
const showFindAcc = (on) => {
  $('#m-findacc').hidden = !on;
  if (on) { $('#m-forgot').hidden = true; $('#m-findacc-nick').focus(); }
};
$('#m-forgot-go').onclick = () => showForgot(true);
$('#m-forgot-back').onclick = () => showForgot(false);
$('#m-findacc-go').onclick = () => showFindAcc(true);
$('#m-findacc-back').onclick = () => showFindAcc(false);
$('#m-forgot-send').onclick = async () => {
  const btn = $('#m-forgot-send');
  const email = $('#m-forgot-email').value.trim();
  if (!email) { msg('#m-forgot-msg', t('member_email'), 'err'); return; }
  btn.disabled = true;
  try {
    const j = await api('/api/member/forgot', { method: 'POST', body: JSON.stringify({ email }) });
    msg('#m-forgot-msg', j.message || '', 'ok');
  } catch (e) { msg('#m-forgot-msg', e.message, 'err'); }
  finally { btn.disabled = false; }
};
$('#m-findacc-do').onclick = async () => {
  const btn = $('#m-findacc-do');
  const nickname = $('#m-findacc-nick').value.trim();
  if (nickname.length < 1) { msg('#m-findacc-msg', t('findacc_ph'), 'err'); return; }
  btn.disabled = true;
  try {
    const j = await api('/api/member/find-account', { method: 'POST', body: JSON.stringify({ nickname }) });
    // 小羅 2026-10-04：不顯示帳號，只告知「已寄到你的信箱」
    msg('#m-findacc-msg', j.message || '', 'ok');
  } catch (e) { msg('#m-findacc-msg', e.message, 'err'); }
  finally { btn.disabled = false; }
};

// 重設密碼：從信件連結進來的（?reset=xxx）→ 自動切到會員頁、顯示設定新密碼
const _resetToken = new URLSearchParams(location.search).get('reset');
if (_resetToken) {
  $('[data-tab="member"]')?.click();
  $('#m-reset').hidden = false;
  const pick = $('#m-login')?.closest('.pick');
  if (pick) pick.hidden = true;
  $('#m-reset-pw')?.focus();
}
$('#m-reset-do').onclick = async () => {
  const btn = $('#m-reset-do');
  const pw = $('#m-reset-pw').value;
  if ((pw || '').length < 6) { msg('#m-reset-msg', t('password_new'), 'err'); return; }
  btn.disabled = true;
  try {
    const j = await api('/api/member/reset', { method: 'POST',
      body: JSON.stringify({ token: _resetToken, password: pw }) });
    msg('#m-reset-msg', j.message || '', 'ok');
    setTimeout(() => { location.href = location.pathname; }, 2000);
  } catch (e) { msg('#m-reset-msg', e.message, 'err'); btn.disabled = false; }
};

// 登出（頂部那顆）
$('#who-out').addEventListener('click', async () => {
  localStorage.removeItem(MKEY);
  await refreshMember();
  await loadQuota();
  go('member');
});
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

// ── 註銷帳號（使用者自己註銷；小羅 2026-09-27 要求警語要講清楚）──
$('#m-delete').addEventListener('click', async () => {
  const m = state.member || {};
  const paid = m.plan && m.plan !== 'free';
  // 依「有沒有付費」給不同的警告（付費的講不退費；沒付費的講要重新申請）
  const warn = paid
    ? t('delete_warn_paid')      // 你目前是付費會員：註銷後費用不退、資格立即失效
    : t('delete_warn_free');     // 註銷後要再用會員功能必須重新申請
  const step1 = confirm(warn);
  if (!step1) return;
  const step2 = confirm(t('delete_confirm_final'));
  if (!step2) return;
  try {
    const j = await api('/api/member/me', { method: 'DELETE' });
    localStorage.removeItem(MKEY);
    msg('#m-msg', j.message || t('delete_done'), 'ok');
    await refreshMember();
    await loadQuota();
  } catch (e) { msg('#m-msg', e.message, 'err'); }
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
function clearTransient() {
  // 一次性的提示訊息（解析中、登入成功…）在換語言時直接清掉，避免殘留舊語言
  ['#status', '#m-msg', '#rp-status'].forEach((sel) => msg(sel, ''));
}
async function loadPlans() {
  try {
    const j = await api('/api/pay/plans');
    Object.entries(j.plans || {}).forEach(([id, p]) => {
      if (p && p.price != null) PLAN_PRICES[id] = p.price;
      const el = document.querySelector(`[data-price="${id}"]`);
      if (el) el.textContent = p.price;
    });
    applyLang();          // 價格載入後重新代入說明文字（{pm}／{pl}）
  } catch { /* 忽略 */ }
}
// ── 付款資訊（小羅 2026-10-03：這區只是「說明支援哪些渠道」，不可點、無連結）──
//   圖標依「開啟的平台」聯動：
//     PayPal 開 → 顯示 PayPal；Stripe 開 → 顯示 VISA/MC/JCB；
//     藍新開 → 顯示 VISA/MC/JCB/銀聯/超商/ATM/Apple Pay
let payProviders = {};   // 目前開啟且可用的平台

async function loadPayWays() {
  const box = $('#pay-icons');
  let cfg;
  try { cfg = await api('/api/pay/providers'); } catch { return; }
  const prov = cfg.providers || {};
  payProviders = {};
  const seen = [];
  Object.entries(prov).forEach(([pid, v]) => {
    if (!v.enabled) return;                 // 以「開關」為準（金鑰未設也先列，按了會提示）
    payProviders[pid] = v;
    (v.channels || []).forEach((c) => { if (!seen.includes(c)) seen.push(c); });
  });
  // 沒開任何平台 → 不顯示圖標（只顯示公測說明）
  if (box) {
    $$('#pay-icons .payic').forEach((el) => {
      el.hidden = !seen.includes(el.dataset.ch);
    });
    box.hidden = seen.length === 0;
  }
  // 付款方式按鈕區（pay-ways）改為純文字提示，不再直接給連結
  const ways = $('#pay-ways');
  if (ways) ways.innerHTML = '';
}

// 平台圖案（小羅 2026-10-03：按鈕要有平台圖案，不要只有文字）
//   直接重用「付款圖標列」(#pay-icons) 的官方 SVG，避免重複定義（零廢碼）
function providerLogo(pid) {
  const box = $('#pay-icons');
  const svg = (ch) => {
    const el = box && box.querySelector('.payic[data-ch="' + ch + '"] svg');
    return el ? el.outerHTML : '';
  };
  if (pid === 'paypal') return svg('paypal');
  if (pid === 'stripe') return svg('visa') + svg('mastercard') + svg('jcb') + svg('amex');
  if (pid === 'newebpay') return svg('visa') + svg('mastercard') + svg('jcb') + svg('unionpay');
  return '';
}

// 第一層彈窗：選擇付款平台（小羅 2026-10-03 指定）
async function chooseProvider() {
  const list = Object.entries(payProviders);
  if (!list.length) return '';
  if (list.length === 1) return list[0][0];   // 只有一家 → 不用選
  return new Promise((resolve) => {
    const el = document.createElement('div');
    el.className = 'paypick';
    el.innerHTML = '<div class="paypick-card"><h3>' + esc(t('pay_choose')) + '</h3>'
      + list.map(([pid, v]) => '<button type="button" class="paypickbtn" data-p="' + esc(pid) + '"'
          + (v.ready ? '' : ' disabled') + '><span class="paypick-logo">' + providerLogo(pid) + '</span>'
          + '<span class="paypick-tag">' + esc(t('pay_prov_' + pid)) + '</span>'
          + (v.ready ? '' : '<span class="dim">' + esc(t('pay_preparing_short')) + '</span>') + '</button>').join('')
      + '<button type="button" class="paypickcancel">' + esc(t('pay_cancel_btn')) + '</button></div>';
    document.body.appendChild(el);
    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-p]');
      if (b) { el.remove(); resolve(b.dataset.p); return; }
      if (e.target.classList.contains('paypickcancel') || e.target === el) { el.remove(); resolve(''); }
    });
  });
}


// ── 付款成功提示（可關閉；手機／電腦都對應）───────────────
function showPaySuccess() {
  const old = document.querySelector('.payok');
  if (old) old.remove();
  const el = document.createElement('div');
  el.className = 'payok';
  el.innerHTML = '<div class="payok-card">'
    + '<div class="payok-emoji">🎉</div>'
    + '<h3>' + esc(t('pay_ok_title')) + '</h3>'
    + '<p>' + esc(t('pay_ok_desc')) + '</p>'
    + '<button type="button" id="payok-close">' + esc(t('pay_ok_btn')) + '</button>'
    + '</div>';
  document.body.appendChild(el);
  el.querySelector('#payok-close').addEventListener('click', () => el.remove());
  setTimeout(() => el.remove(), 15000);
}

// ── PayPal：點「訂閱」→ 開 PayPal 付款頁（彈窗，不離開本站）───────
//   小羅 2026-10-03：① 直接進付款頁（可選信用卡）② 付款成功才開通
//   ③ 金額由後端訂單決定（月 88／終身 988）
async function openPayPal(plan) {
  try {
    const j = await api('/api/pay/checkout', { method: 'POST', body: JSON.stringify({
      plan, provider: 'paypal' }) });
    const url = j.approve_url || j.checkout_url || '';
    if (!url) { msg('#pay-status', t('pay_not_ready'), 'err'); return; }
    msg('#pay-status', t('pay_loading'), 'ok');
    const w = window.open(url, 'paypal_checkout', 'width=540,height=760');
    if (!w) { window.location.href = url; return; }
    // 付款完成後 PayPal 會導回本站（後端確認收款才開通）→ 這裡輪詢確認
    const t0 = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - t0 > 10 * 60 * 1000) { clearInterval(timer); return; }
      try {
        const me = await api('/api/member/me');
        if (me && me.member && me.member.plan && me.member.plan !== 'free') {
          clearInterval(timer);
          msg('#pay-status', t('pay_activated_to'), 'ok');
          try { w.close(); } catch (e) { /* 忽略 */ }
          showPaySuccess();
          loadMe();
        }
      } catch (e) { /* 繼續等 */ }
    }, 3000);
  } catch (e) {
    msg('#pay-status', e.message, 'err');
  }
}

// ── 密碼欄小眼睛（小羅 2026-10-03：要看得出自己打了什麼）──
function setupPasswordEyes() {
  $$('input[type="password"]').forEach((inp) => {
    if (inp.dataset.eye) return;
    inp.dataset.eye = '1';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pweye';
    btn.setAttribute('aria-label', '顯示密碼');
    btn.textContent = '👁';
    inp.parentNode.appendChild(btn);
    btn.addEventListener('click', () => {
      const show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      btn.textContent = show ? '🙈' : '👁';
    });
  });
}
setupPasswordEyes();

// ── 匯率換算：站內浮動小視窗（小羅 2026-10-03 / 2026-10-04）──
//   需求：主頁完全不動、不跳走、不遮整個畫面（右下角浮出小卡）
//   內容：直接嵌入「匯率計算器」讓客人自己輸入金額換算（XE 禁 iframe → 用 x-rates.com）
const FX_URL = 'https://www.x-rates.com/calculator/?from=TWD&to=USD&amount=100';

async function openFx() {
  const old = document.querySelector('.fxcard');
  if (old) { old.remove(); return; }        // 再按一次 = 收起
  const el = document.createElement('div');
  el.className = 'fxcard';
  el.innerHTML = '<div class="fxcard-h"><b>' + esc(t('fx_title')) + '</b>'
    + '<button type="button" class="fxcard-x" aria-label="' + esc(t('fx_close')) + '">×</button></div>'
    + '<div class="fxcard-b"><iframe class="fxfrm" src="' + FX_URL + '" loading="lazy" title="'
    + esc(t('fx_title')) + '"></iframe></div>'
    + '<div class="fxcard-f"><a href="' + FX_URL + '" target="_blank" rel="noopener">'
    + esc(t('fx_open_new')) + '</a></div>';
  document.body.appendChild(el);
  el.querySelector('.fxcard-x').addEventListener('click', () => el.remove());
}
const fxLink = $('#fx-open');
if (fxLink) fxLink.addEventListener('click', (e) => { e.preventDefault(); openFx(); });

$$('[data-buy]').forEach((b) => b.addEventListener('click', async () => {
  // ⚠️ 小羅 2026-09-30：付款前先核實「會開通到哪個帳號」，避免買了卻開給別人
  if (!state.loggedIn) {
    msg('#pay-status', t('pay_need_login'), 'err');
    go('member');
    return;
  }
  const planName = b.dataset.buy === 'lifetime' ? t('plan_lifetime') : t('plan_monthly');
  if (!confirm(t('pay_confirm_to') + '\n\n' + t('m_email') + '：' + state.email
               + '\n' + t('plans_title') + '：' + planName)) return;
  try {
    const provider = await chooseProvider();
    if (!provider) return;                                  // 使用者取消
    // ⓪ PayPal → 付款頁（電腦彈窗；手機另開網頁）
    if (provider === 'paypal') { await openPayPal(b.dataset.buy); return; }
    const j = await api('/api/pay/checkout', { method: 'POST', body: JSON.stringify({
      plan: b.dataset.buy, provider }) });
    // ① 導頁式付款
    if (j.checkout_url) { window.location.href = j.checkout_url; return; }
    // ② QR code 付款（金流商回傳圖片或字串）
    if (j.qr_url || j.qr_code) {
      const qr = $('#pay-qr');
      qr.hidden = false;
      qr.innerHTML = j.qr_url
        ? `<img alt="QR" src="${esc(j.qr_url)}"><div class="note">${t('pay_qr_hint')}</div>`
        : `<canvas id="pay-qr-c" width="200" height="200"></canvas><div class="note">${t('pay_qr_hint')}</div>`;
      msg('#pay-status', t('pay_qr_ready'), 'ok');
      return;
    }
    msg('#pay-status', j.message || t('pay_not_ready'), 'err');
  } catch (e) { msg('#pay-status', e.message, 'err'); }
}));

// ── 啟動 ─────────────────────────────────────────
(async function init() {
  await loadLang();
  try { await loadConfig(); } catch (e) { console.warn(e); }
  await loadQuota();
  // ⚠️ 一定要在啟動時讀登入狀態（小羅 2026-09-29）
  //    「讓客戶在首頁感覺到他登入了」——之前只有點進「會員」分頁才會更新，
  //    所以重整頁面後身分會變回訪客，看起來像沒登入。
  await refreshMember();
  // 付款完成導回（?paid=1）→ 明確告訴他「開通到哪個帳號」（小羅 2026-09-30）
  if (new URLSearchParams(location.search).get('paid') === '1') {
    if (state.loggedIn) {
      msg('#pay-status', t('pay_activated_to') + '：' + state.email, 'ok');
      msg('#m-msg', t('pay_activated_to') + '：' + state.email, 'ok');
    } else {
      msg('#pay-status', t('pay_login_to_check'), 'err');
    }
    history.replaceState(null, '', location.pathname);
  }
  loadAnnouncements();
  loadMyReplies();
  setInterval(pollMyReplies, REPLY_POLL_EVERY);      // 即時輪詢（不用刷新）
  // 「回報問題」被打開（或關起來）就標記已讀 → 數字消失（像未讀訊息）
  const _rbox = document.getElementById('report-box');
  if (_rbox) {
    _rbox.addEventListener('toggle', () => {
      if (_replyItems.length || _replyPending) {
        markRepliesRead();
        renderReplyBar();
      }
    });
  }
  loadPayWays();
  renderHistory();
})();

// 廣告看完按「繼續使用」→ ① 打 API 把次數加回來 ② 更新次數顯示 ③ 接著做剛剛被擋下的動作
$('#ads-continue').addEventListener('click', async () => {
  const box = $('#adsbox');
  if (box) box.hidden = true;
  const p = _adPending || { kind: 'download', resume: null };
  _adPending = null;
  const a = state.quota && state.quota.ads;
  if (a) _adClearedAt = a.used;                   // 先記住，避免 API 失敗時一直彈
  try {
    const j = await api('/api/ads/reward', { method: 'POST', body: JSON.stringify({ kind: p.kind }) });
    if (j && j.quota) renderQuota(j.quota);
    // ⚠️ 2026-09-29 修（小羅：「看完解鎖 3 次、再用完之後沒有再跳廣告，直接說今天用完」）：
    //    看完廣告＝新的一輪開始 → **一定要把「這一輪已看過」的記號清掉**，
    //    不然下一次用完 3（或 5）次時，前台會以為「這一輪已經看過」而直接放行
    //    → 伺服器就回「今天次數用完」，使用者再也看不到廣告、也不能用。
    _adClearedAt = -1;
  } catch { /* 廣告未啟用／不需要看 → 直接放行（保留記號，避免萬一失敗一直彈） */ }
  await loadQuota();
  if (p.resume) p.resume();                       // 放行原本的動作
});
