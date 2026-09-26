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
  return (v === undefined || v === null || v === '') ? (fallback !== undefined ? fallback : key) : v;
}
window.FY = { t: (k, d) => t(k, d), lang: () => state.lang };

function applyLang() {
  $$('[data-i18n]').forEach((el) => { const v = t(el.dataset.i18n, el.textContent); if (v) el.textContent = v; });
  $$('[data-i18n-ph]').forEach((el) => { const v = t(el.dataset.i18nPh); if (v) el.placeholder = v; });
  $$('#lang button').forEach((b) => b.classList.toggle('on', b.dataset.lang === state.lang));
  document.documentElement.lang = state.lang;
  document.title = t('brand', '轉運站') + ' · ' + t('dl_title');
  buildTeach();
  buildPlans();
  renderPlatforms(state.config?.platforms, state.config?.enabled_platform_count);
  renderQuota(state.quota);
  renderHistory();
  if (state.info) renderResult(state.info);
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
const FEATURE_TAB = { transfer: 'feature.transfer', teach: 'feature.teach', plans: 'feature.plans', member: 'feature.member' };
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
  if ($('#download')) $('#download').hidden = f['feature.download'] === false;
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
  $$('.tabs button, .foot button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
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

const api = async (url, opt = {}) => {
  const r = await fetch(url, {
    ...opt,
    headers: {
      'Content-Type': 'application/json', 'X-Device-Id': deviceId(), 'X-Timezone': state.tz,
      ...(memberToken() ? { 'X-Member-Token': memberToken() } : {}),
      ...(opt.headers || {}),
    },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || `HTTP ${r.status}`);
  return j;
};

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
  if (unlimited) {
    $('#q-left').textContent = '∞';
    $('#q-used').textContent = '–';
    $('#q-reset').textContent = t('quota_unlimited');
  } else {
    $('#q-left').textContent = q.download?.remaining ?? '–';
    $('#q-used').textContent = q.download?.used ?? '–';
    $('#q-reset').textContent = t('quota_reset_pre') + ' ' + (q.download?.reset_hint || '00:00') + ' ' + t('quota_reset_suf');
  }
}
async function loadQuota() {
  try { renderQuota((await api('/api/quota')).quota); } catch { /* 忽略 */ }
}

// ── 解析 ─────────────────────────────────────────
$('#go').addEventListener('click', doResolve);
$('#url').addEventListener('keydown', (e) => { if (e.key === 'Enter') doResolve(); });

async function doResolve() {
  const url = $('#url').value.trim();
  if (!url) return;
  msg('#status', t('parsing'));
  $('#go').disabled = true;
  try {
    const res = await api('/api/resolve', { method: 'POST', body: JSON.stringify({ url }) });
    state.info = res.data;
    renderResult(res.data);
    loadQuota();
  } catch (err) {
    msg('#status', err.message, 'err');
  } finally {
    $('#go').disabled = false;
  }
}

function renderResult(info) {
  msg('#status', '');
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
    const tag = f.audio ? `<span class="tag">${t('tag_audio')}</span>` : '';
    el.innerHTML = `<span class="lb">${tag}${esc(qlabel(f.label))}</span><span class="s">${f.size ? fmtSize(f.size) : ''} ${f.ext || ''}</span>`;
    el.addEventListener('click', () => selectFormat(i));
    el.dataset.i = i;
    box.appendChild(el);
  });
  if (!list.length) box.innerHTML = `<div class="q"><span class="lb">${t('no_formats')}</span></div>`;
  if (list.length === 1) selectFormat(0);
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

function selectFormat(i) {
  state.selected = i;
  $$('#qs .q').forEach((el) => el.classList.toggle('on', Number(el.dataset.i) === i));
  $('#download').disabled = false;
  $('#download').textContent = t('btn_download');
}

// ── 下載 ─────────────────────────────────────────
$('#download').addEventListener('click', async () => {
  const f = state.info?.formats?.[state.selected];
  if (!f) return;
  const track = $('#track'), bar = $('#bar'), pm = $('#pm');
  track.hidden = false; pm.hidden = false; bar.style.width = '0%'; $('#pct').textContent = '0%'; $('#pspeed').textContent = '';
  try {
    if (f.mode === 'direct') {
      const a = document.createElement('a');
      a.href = f.url; a.download = ''; a.rel = 'noreferrer';
      document.body.appendChild(a); a.click(); a.remove();
      $('#pct').textContent = t('dl_started');
    } else if (f.mode === 'proxy') {
      const ext = f.audio ? (f.ext || 'm4a') : (f.ext || 'mp4');
      const q = new URLSearchParams({ src: state.info.source_url, name: (state.info.title || 'video').slice(0, 60) + '.' + ext });
      if (f.audio) q.set('audio', 'true'); else if (f.height) q.set('h', String(f.height));
      const a = document.createElement('a');
      a.href = '/api/download?' + q.toString(); a.rel = 'noreferrer';
      document.body.appendChild(a); a.click(); a.remove();
      $('#pct').textContent = t('dl_proxy');
    } else {
      const t0 = performance.now();
      const resp = await fetch(f.url, { headers: f.headers || {} });
      const total = Number(resp.headers.get('content-length')) || f.size || 0;
      const reader = resp.body.getReader();
      const chunks = []; let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value); got += value.length;
        if (total) {
          const p = Math.round(got / total * 100);
          bar.style.width = p + '%'; $('#pct').textContent = p + '%';
          const sec = (performance.now() - t0) / 1000;
          if (sec > 0.5) $('#pspeed').textContent = fmtSize(got / sec) + t('tr_per_sec');
        }
      }
      const blob = new Blob(chunks);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (state.info.title || 'video').slice(0, 40) + '.' + (f.ext || 'mp4');
      a.click(); URL.revokeObjectURL(a.href);
      $('#pct').textContent = t('dl_done'); $('#pspeed').textContent = fmtSize(got);
    }
    saveHistory(state.info, f);
    api('/api/track/download', { method: 'POST', body: JSON.stringify({
      platform: state.info.platform, quality: f.label, size: f.size || null,
      mode: f.mode, url: state.info.source_url }) }).catch(() => {});
  } catch (err) {
    $('#pspeed').textContent = t('dl_fail') + err.message;
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
$('#rp-send').addEventListener('click', async () => {
  try {
    const j = await api('/api/report', { method: 'POST', body: JSON.stringify({
      message: $('#rp-msg').value, contact: $('#rp-contact').value }) });
    msg('#rp-status', j.message || 'OK', 'ok');
    $('#rp-msg').value = '';
  } catch (e) { msg('#rp-status', e.message, 'err'); }
});

// ── 方案 ─────────────────────────────────────────
async function loadPlans() {
  try {
    const j = await api('/api/pay/plans');
    Object.entries(j.plans || {}).forEach(([id, p]) => {
      const el = document.querySelector(`[data-price="${id}"]`);
      if (el) el.textContent = p.price;
    });
    const ready = Object.values(j.providers || {}).filter((p) => p.ready).length;
    msg('#pay-status', ready ? t('pay_ready') : t('pay_preparing'));
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
