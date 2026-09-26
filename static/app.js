/* 轉運站 — 前台（原生 JS，無框架）*/
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = { config: null, info: null, selected: null, tz: Intl.DateTimeFormat().resolvedOptions().timeZone };

// ── 平台（分組＋圖示，順序＝規格書 5-4）────────────────
const PLATFORMS = [
  { id: 'douyin', label: '抖音', logo: '/logos/douyin.png', g: 'cn' },
  { id: 'bilibili', label: 'B站', logo: '/logos/bilibili.png', g: 'cn' },
  { id: 'xiaohongshu', label: '小紅書', logo: '/logos/xiaohongshu.png', g: 'cn' },
  { id: 'xigua', label: '西瓜視頻', logo: '/logos/xigua.png', g: 'cn' },
  { id: 'weibo', label: '微博', logo: '/logos/weibo.png', g: 'cn' },
  { id: 'toutiao', label: '今日頭條', logo: '/logos/toutiao.png', g: 'cn' },
  { id: 'tiktok', label: 'TikTok', logo: '/logos/tiktok.png', g: 'intl' },
  { id: 'instagram', label: 'Instagram', logo: '/logos/instagram.png', g: 'intl' },
  { id: 'facebook', label: 'Facebook', logo: '/logos/facebook.png', g: 'intl' },
  { id: 'x', label: 'X', logo: '/logos/x.png', g: 'intl' },
  { id: 'youtube', label: 'YouTube', logo: '/logos/youtube.png', g: 'intl' },
  { id: 'threads', label: '脆 Threads', logo: '/logos/threads.png', g: 'intl' },
  { id: 'shopee', label: '蝦皮', logo: '/logos/shopee.png', g: 'intl' },
];
const GROUPS = [
  { key: 'cn', title: '中國大陸平台' },
  { key: 'intl', title: '海外平台（含台灣、國際）' },
];

function renderPlatforms(enabled) {
  const on = enabled || {};
  const html = GROUPS.map((g) => `<div class="pgrp"><div class="pgh">${g.title}</div><div class="plist">${
    PLATFORMS.filter((p) => p.g === g.key).map((p) => {
      const off = on[p.id] === false ? ' off' : '';
      return `<span class="pi${off}" title="${p.label}"><img src="${p.logo}" alt="${p.label}"><i>${p.label}</i></span>`;
    }).join('')
  }</div></div>`).join('');
  ['#plats', '#plats-teach'].forEach((sel) => { const el = $(sel); if (el) el.innerHTML = html; });
}

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
  const m = Math.floor(s / 60), ss = String(s % 60).padStart(2, '0');
  return m + ':' + ss;
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const msg = (sel, text, kind = '') => {
  const el = $(sel); if (!el) return;
  el.hidden = false; el.className = 'msg ' + kind; el.textContent = text;
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
      'Content-Type': 'application/json',
      'X-Device-Id': deviceId(),
      'X-Timezone': state.tz,
      ...(memberToken() ? { 'X-Member-Token': memberToken() } : {}),
      ...(opt.headers || {}),
    },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || `HTTP ${r.status}`);
  return j;
};

// ── 分頁 ─────────────────────────────────────────
const TITLES = { download: '無水印下載', transfer: '無損傳輸', teach: '教學', plans: '方案', member: '會員' };
function go(tab) {
  $$('.tab, .foot button, #tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  $$('.panel').forEach((p) => p.classList.toggle('on', p.id === 'p-' + tab));
  $('#ctx').textContent = TITLES[tab] || '';
  if (tab === 'member') { refreshMember(); renderHistory(); }
  if (tab === 'plans') loadPlans();
}
$$('[data-tab]').forEach((b) => b.addEventListener('click', () => go(b.dataset.tab)));

// ── 語言 ─────────────────────────────────────────
let LANGS = {};
async function loadLang() {
  const code = localStorage.getItem('fy_lang') || 'zh-Hant';
  $$('#lang button').forEach((b) => b.classList.toggle('on', b.dataset.lang === code));
  try { LANGS = await (await fetch('/locales/' + code + '.json')).json(); } catch { LANGS = {}; }
  document.documentElement.lang = code;
  $$('[data-i18n]').forEach((el) => { const v = LANGS[el.dataset.i18n]; if (v) el.textContent = v; });
}
$$('#lang button').forEach((b) => b.addEventListener('click', () => {
  localStorage.setItem('fy_lang', b.dataset.lang); loadLang();
}));

// ── 設定 / 次數 ───────────────────────────────────
async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  renderPlatforms(cfg.platforms);
  $('#pl-dl').textContent = cfg.quota?.download_per_day ?? 5;
  $('#pl-tr').textContent = cfg.quota?.transfer_per_day ?? 5;
}
async function loadQuota() {
  try {
    const j = await api('/api/quota');
    const q = j.quota || {};
    const unlimited = q.unlimited || (q.download?.remaining ?? 0) >= 9999;
    if (unlimited) {
      $('#q-left').textContent = '∞';
      $('#q-used').textContent = '–';
      $('#q-reset').textContent = '（付費會員：不限次數）';
    } else {
      $('#q-left').textContent = q.download?.remaining ?? '–';
      $('#q-used').textContent = q.download?.used ?? '–';
      $('#q-reset').textContent = '將於 ' + (q.download?.reset_hint || '當地 00:00') + ' 重置（當地時間）';
    }
  } catch { /* 忽略 */ }
}

// ── 解析 ─────────────────────────────────────────
$('#go').addEventListener('click', doResolve);
$('#url').addEventListener('keydown', (e) => { if (e.key === 'Enter') doResolve(); });

async function doResolve() {
  const url = $('#url').value.trim();
  if (!url) return;
  $('#status').hidden = true;
  $('#go').disabled = true;
  msg('#status', '解析中…');
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
  $('#status').hidden = true;
  const pv = $('#pv');
  const cover = $('#cover');
  cover.onerror = () => { cover.hidden = true; $('#shade').hidden = true; };
  if (info.cover) {
    cover.src = info.cover; cover.hidden = false; $('#shade').hidden = false;
  } else {
    cover.hidden = true; $('#shade').hidden = true;
  }
  $('#pv-title').textContent = info.title || '（無標題）';
  $('#pv-plat').textContent = info.platform || '';
  $('#pv-dur').textContent = fmtDur(info.duration);

  const box = $('#qs');
  box.innerHTML = '';
  state.selected = null;
  const list = info.formats || [];
  list.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'q';
    const tag = f.audio ? '<span class="tag">音訊</span>' : '';
    el.innerHTML = `<span class="lb">${tag}${esc(f.label)}</span><span class="s">${f.size ? fmtSize(f.size) : ''} ${f.ext || ''}</span>`;
    el.addEventListener('click', () => selectFormat(i));
    el.dataset.i = i;
    box.appendChild(el);
  });
  if (!list.length) box.innerHTML = '<div class="q"><span class="lb">沒有可下載的格式</span></div>';
  if (list.length === 1) selectFormat(0);
  else { $('#download').disabled = true; $('#download').textContent = '選擇畫質後下載'; }
}

function selectFormat(i) {
  state.selected = i;
  $$('#qs .q').forEach((el) => el.classList.toggle('on', Number(el.dataset.i) === i));
  $('#download').disabled = false;
  $('#download').textContent = '下載到這台裝置';
}

// ── 下載 ─────────────────────────────────────────
$('#download').addEventListener('click', async () => {
  const f = state.info?.formats?.[state.selected];
  if (!f) return;
  const track = $('#track'), bar = $('#bar'), pm = $('#pm');
  track.hidden = false; pm.hidden = false; bar.style.width = '0%'; $('#pct').textContent = '0%';
  $('#pspeed').textContent = '';
  try {
    if (f.mode === 'direct') {
      const a = document.createElement('a');
      a.href = f.url; a.download = ''; a.rel = 'noreferrer';
      document.body.appendChild(a); a.click(); a.remove();
      $('#pct').textContent = '已開始下載';
    } else if (f.mode === 'proxy') {
      const ext = f.audio ? (f.ext || 'm4a') : (f.ext || 'mp4');
      const q = new URLSearchParams({ src: state.info.source_url, name: (state.info.title || 'video').slice(0, 60) + '.' + ext });
      if (f.audio) q.set('audio', 'true'); else if (f.height) q.set('h', String(f.height));
      const a = document.createElement('a');
      a.href = '/api/download?' + q.toString(); a.rel = 'noreferrer';
      document.body.appendChild(a); a.click(); a.remove();
      $('#pct').textContent = '已開始下載（伺服器代理）';
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
          if (sec > 0.5) $('#pspeed').textContent = fmtSize(got / sec) + '/s';
        }
      }
      const blob = new Blob(chunks);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (state.info.title || 'video').slice(0, 40) + '.' + (f.ext || 'mp4');
      a.click(); URL.revokeObjectURL(a.href);
      $('#pct').textContent = '完成'; $('#pspeed').textContent = fmtSize(got);
    }
    saveHistory(state.info, f);
    api('/api/track/download', { method: 'POST', body: JSON.stringify({
      platform: state.info.platform, quality: f.label, size: f.size || null, mode: f.mode,
    }) }).catch(() => {});
  } catch (err) {
    $('#pspeed').textContent = '失敗：' + err.message;
  }
});

// ── 歷史（存裝置，不上傳）────────────────────────
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
  const list = getHistory(), box = $('#h-list');
  if (!list.length) { box.innerHTML = '<div class="hrow"><span class="dim">還沒有下載記錄</span></div>'; return; }
  box.innerHTML = list.map((h) => `<div class="hrow">
    <img src="${esc(h.cover || '')}" alt="" loading="lazy">
    <div class="m"><div class="t">${esc(h.title)}</div>
    <div class="s">${esc(h.platform)} · ${esc(h.label)}${h.size ? ' · ' + fmtSize(h.size) : ''} · ${new Date(h.at).toLocaleString('zh-TW')}</div></div>
  </div>`).join('');
}
$('#h-clear').addEventListener('click', () => {
  if (confirm('確定清空歷史記錄？')) { localStorage.removeItem(HKEY); renderHistory(); }
});

// ── 會員 ─────────────────────────────────────────
async function refreshMember() {
  try {
    const me = await api('/api/member/me');
    if (me.logged_in) {
      $('#m-guest').hidden = true; $('#m-info').hidden = false;
      const m = me.member || {};
      const planName = { free: '免費', monthly: '月會員', lifetime: '終身會員' }[me.plan] || me.plan;
      $('#m-detail').innerHTML = `
        <div class="r"><div class="k">Email</div><div class="v">${esc(m.email)}</div></div>
        <div class="r"><div class="k">方案</div><div class="v">${planName}${me.unlimited ? '（不限次數）' : ''}</div></div>
        <div class="r"><div class="k">到期</div><div class="v">${m.expires_at ? new Date(m.expires_at * 1000).toLocaleDateString('zh-TW') : '—'}</div></div>`;
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
    msg('#m-msg', '登入成功', 'ok');
    await refreshMember(); await loadQuota();
  } catch (e) { msg('#m-msg', e.message, 'err'); }
});
$('#m-register').addEventListener('click', async () => {
  try {
    const j = await api('/api/member/register', { method: 'POST', body: JSON.stringify({
      email: $('#m-email').value, password: $('#m-pass').value, tz: state.tz }) });
    localStorage.setItem(MKEY, j.token);
    msg('#m-msg', '註冊成功，已自動登入', 'ok');
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
    msg('#rp-status', j.message || '已送出', 'ok');
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
    msg('#pay-status', ready
      ? '金流已設定完成，可開始收款。'
      : '收費功能準備中：程式與訂單流程都已完成，等金流商金鑰設定後即可收款。');
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
