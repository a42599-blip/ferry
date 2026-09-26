/* 轉運站 後台（原生 JS）
   頁面：總覽 / 成長趨勢 / 平台開關 / 會員與裝置 / 收益 / 錯誤與告警 / 系統 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const TKEY = 'fy_admin_token';

const FEATURE_LABELS = {
  'feature.download': '無水印下載（主功能）',
  'feature.transfer': '無損傳輸（頁面）',
  'feature.teach': '教學頁',
  'feature.plans': '方案頁',
  'feature.member': '會員頁',
  'feature.report': '回報問題',
  'feature.quality': '畫質選擇',
  'feature.audio_only': '純音訊輸出',
  'feature.history': '歷史記錄',
  'feature.free_limit': '免費次數限制',
  'feature.maintenance': '全站維護模式',
  'feature.auth': '會員登入（預留）',
  'feature.billing': '付費／訂閱（預留）',
};
const SEV = { critical: '嚴重', warn: '警告', info: '一般' };
const KIND = { page_view: '進站', resolve: '解析', download: '下載',
               transfer_pair: '傳輸配對', transfer_done: '傳輸完成',
               signup: '註冊', login: '登入', pay: '付款', notify: '通知' };

// ── 工具 ─────────────────────────────────────────
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
const days = () => $('#days').value;
const pct = (v) => v === null || v === undefined ? '–'
  : `<span class="${v > 0 ? 'up' : v < 0 ? 'dn' : ''}">${v > 0 ? '▲ ' : v < 0 ? '▼ ' : ''}${Math.abs(v)}%</span>`;

function table(cols, rows, empty = '目前沒有資料') {
  if (!rows || !rows.length) return `<p class="note">${empty}</p>`;
  const head = cols.map((c) => `<th class="${c.num ? 'num' : ''}">${esc(c.t)}</th>`).join('');
  const body = rows.map((r) => '<tr>' + cols.map((c) => {
    const v = typeof c.v === 'function' ? c.v(r) : r[c.v];
    const empty2 = (v === null || v === undefined || v === '') ? '–' : v;
    return `<td class="${c.num ? 'num' : ''}">${c.html ? empty2 : esc(empty2)}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function rateBadge(rate) {
  if (rate === null || rate === undefined) return '<span class="dim">–</span>';
  const cls = rate >= 90 ? 'ok' : rate >= 70 ? 'warn' : 'err';
  return `<span class="badge ${cls}">${rate}%</span>`;
}

function kpi(label, value, sub, cls = '') {
  return `<div class="kpi"><div class="l">${label}</div><div class="v num">${value}</div>
    <div class="d ${cls}">${sub || ''}</div></div>`;
}

function chart(series, hiLast = true) {
  if (!series || !series.length) return '<p class="note">還沒有資料</p>';
  const max = Math.max(1, ...series.map((x) => x.c));
  return series.map((x, i) => {
    const h = Math.round(x.c / max * 100);
    const hi = hiLast && i === series.length - 1;
    return `<div class="bar${hi ? ' hi' : ''}" style="height:${Math.max(2, h)}%" title="${x.d}: ${x.c}">
      ${hi ? `<b>${x.c}</b>` : ''}<i>${String(x.d).slice(5)}</i></div>`;
  }).join('');
}

// ── API ──────────────────────────────────────────
const api = async (path, opt = {}) => {
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
};

// ── 登入 ─────────────────────────────────────────
function showLogin() {
  $('#app').hidden = true; $('#login').hidden = false;
  localStorage.removeItem(TKEY);
}
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = $('#login-msg');
  msg.textContent = '登入中…';
  try {
    const j = await api('/login', { method: 'POST', body: JSON.stringify({
      user: $('#l-user').value, password: $('#l-pass').value, code: $('#l-code').value }) });
    if (j.need_code) { $('#l-code').hidden = false; msg.textContent = j.message; return; }
    localStorage.setItem(TKEY, j.token);
    start();
  } catch (err) { msg.textContent = err.message; }
});
$('#logout').addEventListener('click', showLogin);

// ── 分頁 ─────────────────────────────────────────
let curPage = 'overview';
function go(page) {
  curPage = page;
  $$('#side button').forEach((b) => b.classList.toggle('on', b.dataset.p === page));
  $$('.pg').forEach((p) => p.classList.toggle('on', p.id === 'pg-' + page));
  render();
}
$$('#side button').forEach((b) => b.addEventListener('click', () => go(b.dataset.p)));
$('#refresh').addEventListener('click', () => render());
$('#days').addEventListener('change', () => render());

async function render() {
  try {
    if (curPage === 'overview') await pgOverview();
    else if (curPage === 'growth') await pgGrowth();
    else if (curPage === 'flags') await pgFlags();
    else if (curPage === 'devices') await pgDevices();
    else if (curPage === 'revenue') await pgRevenue();
    else if (curPage === 'errors') await pgErrors();
    else if (curPage === 'system') await pgSystem();
  } catch (e) {
    $('#queue').innerHTML = `<span style="color:var(--err)">載入失敗：${esc(e.message)}</span>`;
  }
}

// ── 總覽 ─────────────────────────────────────────
async function pgOverview() {
  const d = await api('/overview?days=' + days());
  const s = d.summary, t = d.transfer;
  $('#ov-kpis').innerHTML = [
    kpi('進站瀏覽', fmtN(s.page_views), `不重複訪客 ${fmtN(s.visitors)}（新 ${fmtN(s.new_visitors)} / 回訪 ${fmtN(s.returning_visitors)}）`),
    kpi('解析次數', fmtN(s.resolve_total), `成功 ${fmtN(s.resolve_ok)} · 失敗 ${fmtN(s.resolve_fail)}　成功率 ${s.success_rate ?? '–'}%`),
    kpi('下載次數', fmtN(s.downloads), fmtBytes(s.download_bytes)),
    kpi('傳輸次數 / 總量', fmtN(s.transfers), `${fmtBytes(s.transfer_bytes)}　成功率 ${t.success_rate ?? '–'}%`),
  ].join('');
  $('#ov-chart').innerHTML = chart(d.series.page_view || []);
  $('#ov-plat').innerHTML = table([
    { t: '平台', v: 'platform' }, { t: '次數', v: 'total', num: true },
    { t: '成功', v: 'ok', num: true }, { t: '失敗', v: 'fail', num: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
    { t: '平均耗時', v: (r) => r.avg_ms ? (r.avg_ms / 1000).toFixed(1) + 's' : '–', num: true },
  ], d.platforms);
  $('#ov-country').innerHTML = table(
    [{ t: '國家', v: 'country' }, { t: '訪客', v: 'visitors', num: true }], d.countries);
  $('#ov-os').innerHTML = table(
    [{ t: '系統', v: 'k' }, { t: '次數', v: 'v', num: true }],
    Object.entries(d.devices_os || {}).map(([k, v]) => ({ k, v })));
  $('#ov-src').innerHTML = table(
    [{ t: '來源', v: 'referrer' }, { t: '次數', v: 'count', num: true }], d.sources);
  queue(`進站 ${fmtN(s.page_views)}　解析 ${fmtN(s.resolve_total)}　下載 ${fmtN(s.downloads)}　傳輸 ${fmtN(s.transfers)}`);
}

// ── 成長趨勢 ─────────────────────────────────────
async function pgGrowth() {
  const d = await api('/growth?days=' + days());
  const f = d.funnel;
  $('#gr-kpis').innerHTML = [
    kpi('不重複訪客', fmtN(d.visitors.current), `${d.period_days} 天　${pct(d.visitors.change_pct)}`, ''),
    kpi('解析次數', fmtN(d.resolve.current), `前一期 ${fmtN(d.resolve.previous)}　${pct(d.resolve.change_pct)}`, ''),
    kpi('下載次數', fmtN(d.download.current), `前一期 ${fmtN(d.download.previous)}　${pct(d.download.change_pct)}`, ''),
    kpi('傳輸次數', fmtN(d.transfer.current), `前一期 ${fmtN(d.transfer.previous)}　${pct(d.transfer.change_pct)}`, ''),
  ].join('');
  $('#gr-chart').innerHTML = chart(d.series.resolve || []);
  const maxN = Math.max(1, f.visitors, f.resolvers, f.downloaders, f.members, f.paid);
  const step = (label, n) => `<div class="fstep"><span class="n">${label}</span>
    <span class="track"><i style="width:${Math.round(n / maxN * 100)}%"></i></span>
    <span class="v">${fmtN(n)}</span></div>`;
  $('#gr-funnel').innerHTML = `<div class="funnel">
    ${step('訪客', f.visitors)}${step('解析成功', f.resolvers)}${step('實際下載', f.downloaders)}
    ${step('註冊會員', f.members)}${step('付費會員', f.paid)}</div>
    <p class="note">轉換率（訪客 → 付費）：${f.visitors ? (f.paid / f.visitors * 100).toFixed(2) : '–'}%</p>`;
  $('#gr-compare').innerHTML = `<div class="metric"><span>期間長度</span><span>${d.period_days} 天 vs 前 ${d.period_days} 天</span></div>` +
    [['進站瀏覽', d.page_view], ['解析', d.resolve], ['下載', d.download], ['傳輸', d.transfer]]
      .map(([n, o]) => `<div class="metric"><span>${n}</span>
        <span>${fmtN(o.current)} <span class="dim">/ ${fmtN(o.previous)}</span> ${pct(o.change_pct)}</span></div>`).join('') +
    `<div class="metric"><span>收益</span><span>US$ ${fmtN(d.revenue.current)} <span class="dim">/ ${fmtN(d.revenue.previous)}</span> ${pct(d.revenue.change_pct)}</span></div>`;
  $('#gr-retention').innerHTML = `<div class="metric"><span>總裝置數</span><span>${fmtN(d.retention.total_devices)}</span></div>
    <div class="metric"><span>回訪裝置</span><span>${fmtN(d.retention.returning)}</span></div>
    <div class="metric"><span>回訪率</span><span>${d.retention.returning_pct ?? '–'}%</span></div>
    <div class="metric"><span>註冊會員</span><span>${fmtN(f.members)}</span></div>
    <div class="metric"><span>付費會員</span><span>${fmtN(f.paid)}</span></div>
    <div class="metric"><span>已付款訂單</span><span>${fmtN(f.orders)}</span></div>`;
  queue(`期間 ${d.period_days} 天　解析 ${fmtN(d.resolve.current)}（${d.resolve.change_pct ?? '–'}%）　回訪率 ${d.retention.returning_pct ?? '–'}%`);
}

// ── 平台開關 ─────────────────────────────────────
async function pgFlags() {
  const d = await api('/flags');
  $('#feature-list').innerHTML = Object.entries(d.features).map(([k, v]) => `
    <div class="switch">
      <span>${FEATURE_LABELS[k] || k}<small>${k}</small></span>
      <button type="button" class="toggle${v ? ' on' : ''}" data-feature="${k}" aria-pressed="${v}"><span></span></button>
    </div>`).join('');
  $('#platform-list').innerHTML = table([
    { t: '平台', v: (r) => `${esc(r.label)} <span class="dim">${esc(r.id)}</span>`, html: true },
    { t: '狀態', v: (r) => r.enabled ? '<span class="badge ok">啟用</span>' : '<span class="badge err">關閉</span>', html: true },
    { t: '近 7 天解析', v: 'today_total', num: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
    { t: '平均耗時', v: (r) => r.avg_ms ? (r.avg_ms / 1000).toFixed(1) + 's' : '–', num: true },
    { t: '開關', v: (r) => `<button type="button" class="toggle${r.enabled ? ' on' : ''}" data-platform="${esc(r.id)}" aria-pressed="${r.enabled}"><span></span></button>`, html: true },
  ], d.platforms);
  $('#auto-off').checked = !!d.auto_off;

  $$('#feature-list .toggle[data-feature]').forEach((el) => el.addEventListener('click', async (e) => {
    e.preventDefault(); e.stopPropagation();
    el.classList.toggle('on');
    el.setAttribute('aria-pressed', el.classList.contains('on'));
    const features = {};
    $$('#feature-list .toggle[data-feature]').forEach((x) => { features[x.dataset.feature] = x.classList.contains('on'); });
    await api('/flags', { method: 'PUT', body: JSON.stringify({ features }) });
    queue('已儲存功能開關（前台 30 秒內生效）');
  }));
  $$('#platform-list .toggle[data-platform]').forEach((el) => el.addEventListener('click', async () => {
    el.classList.toggle('on');
    el.setAttribute('aria-pressed', el.classList.contains('on'));
    const platforms = {};
    $$('#platform-list .toggle[data-platform]').forEach((x) => { platforms[x.dataset.platform] = x.classList.contains('on'); });
    await api('/flags', { method: 'PUT', body: JSON.stringify({ platforms, auto_off: $('#auto-off').checked }) });
    queue('已儲存平台開關（前台圖示會跟著消失／出現）');
  }));
  $$('[data-all]').forEach((b) => b.addEventListener('click', async () => {
    await api('/flags/all?on=' + b.dataset.all, { method: 'POST' });
    pgFlags();
  }));
  queue(`功能 ${Object.keys(d.features).length} 項　平台 ${d.platforms.length} 個　自動關閉：${d.auto_off ? '開' : '關'}`);
}

// ── 會員與裝置 ───────────────────────────────────
async function pgDevices() {
  const d = await api('/devices?days=' + days());
  $('#dev-count').textContent = `共 ${d.devices.length} 台`;
  $('#devices').innerHTML = table([
    { t: '身份', v: (r) => r.kind === 'member'
        ? `<span class="badge ok">${esc(r.kind_label)}</span>${r.member_email ? `<br><span class="dim">${esc(r.member_email)}</span>` : ''}`
        : '<span class="badge">訪客</span>', html: true },
    { t: '裝置 / 帳號', v: (r) => `<span class="rowlink" data-dev="${esc(r.device_id)}">${esc(String(r.device_id).slice(0, 22))}…</span>`, html: true },
    { t: '國家', v: 'country' }, { t: '系統', v: 'os' }, { t: '瀏覽器', v: 'browser' },
    { t: '解析（成功/失敗）', v: (r) => `<b>${fmtN(r.resolve_ok)}</b> / ${fmtN(r.resolve_fail)}<br><span class="dim">共 ${fmtN(r.resolves)} 次</span>`, html: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
    { t: '常用平台', v: 'last_platform' },
    { t: '最近解析的網址', v: (r) => r.last_url
        ? `<a href="${esc(r.last_url)}" target="_blank" rel="noreferrer">${esc(String(r.last_url).slice(0, 40))}${String(r.last_url).length > 40 ? '…' : ''}</a>`
        : '–', html: true },
    { t: '下載', v: 'downloads', num: true },
    { t: '傳輸', v: 'transfers', num: true },
    { t: '拜訪', v: 'visits', num: true },
    { t: '最後活動', v: (r) => fmtTime(r.last_seen) },
  ], d.devices);
  $$('#devices .rowlink').forEach((el) => el.addEventListener('click', () => loadTrace(el.dataset.dev)));
  queue(`裝置 ${d.devices.length} 台　（區間：最近 ${days()} 天）`);
}

async function loadTrace(dev) {
  const d = await api('/devices/' + encodeURIComponent(dev));
  $('#trace-card').hidden = false;
  $('#trace-id').textContent = dev;
  $('#trace').innerHTML = table([
    { t: '時間', v: (r) => fmtTime(r.ts) },
    { t: '事件', v: (r) => KIND[r.kind] || r.kind },
    { t: '平台', v: 'platform' },
    { t: '結果', v: (r) => r.result === 'ok' ? '<span class="badge ok">成功</span>'
        : r.result === 'fail' ? '<span class="badge err">失敗</span>' : '–', html: true },
    { t: '解析/下載的網址', v: (r) => r.url
        ? `<a href="${esc(r.url)}" target="_blank" rel="noreferrer">${esc(String(r.url).slice(0, 56))}${String(r.url).length > 56 ? '…' : ''}</a>`
        : '–', html: true },
    { t: '畫質', v: 'quality' },
    { t: '大小', v: (r) => r.size ? fmtBytes(r.size) : '–', num: true },
    { t: '耗時', v: (r) => r.latency_ms ? (r.latency_ms / 1000).toFixed(1) + 's' : '–', num: true },
    { t: '錯誤', v: 'error_code' },
    { t: '來源', v: (r) => r.referrer ? `<span class="dim">${esc(String(r.referrer).slice(0, 28))}</span>` : '–', html: true },
  ], d.trace);
  $('#trace-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── 收益 ─────────────────────────────────────────
async function pgRevenue() {
  const d = await api('/revenue?days=' + days());
  const s = d.summary;
  $('#rev-kpis').innerHTML = [
    kpi('本期收益', 'US$ ' + fmtN(s.month), `最近 ${days()} 天`),
    kpi('累計收益', 'US$ ' + fmtN(s.total), `手續費 US$ ${fmtN(s.fees)}`),
    kpi('已付款訂單', fmtN(s.orders), ''),
    kpi('付費方案數', fmtN((s.by_plan || []).length), ''),
  ].join('');
  $('#rev-plans').innerHTML = table([
    { t: '方案', v: 'plan' }, { t: '筆數', v: 'c', num: true }, { t: '金額', v: 'amt', num: true },
  ], s.by_plan, '還沒有付費紀錄（金流為 P6 階段）');
  $('#rev-orders').innerHTML = table([
    { t: '訂單', v: 'id' }, { t: '方案', v: 'plan' }, { t: '金額', v: 'amount', num: true },
    { t: '狀態', v: 'status' }, { t: '時間', v: (r) => fmtTime(r.created_at) },
  ], d.orders, '還沒有訂單');
  queue(`本期 US$ ${fmtN(s.month)}　累計 US$ ${fmtN(s.total)}　訂單 ${fmtN(s.orders)} 筆`);
}

// ── 錯誤與告警 ───────────────────────────────────
async function pgErrors() {
  const d = await api('/errors?days=' + days());
  $('#err-top').innerHTML = table([
    { t: '平台', v: 'platform' }, { t: '錯誤碼', v: 'error_code' }, { t: '次數', v: 'count', num: true },
  ], d.top_errors, '沒有失敗紀錄');
  $('#err-plat').innerHTML = table([
    { t: '平台', v: 'platform' }, { t: '總次數', v: 'total', num: true },
    { t: '失敗', v: 'fail', num: true },
    { t: '成功率', v: (r) => rateBadge(r.success_rate), html: true },
  ], d.platforms);

  const t = d.transfer;
  $('#err-transfer').innerHTML = `<div class="metric"><span>配對次數</span><span>${fmtN(t.pairs)}</span></div>
    <div class="metric"><span>完成次數</span><span>${fmtN(t.done)}</span></div>
    <div class="metric"><span>失敗</span><span>${fmtN(t.fail)}</span></div>
    <div class="metric"><span>成功率</span><span>${t.success_rate ?? '–'}%</span></div>
    <div class="metric"><span>傳輸總量</span><span>${fmtBytes(t.total_bytes)}</span></div>`;

  const n = await api('/notify');
  const m = n.monitor || {}, p = n.process || {};
  $('#n-monitor').innerHTML = `<div class="metric"><span>寄送方式</span><span>${n.transport === 'none' ? '未設定（只記錄在後台）' : n.transport}</span></div>
    <div class="metric"><span>收件人</span><span>${(n.recipients || []).join(', ') || '–'}</span></div>
    <div class="metric"><span>記憶體</span><span>${p.memory_mb ? p.memory_mb + ' MB' : '–'}</span></div>
    <div class="metric"><span>開機時間</span><span>${p.uptime_seconds ? Math.round(p.uptime_seconds / 60) + ' 分' : '–'}</span></div>
    <div class="metric"><span>檢查間隔</span><span>${(p.check_interval || 300) / 60} 分鐘</span></div>
    <div class="metric"><span>出口 IP</span><span>${m.egress_ip || '–'}</span></div>
    <div class="metric"><span>最近檢查</span><span>${m.at ? fmtTime(m.at) : '尚未執行'}</span></div>
    <div class="metric"><span>資料庫</span><span>${m.db_mb ?? '–'} MB</span></div>`;

  $('#n-events').innerHTML = Object.entries(n.events).map(([key, meta]) => {
    const on = n.toggles[key], locked = !meta.can_disable, left = n.cooldown[key];
    return `<div class="switch"><span>${esc(meta.title)}<small>${SEV[meta.severity] || ''} · ${key}${left ? ' · 冷卻 ' + left + 's' : ''}</small></span>
      <button type="button" class="toggle${on ? ' on' : ''}" data-notify="${key}" ${locked ? 'disabled' : ''} aria-pressed="${on}"><span></span></button></div>`;
  }).join('');
  $$('#n-events .toggle[data-notify]').forEach((el) => el.addEventListener('click', async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (el.disabled) return;
    el.classList.toggle('on');
    el.setAttribute('aria-pressed', el.classList.contains('on'));
    const toggles = {};
    $$('#n-events .toggle[data-notify]').forEach((x) => { toggles[x.dataset.notify] = x.classList.contains('on'); });
    await api('/notify/toggles', { method: 'PUT', body: JSON.stringify({ toggles }) });
  }));

  $('#n-recent').innerHTML = table([
    { t: '時間', v: (r) => fmtTime(r.ts) }, { t: '事件', v: 'event' },
    { t: '主旨', v: 'subject' },
    { t: '結果', v: (r) => r.ok ? '<span class="badge ok">已寄出</span>' : `<span class="badge err">${esc(r.error || '失敗')}</span>`, html: true },
    { t: '管道', v: 'transport' },
  ], n.recent, '還沒有通知紀錄');
  queue(`失敗 ${fmtN(d.top_errors.reduce((a, x) => a + x.count, 0))} 次　監控每 ${(p.check_interval || 300) / 60} 分鐘　通知管道：${n.transport}`);
}

// ── 系統 ─────────────────────────────────────────
async function pgSystem() {
  const d = await api('/system');
  $('#sys-info').innerHTML = [
    ['出口 IP', d.egress_ip || '取得失敗'],
    ['資料庫路徑', d.db_path],
    ['資料檔大小', fmtBytes(d.db_size)],
    ['事件筆數', fmtN(d.events)],
    ['平台數（總）', d.platforms_total],
    ['兩步驟驗證', d.totp_enabled ? '已啟用 ✅' : '未啟用'],
    ['資料目錄設定', d.data_dir],
  ].map(([k, v]) => `<div class="metric"><span>${k}</span><span>${esc(v)}</span></div>`).join('') +
    `<p class="note">已提供 cookies 的平台：${Object.entries(d.cookies || {}).filter(([, v]) => v).map(([k]) => k).join(', ') || '（尚無）'}</p>`;
  $('#notify-input').value = (d.notify_emails || []).join(', ');
  $('#totp-msg').textContent = d.totp_enabled ? '已啟用 ✅' : '未啟用。';
  queue(`出口 IP ${d.egress_ip || '–'}　事件 ${fmtN(d.events)} 筆　DB ${fmtBytes(d.db_size)}`);
}

$('#notify-save').addEventListener('click', async () => {
  const emails = $('#notify-input').value.split(',').map((s) => s.trim()).filter(Boolean);
  await api('/system/notify', { method: 'POST', body: JSON.stringify({ emails }) });
  queue('已儲存通知收件人');
});
$('#totp-on').addEventListener('click', async () => {
  const d = await api('/system/totp?action=enable', { method: 'POST' });
  $('#totp-box').hidden = false;
  $('#totp-secret').textContent = d.secret;
  $('#totp-msg').textContent = `已啟用。目前驗證碼：${d.current_code}`;
});
$('#totp-off').addEventListener('click', async () => {
  await api('/system/totp?action=disable', { method: 'POST' });
  $('#totp-box').hidden = true; $('#totp-msg').textContent = '已停用。';
});
$('#n-test').addEventListener('click', async () => {
  const d = await api('/notify/test', { method: 'POST', body: JSON.stringify({}) });
  alert(d.ok ? `已寄出（${d.transport}）→ ${(d.to || []).join(', ')}` : `未寄出：${d.note}`);
});
$('#n-digest').addEventListener('click', async () => {
  const d = await api('/notify/digest?days=1', { method: 'POST' });
  alert(d.ok ? `摘要已寄出（${d.transport}）` : `未寄出：${d.note}`);
});
$('#n-run').addEventListener('click', async () => { await api('/monitor/run', { method: 'POST' }); alert('監控已執行'); render(); });

let cleanCount = 0;
$('#clean-preview').addEventListener('click', async () => {
  const d = await api('/data/preview?days=' + $('#clean-days').value);
  cleanCount = d.will_delete;
  $('#clean-msg').textContent = `將刪除 ${fmtN(d.will_delete)} 筆（目前共 ${fmtN(d.total)} 筆）。建議先匯出 CSV。`;
  $('#clean-run').disabled = d.will_delete === 0;
});
$('#clean-run').addEventListener('click', async () => {
  if (!confirm(`確定刪除 ${cleanCount} 筆事件？此動作無法復原。`)) return;
  const d = await api('/data/cleanup', { method: 'POST', body: JSON.stringify({ confirm: true, older_than_days: Number($('#clean-days').value) }) });
  $('#clean-msg').textContent = `已刪除 ${fmtN(d.deleted)} 筆。`;
  $('#clean-run').disabled = true;
});
$('#export').addEventListener('click', () => {
  fetch('/admin/api/data/export', { headers: { Authorization: 'Bearer ' + localStorage.getItem(TKEY) } })
    .then((r) => r.blob()).then((b) => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b); a.download = 'ferry-events.csv';
      a.click(); URL.revokeObjectURL(a.href);
    });
});
$('#quota-reset').addEventListener('click', async () => {
  if (!confirm('確定清空所有使用者的今日次數？')) return;
  await api('/quota/reset', { method: 'POST' });
  queue('已清空今日次數');
});

const queue = (text) => { $('#queue').textContent = text || ''; };

// ── 啟動 ─────────────────────────────────────────
async function start() {
  $('#login').hidden = true; $('#app').hidden = false;
  go('overview');
}
(async () => {
  if (!localStorage.getItem(TKEY)) return showLogin();
  try { await api('/session'); start(); } catch { showLogin(); }
})();
