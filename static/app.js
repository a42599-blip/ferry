/* 轉運站 前端（原生 JS，無框架）*/
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  config: null,
  info: null,
  selected: null,
  langs: {},
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
};

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
  return `${m}:${ss}`;
};
const showStatus = (el, msg, kind = '') => {
  el.hidden = false; el.className = 'status ' + kind; el.textContent = msg;
};
const api = async (url, opt = {}) => {
  const r = await fetch(url, {
    ...opt,
    headers: {
      'Content-Type': 'application/json',
      'X-Device-Id': deviceId(),
      'X-Timezone': state.tz,
      ...(opt.headers || {}),
    },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.message || `HTTP ${r.status}`);
  return j;
};
const deviceId = () => {
  let id = localStorage.getItem('fy_device_id');
  if (!id) { id = crypto.randomUUID(); localStorage.setItem('fy_device_id', id); }
  return id;
};

// ── 語言 ─────────────────────────────────────────
async function loadLangs() {
  const code = localStorage.getItem('fy_lang') || 'zh-Hant';
  $('#lang').value = code;
  try { state.langs = await (await fetch(`/locales/${code}.json`)).json(); } catch { state.langs = {}; }
  applyLang();
}
function t(key) { return state.langs[key] || ''; }
function applyLang() {
  $$('[data-i18n]').forEach((el) => { const v = t(el.dataset.i18n); if (v) el.textContent = v; });
}
$('#lang').addEventListener('change', (e) => {
  localStorage.setItem('fy_lang', e.target.value); loadLangs();
});

// ── 分頁 ─────────────────────────────────────────
$$('.tab').forEach((tab) => tab.addEventListener('click', () => {
  $$('.tab').forEach((x) => x.classList.remove('active'));
  $$('.panel').forEach((p) => p.classList.remove('active'));
  tab.classList.add('active');
  $('#panel-' + tab.dataset.tab).classList.add('active');
  if (tab.dataset.tab === 'history') renderHistory();
}));

// ── 設定（開關／平台／次數）────────────────────────
async function loadConfig() {
  const cfg = await api('/api/config');
  state.config = cfg;
  renderPlatforms(cfg.platforms);
  renderQuotaFromCfg(cfg.quota);
}
function renderPlatforms(plats) {
  const labels = {
    douyin: '抖音', tiktok: 'TikTok', bilibili: 'B站',
    xiaohongshu: '小紅書', instagram: 'Instagram', facebook: 'Facebook',
    xigua: '西瓜視頻', shopee: '蝦皮', weibo: '微博',
    toutiao: '今日頭條', x: 'X', youtube: 'YouTube', threads: '脆 Threads',
  };
  const draw = (box) => {
    if (!box) return;
    box.innerHTML = '';
    Object.entries(plats || {}).forEach(([id, v]) => {
      const el = document.createElement('span');
      el.className = 'plat' + (v.enabled ? '' : ' off');
      el.textContent = labels[id] || id;
      el.dataset.id = id;
      box.appendChild(el);
    });
  };
  draw($('#plats'));
  draw($('#teach-plats'));
}
function renderQuotaFromCfg(q) {
  if (!q) return;
  $('#q-dl').textContent = q.download_per_day ?? '–';
  $('#q-reset').textContent = '每日 00:00 重置';
}

// ── 下載 ─────────────────────────────────────────
$('#go').addEventListener('click', doResolve);
$('#url').addEventListener('keydown', (e) => { if (e.key === 'Enter') doResolve(); });

async function doResolve() {
  const url = $('#url').value.trim();
  if (!url) return;
  const st = $('#status');
  showStatus(st, '解析中…');
  $('#result').hidden = true;
  try {
    const res = await api('/api/resolve', { method: 'POST', body: JSON.stringify({ url }) });
    state.info = res.data;
    renderResult(res.data);
    updateQuota(res.quota);
  } catch (err) {
    showStatus(st, err.message, 'err');
  }
}

function renderResult(info) {
  $('#status').hidden = true;
  $('#cover').src = info.cover || '';
  $('#title').textContent = info.title;
  $('#platform').textContent = info.platform;
  $('#duration').textContent = fmtDur(info.duration);

  const box = $('#formats'); box.innerHTML = '';
  state.selected = null;
  info.formats.forEach((f, i) => {
    const el = document.createElement('div');
    el.className = 'fmt';
    el.innerHTML = `<span class="lb">${f.label}</span>
      <span class="sz">${f.size ? fmtSize(f.size) : ''} ${f.ext || ''}</span>`;
    el.addEventListener('click', () => selectFormat(i));
    el.dataset.i = i;
    box.appendChild(el);
  });
  if (info.formats.length === 1) selectFormat(0);
  $('#result').hidden = false;
  $('#download').disabled = true;
  $('#download').textContent = '選擇畫質後下載';
}

function selectFormat(i) {
  state.selected = i;
  $$('.fmt').forEach((el) => el.classList.toggle('sel', Number(el.dataset.i) === i));
  $('#download').disabled = false;
  $('#download').textContent = '下載 ' + state.info.formats[i].label;
}

$('#download').addEventListener('click', async () => {
  const f = state.info?.formats?.[state.selected];
  if (!f) return;
  const prog = $('#prog'), bar = prog.querySelector('.bar'), pct = prog.querySelector('.pct');
  prog.hidden = false; bar.style.width = '0%'; pct.textContent = '0%';
  try {
    if (f.mode === 'direct') {
      // CDN 擋 CORS → 用 <a download> 直連（零流量）
      const a = document.createElement('a');
      a.href = f.url; a.download = ''; a.rel = 'noreferrer';
      document.body.appendChild(a); a.click(); a.remove();
      pct.textContent = '已開始下載';
    } else if (f.mode === 'proxy') {
      // CDN 擋 Origin（YouTube…）→ 伺服器代理，串流不落地
      const ext = f.audio ? (f.ext || 'm4a') : (f.ext || 'mp4');
      const q = new URLSearchParams({
        src: state.info.source_url,
        name: `${state.info.title.slice(0, 60)}.${ext}`,
      });
      if (f.audio) q.set('audio', 'true');
      else if (f.height) q.set('h', String(f.height));
      const a = document.createElement('a');
      a.href = `/api/download?${q.toString()}`;
      a.rel = 'noreferrer';
      document.body.appendChild(a); a.click(); a.remove();
      pct.textContent = '已開始下載（伺服器代理）';
    } else {
      // fetch → blob（零流量、有進度、可自訂檔名）
      const resp = await fetch(f.url, { headers: f.headers || {} });
      const total = Number(resp.headers.get('content-length')) || f.size || 0;
      const reader = resp.body.getReader();
      const chunks = []; let got = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value); got += value.length;
        if (total) { const p = Math.round(got / total * 100); bar.style.width = p + '%'; pct.textContent = p + '%'; }
      }
      const blob = new Blob(chunks);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${state.info.title.slice(0, 40)}.${f.ext || 'mp4'}`;
      a.click(); URL.revokeObjectURL(a.href);
      pct.textContent = '完成';
    }
    saveHistory(state.info, f);
  } catch (err) {
    pct.textContent = '失敗：' + err.message;
  }
});

function updateQuota(q) {
  if (!q) return;
  $('#q-dl').textContent = q.unlimited ? '∞' : q.remaining;
}

// ── 歷史記錄（存使用者瀏覽器，我們零儲存）──────────
const HKEY = 'fy_history';
const getHistory = () => { try { return JSON.parse(localStorage.getItem(HKEY) || '[]'); } catch { return []; } };
function saveHistory(info, fmt) {
  const list = getHistory();
  const entry = {
    title: info.title, platform: info.platform, cover: info.cover,
    label: fmt.label, size: fmt.size || null, at: Date.now(),
  };
  const i = list.findIndex((h) => h.title === info.title && h.platform === info.platform);
  if (i >= 0) list.splice(i, 1);
  list.unshift(entry);
  const limit = state.config?.history_limit || 50;
  localStorage.setItem(HKEY, JSON.stringify(list.slice(0, limit)));
}
function renderHistory() {
  const list = getHistory(), box = $('#h-list');
  box.innerHTML = '';
  if (!list.length) { box.innerHTML = '<li>還沒有下載記錄</li>'; return; }
  list.forEach((h) => {
    const li = document.createElement('li');
    li.innerHTML = `<img src="${h.cover || ''}" alt="">
      <div class="h-meta">
        <div class="h-title">${h.title}</div>
        <div class="h-sub">${h.platform} · ${h.label} · ${h.size ? fmtSize(h.size) : ''} · ${new Date(h.at).toLocaleString()}</div>
      </div>`;
    // 注意：不可點擊重新下載（規格：只剩紀錄）
    box.appendChild(li);
  });
}
$('#h-clear').addEventListener('click', () => {
  if (confirm('確定清空歷史記錄？')) { localStorage.removeItem(HKEY); renderHistory(); }
});

// ── 啟動 ─────────────────────────────────────────
(async function init() {
  await loadLangs();
  try { await loadConfig(); } catch (e) { console.warn(e); }
})();
