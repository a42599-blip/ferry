/* 轉運站後台（原生 JS，無框架）*/
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const TKEY = 'fy_admin_token';

const FEATURE_LABELS = {
  'feature.download': '無水印下載',
  'feature.transfer': '無損傳輸',
  'feature.quality': '畫質選擇',
  'feature.audio_only': '純音訊輸出',
  'feature.history': '歷史記錄',
  'feature.free_limit': '免費次數限制',
  'feature.maintenance': '全站維護模式',
  'feature.auth': '會員登入（預留）',
  'feature.billing': '付費／訂閱（預留）',
};

// ── 工具 ────────────────────────────────────────────
const fmtN = (n) => (n === null || n === undefined) ? '–' : Number(n).toLocaleString();
const fmtBytes = (n) => {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
};
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString('zh-TW', { hour12: false }) : '–';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opt = {}) {
  const r = await fetch('/admin/api' + path, {
    ...opt,
    headers: {
      'Content-Type': 'application/json',
      ...(localStorage.getItem(TKEY) ? { Authorization: 'Bearer ' + localStorage.getItem(TKEY) } : {}),
      ...(opt.headers || {}),
    },
  });
  if (r.status === 401) { showLogin(); throw new Error('請重新登入'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || `HTTP ${r.status}`);
  return j;
}

function table(cols, rows) {
  if (!rows || !rows.length) return '<p class="muted">目前沒有資料</p>';
  const head = cols.map((c) => `<th class="${c.num ? 'num' : ''}">${esc(c.t)}</th>`).join('');
  const body = rows.map((r) => '<tr>' + cols.map((c) => {
    const v = typeof c.v === 'function' ? c.v(r) : r[c.v];
    return `<td class="${c.num ? 'num' : ''}">${v === null || v === undefined ? '–' : (c.html ? v : esc(v))}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function rateBadge(rate) {
  if (rate === null || rate === undefined) return '<span class="muted">無資料</span>';
  const cls = rate >= 90 ? 'ok' : rate >= 70 ? 'warn' : 'err';
  return `<span class="badge ${cls}">${rate}%</span>`;
}

// ── 登入 ────────────────────────────────────────────
function showLogin() {
  $('#app').hidden = true;
  $('#login').hidden = false;
  localStorage.removeItem(TKEY);
}

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = $('#login-msg');
  msg.textContent = '登入中…';
  try {
    const j = await api('/login', {
      method: 'POST',
      body: JSON.stringify({
        user: $('#l-user').value, password: $('#l-pass').value, code: $('#l-code').value,
      }),
    });
    if (j.need_code) { $('#code-row').hidden = false; msg.textContent = j.message; return; }
    localStorage.setItem(TKEY, j.token);
    startApp();
  } catch (err) { msg.textContent = err.message; }
});

$('#logout').addEventListener('click', showLogin);

// ── 分頁 ────────────────────────────────────────────
$$('.tab').forEach((t) => t.addEventListener('click', () => {
  $$('.tab').forEach((x) => x.classList.remove('active'));
  $$('.panel').forEach((p) => p.classList.remove('active'));
  t.classList.add('active');
  $('#panel-' + t.dataset.tab).classList.add('active');
  render(t.dataset.tab);
}));
$('#refresh').addEventListener('click', () => render(currentTab()));
$('#days').addEventListener('change', () => render(currentTab()));

const currentTab = () => ($('.tab.active')?.dataset.tab) || 'overview';
const days = () => $('#days').value;

async function startApp() {
  $('#login').hidden = true;
  $('#app').hidden = false;
  try {
    const s = await api('/session');
    if (s.totp_enabled) $('#totp-msg').textContent = '已啟用 ✅';
  } catch { /* 忽略 */ }
  render('overview');
}

// ── 各頁 ────────────────────────────────────────────
async function render(tab) {
  try {
    if (tab === 'overview') return await renderOverview();
    if (tab === 'flags') return await renderFlags();
    if (tab === 'devices') return await renderDevices();
    if (tab === 'revenue') return await renderRevenue();
    if (tab === 'errors') return await renderErrors();
    if (tab === 'system') return await renderSystem();
  } catch (err) {
    console.error(err);
  }
}

async function renderOverview() {
  const d = await api('/overview?days=' + days());
  const s = d.summary;
  $('#kpis').innerHTML = [
    ['進站瀏覽', fmtN(s.page_views)],
    ['不重複訪客', fmtN(s.visitors), `新 ${fmtN(s.new_visitors)} · 回訪 ${fmtN(s.returning_visitors)}`],
    ['解析次數', fmtN(s.resolve_total), `成功 ${fmtN(s.resolve_ok)} · 失敗 ${fmtN(s.resolve_fail)}`],
    ['成功率', s.success_rate === null ? '–' : s.success_rate + '%', '', s.success_rate >= 90 ? 'ok' : s.success_rate >= 70 ? 'warn' : 'err'],
    ['下載次數', fmtN(s.downloads), fmtBytes(s.download_bytes)],
    ['傳輸次數', fmtN(s.transfers), fmtBytes(s.transfer_bytes)],
    ['收益', 'US$ ' + fmtN(s.revenue), '（P6 才接金流）'],
    ['錯誤數', fmtN(s.errors), '失敗解析', s.errors > 0 ? 'warn' : ''],
  ].map(([k, v, sub, cls]) => `<div class="kpi ${cls || ''}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub || ''}</div></div>`).join('');

  // 趨勢
  const all = [...(d.series.page_view || [])];
  const max = Math.max(1, ...all.map((x) => x.count));
  $('#chart-series').innerHTML = all.length
    ? all.map((x) => `<div class="bar" style="height:${Math.round(x.count / max * 100)}%" title="${x.date}: ${x.count}"><b>${x.count}</b><span>${x.date.slice(5)}</span></div>`).join('')
    : '<p class="muted">還沒有資料</p>';

  $('#chart-platforms').innerHTML = table([
    { t: '平台', v: 'platform' }, { t: '次數', v: 'total', num: true },
    { t: '成功', v: 'ok', num: true }, { t: '失敗', v: 'fail', num: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
    { t: '平均耗時', v: (r) => r.avg_ms ? (r.avg_ms / 1000).toFixed(1) + 's' : '–', num: true },
  ], d.platforms);

  $('#chart-countries').innerHTML = table(
    [{ t: '國家', v: 'country' }, { t: '訪客', v: 'visitors', num: true }], d.countries);

  $('#chart-os').innerHTML = table(
    [{ t: '系統', v: 'k' }, { t: '次數', v: 'v', num: true }],
    Object.entries(d.devices_os || {}).map(([k, v]) => ({ k, v })));

  $('#chart-sources').innerHTML = table(
    [{ t: '來源', v: 'referrer' }, { t: '次數', v: 'count', num: true }], d.sources);

  const t = d.transfer;
  $('#chart-transfer').innerHTML = `<table><tbody>${
    [['配對次數', fmtN(t.pairs)], ['完成次數', fmtN(t.done)],
     ['成功率', t.success_rate === null ? '–' : t.success_rate + '%'],
     ['失敗', fmtN(t.fail)], ['總量', fmtBytes(t.total_bytes)]]
      .map(([k, v]) => `<tr><td>${k}</td><td class="num">${v}</td></tr>`).join('')}</tbody></table>`;
}

async function renderFlags() {
  const d = await api('/flags');
  $('#feature-list').innerHTML = Object.entries(d.features).map(([k, v]) => `
    <label class="switch">
      <span>${FEATURE_LABELS[k] || k}<small>${k}</small></span>
      <span class="toggle"><input type="checkbox" data-feature="${k}" ${v ? 'checked' : ''}><span></span></span>
    </label>`).join('');

  $('#platform-list').innerHTML = table([
    { t: '平台', v: (r) => `${esc(r.label)} <small class="muted">${esc(r.id)}</small>`, html: true },
    { t: '狀態', v: (r) => r.enabled ? '<span class="badge ok">啟用</span>' : '<span class="badge err">關閉</span>', html: true },
    { t: '近 7 天次數', v: 'today_total', num: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
    { t: '平均耗時', v: (r) => r.avg_ms ? (r.avg_ms / 1000).toFixed(1) + 's' : '–', num: true },
    { t: '開關', v: (r) => `<span class="toggle"><input type="checkbox" data-platform="${esc(r.id)}" ${r.enabled ? 'checked' : ''}><span></span></span>`, html: true },
  ], d.platforms);
  $('#auto-off').checked = !!d.auto_off;

  $$('#feature-list input[data-feature]').forEach((el) => el.addEventListener('change', saveFeatures));
  $$('#platform-list input[data-platform]').forEach((el) => el.addEventListener('change', savePlatforms));
}

async function saveFeatures() {
  const features = {};
  $$('#feature-list input[data-feature]').forEach((el) => { features[el.dataset.feature] = el.checked; });
  await api('/flags', { method: 'PUT', body: JSON.stringify({ features }) });
}
async function savePlatforms() {
  const platforms = {};
  $$('#platform-list input[data-platform]').forEach((el) => { platforms[el.dataset.platform] = el.checked; });
  await api('/flags', { method: 'PUT', body: JSON.stringify({ platforms, auto_off: $('#auto-off').checked }) });
}
$('#auto-off').addEventListener('change', savePlatforms);
$$('[data-all]').forEach((b) => b.addEventListener('click', async () => {
  await api('/flags/all?on=' + b.dataset.all, { method: 'POST' });
  renderFlags();
}));

async function renderDevices() {
  const d = await api('/devices?days=' + days());
  $('#devices').innerHTML = table([
    { t: '裝置', v: (r) => `<span class="rowlink" data-dev="${esc(r.device_id)}">${esc(String(r.device_id).slice(0, 26))}…</span>`, html: true },
    { t: '國家', v: 'country' }, { t: '系統', v: 'os' }, { t: '瀏覽器', v: 'browser' },
    { t: '解析', v: 'resolves', num: true }, { t: '下載', v: 'downloads', num: true },
    { t: '拜訪', v: 'visits', num: true },
    { t: '最後', v: (r) => fmtTime(r.last_seen) },
  ], d.devices);
  $$('#devices .rowlink').forEach((el) => el.addEventListener('click', () => loadTrace(el.dataset.dev)));
}

async function loadTrace(dev) {
  const d = await api('/devices/' + encodeURIComponent(dev));
  $('#trace-card').hidden = false;
  $('#trace-id').textContent = dev;
  $('#trace').innerHTML = table([
    { t: '時間', v: (r) => fmtTime(r.ts) },
    { t: '事件', v: 'kind' }, { t: '平台', v: 'platform' }, { t: '結果', v: 'result' },
    { t: '畫質', v: 'quality' }, { t: '大小', v: (r) => r.size ? fmtBytes(r.size) : '–', num: true },
    { t: '錯誤', v: 'error_code' }, { t: '路徑', v: 'path' },
  ], d.trace);
}

async function renderRevenue() {
  const d = await api('/revenue?days=' + days());
  const s = d.summary;
  $('#rev-kpis').innerHTML = [
    ['本月收益', 'US$ ' + fmtN(s.month)], ['累計收益', 'US$ ' + fmtN(s.total)],
    ['手續費', 'US$ ' + fmtN(s.fees)], ['訂單數', fmtN(s.orders)],
  ].map(([k, v]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  $('#rev-plans').innerHTML = table([
    { t: '方案', v: 'plan' }, { t: '筆數', v: 'c', num: true }, { t: '金額', v: 'amt', num: true },
  ], s.by_plan);
  $('#rev-orders').innerHTML = table([
    { t: '訂單', v: 'id' }, { t: '方案', v: 'plan' }, { t: '金額', v: 'amount', num: true },
    { t: '狀態', v: 'status' }, { t: '時間', v: (r) => fmtTime(r.created_at) },
  ], d.orders);
}

async function renderErrors() {
  const d = await api('/errors?days=' + days());
  $('#err-top').innerHTML = table([
    { t: '平台', v: 'platform' }, { t: '錯誤碼', v: 'error_code' }, { t: '次數', v: 'count', num: true },
  ], d.top_errors);
  $('#err-plat').innerHTML = table([
    { t: '平台', v: 'platform' }, { t: '總次數', v: 'total', num: true },
    { t: '失敗', v: 'fail', num: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
  ], d.platforms);
  const t = d.transfer;
  $('#err-transfer').innerHTML = `<table><tbody>${
    [['配對次數', fmtN(t.pairs)], ['完成次數', fmtN(t.done)],
     ['失敗', fmtN(t.fail)], ['成功率', t.success_rate === null ? '–' : t.success_rate + '%'],
     ['傳輸總量', fmtBytes(t.total_bytes)]]
      .map(([k, v]) => `<tr><td>${k}</td><td class="num">${v}</td></tr>`).join('')}</tbody></table>`;
}

async function renderSystem() {
  const d = await api('/system');
  $('#sys-info').innerHTML = `<table><tbody>${
    [['出口 IP', d.egress_ip || '取得失敗'], ['資料庫路徑', d.db_path],
     ['資料檔大小', fmtBytes(d.db_size)], ['事件筆數', fmtN(d.events)],
     ['平台數（總）', d.platforms_total], ['兩步驟驗證', d.totp_enabled ? '已啟用 ✅' : '未啟用'],
     ['資料目錄設定', d.data_dir]]
      .map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('')}</tbody></table>
    <p class="muted">Cookies 狀態：${Object.entries(d.cookies || {}).filter(([, v]) => v).map(([k]) => k).join(', ') || '（尚未提供任何平台的 cookies）'}</p>`;

  $('#notify-input').value = (d.notify_emails || []).join(', ');
  $('#totp-msg').textContent = d.totp_enabled ? '已啟用 ✅' : '未啟用。';
}

$('#notify-save').addEventListener('click', async () => {
  const emails = $('#notify-input').value.split(',').map((s) => s.trim()).filter(Boolean);
  await api('/system/notify', { method: 'POST', body: JSON.stringify({ emails }) });
  $('#notify-input').value = emails.join(', ');
  alert('已儲存');
});

$('#totp-on').addEventListener('click', async () => {
  const d = await api('/system/totp?action=enable', { method: 'POST' });
  $('#totp-box').hidden = false;
  $('#totp-secret').textContent = d.secret;
  $('#totp-link').href = d.otpauth;
  $('#totp-msg').textContent = `已啟用。目前驗證碼：${d.current_code}（30 秒後更換）`;
});
$('#totp-off').addEventListener('click', async () => {
  await api('/system/totp?action=disable', { method: 'POST' });
  $('#totp-box').hidden = true;
  $('#totp-msg').textContent = '已停用。';
});

let cleanCount = 0;
$('#clean-preview').addEventListener('click', async () => {
  const d = await api('/data/preview?days=' + $('#clean-days').value);
  cleanCount = d.will_delete;
  $('#clean-msg').textContent = `將刪除 ${fmtN(d.will_delete)} 筆（目前共 ${fmtN(d.total)} 筆）。建議先匯出 CSV 備份。`;
  $('#clean-run').disabled = d.will_delete === 0;
});
$('#clean-run').addEventListener('click', async () => {
  if (!confirm(`確定要刪除 ${cleanCount} 筆事件嗎？此動作無法復原。`)) return;
  const d = await api('/data/cleanup', {
    method: 'POST',
    body: JSON.stringify({ confirm: true, older_than_days: Number($('#clean-days').value) }),
  });
  $('#clean-msg').textContent = `已刪除 ${fmtN(d.deleted)} 筆。`;
  $('#clean-run').disabled = true;
});
$('#export').addEventListener('click', () => {
  const t = localStorage.getItem(TKEY);
  fetch('/admin/api/data/export', { headers: { Authorization: 'Bearer ' + t } })
    .then((r) => r.blob()).then((b) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b);
      a.download = 'ferry-events.csv';
      a.click(); URL.revokeObjectURL(a.href);
    });
});
$('#quota-reset').addEventListener('click', async () => {
  if (!confirm('確定清空所有使用者的今日次數嗎？')) return;
  await api('/quota/reset', { method: 'POST' });
  alert('已清空');
});

// ── 啟動 ────────────────────────────────────────────
(async () => {
  if (!localStorage.getItem(TKEY)) return showLogin();
  try { await api('/session'); startApp(); } catch { showLogin(); }
})();
