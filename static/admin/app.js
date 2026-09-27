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
  // 免費次數限制（小羅要求：下載與傳輸**分開**開關；關掉＝該模組無限使用）
  'feature.free_limit_download': '免費次數限制 ── 無水印下載',
  'feature.free_limit_transfer': '免費次數限制 ── 無損傳輸',
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

/**
 * 長條圖（每根柱子都顯示數值與日期）。
 * @param series [{d:'2026-09-27', c:3}, …]
 * @param unit   單位（人／次）→ 顯示在 title 與圖例
 *
 * 天數變多時：柱子自動變窄（flex 均分）→ 就是「慢慢縮小、塞滿整個框」。
 * 但日期標籤會擠 → 超過 16 天就跳著顯示（最後一根一定顯示）。
 */
function chart(series, unit = '次', extra = '') {
  if (!series || !series.length) return '<p class="note">還沒有資料</p>';
  const data = series.filter((x) => x && x.d !== undefined && x.c !== undefined);
  if (!data.length) return '<p class="note">還沒有資料</p>';
  const max = Math.max(1, ...data.map((x) => x.c));
  const n = data.length;
  const skip = n > 16 ? Math.ceil(n / 12) : 1;
  const bars = data.map((x, i) => {
    const h = Math.max(3, Math.round((x.c / max) * 100));
    const hi = i === n - 1;
    const showDate = i % skip === 0 || hi;
    return `<div class="bar${hi ? ' hi' : ''}" style="height:${h}%" title="${x.d}：${x.c} ${unit}">
      <b>${x.c}</b>${showDate ? `<i>${String(x.d).slice(5)}</i>` : ''}</div>`;
  }).join('');
  const total = data.reduce((a, x) => a + x.c, 0);
  return bars +
    `<div class="chart-legend">共 ${fmtN(total)} ${unit}　最高 ${fmtN(max)} ${unit}　（${data[0].d.slice(5)} ～ ${data[n - 1].d.slice(5)}）${extra ? '　' + extra : ''}</div>`;
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
  if (r.status === 401) {
    // 只有「權杖真的無效／過期」才會到這裡（有效期限 30 天）
    showLogin('登入已過期，請重新登入（帳號密碼都是 admin）');
    throw new Error('請重新登入');
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.detail || j.message || `HTTP ${r.status}`);
  return j;
};

// ── 登入 ─────────────────────────────────────────
function showLogin(why) {
  $('#app').hidden = true; $('#login').hidden = false;
  localStorage.removeItem(TKEY);
  prepareLogin();
  if (why) {
    const m = $('#login-msg');
    if (m) m.textContent = why;
  }
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
$('#logout').addEventListener('click', () => {
  const ok = confirm(
    '確定要登出嗎？' + String.fromCharCode(10) + String.fromCharCode(10) +
    '━━━ 登出後要用這組重新登入 ━━━' + String.fromCharCode(10) +
    '帳號：admin' + String.fromCharCode(10) +
    '密碼：Ferry-C2cPqk-1827' + String.fromCharCode(10) + String.fromCharCode(10) +
    '（也記錄在：桌面/記憶目錄_備份/小羅的人設.md）' + String.fromCharCode(10) + String.fromCharCode(10) +
    '按「確定」登出，按「取消」留在後台'
  );
  if (ok) showLogin();
});

// ── 分頁 ─────────────────────────────────────────
let curPage = 'overview';
function go(page) {
  curPage = page;
  $$('#side button').forEach((b) => b.classList.toggle('on', b.dataset.p === page));
  $$('.pg').forEach((p) => p.classList.toggle('on', p.id === 'pg-' + page));
  syncRangeLabel();
  render();
}
$$('#side button').forEach((b) => b.addEventListener('click', () => go(b.dataset.p)));
let _lastUpdate = null;

function stamp() {
  _lastUpdate = new Date().toLocaleTimeString('zh-TW', { hour12: false });
  return _lastUpdate;
}

$('#refresh').addEventListener('click', async () => {
  const btn = $('#refresh');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '更新中…';
  btn.style.borderColor = 'var(--acc)';
  try {
    syncRangeLabel();
    await render();
    btn.textContent = '✔ 已更新';
    queue(`已更新（${stamp()}）　區間：${$('#days').selectedOptions[0].textContent}`);
  } catch (e) {
    btn.textContent = '✖ 失敗';
    queue('更新失敗：' + e.message);
  }
  setTimeout(() => { btn.textContent = old; btn.disabled = false; btn.style.borderColor = ''; }, 1600);
});

/** 把「目前區間」同步到頂欄標籤 */
function syncRangeLabel() {
  const rl = document.getElementById('range-label');
  if (rl) rl.textContent = '· ' + ($('#days').selectedOptions[0]?.textContent || '最近 30 天');
}

$('#days').addEventListener('change', async () => {
  const label = $('#days').selectedOptions[0].textContent;
  syncRangeLabel();
  queue(`切換為「${label}」，載入中…`);
  await render();
  queue(`已切換為「${label}」（${stamp()}）`);
});

async function render() {
  // ⚠️ 先清掉上一頁留下的訊息，否則切頁時會看到「載入失敗」殘留在左下角
  $('#queue').innerHTML = '';
  try {
    if (curPage === 'overview') await pgOverview();
    else if (curPage === 'growth') await pgGrowth();
    else if (curPage === 'flags') await pgFlags();
    else if (curPage === 'devices') await pgDevices();
    else if (curPage === 'revenue') await pgRevenue();
    else if (curPage === 'inbox') await pgInbox();
    else if (curPage === 'errors') await pgErrors();
    else if (curPage === 'system') await pgSystem();
  } catch (e) {
    $('#queue').innerHTML = `<span style="color:var(--err)">載入失敗：${esc(e.message)}</span>`;
  }
}

// ── 回報與額度 ───────────────────────────────────
async function pgInbox() {
  const onlyNew = $('#ib-onlynew').checked;
  const d = await api('/feedback?days=' + days() + '&only_new=' + onlyNew);
  const c = d.counts;
  $('#ib-kpis').innerHTML = [
    kpi('回報總數', fmtN(c.total), `近 ${days()} 天`),
    kpi('未處理', fmtN(c.new), '建議每天清一次'),
    kpi('已處理', fmtN(c.handled), ''),
  ].join('');
  const rows = d.rows || [];
  $('#ib-list').innerHTML = rows.length ? table([
    { t: '時間', v: (r) => esc(r.when) },
    { t: '平台', v: (r) => esc(r.platform || '–') },
    { t: '內容', v: (r) => esc(r.message), html: false },
    { t: '聯絡', v: (r) => esc(r.contact || '–') },
    { t: '連結', v: (r) => r.url ? '<span class="dim">' + esc(r.url.slice(0, 46)) + '</span>' : '–', html: true },
    { t: '狀態', v: (r) => r.handled ? '<span class="badge ok">已處理</span>'
        : '<button class="gh" data-fb="' + r.id + '">標記已處理</button>', html: true },
  ], rows) : '<div class="dim">目前沒有回報 🎉</div>';
  $$('#ib-list [data-fb]').forEach((b) => b.addEventListener('click', async () => {
    await api('/feedback/' + b.dataset.fb + '/handled', { method: 'POST', body: '{}' });
    queue('已標記回報 #' + b.dataset.fb + ' 為已處理');
    pgInbox();
  }));

  const q = await api('/quota');
  $('#ib-quota').innerHTML = (q.rows || []).length ? table([
    { t: '對象', v: (r) => esc(r.subject) },
    { t: '用途', v: (r) => esc(r.kind === 'transfer' ? '無損傳輸' : '下載／解析') },
    { t: '今日用掉', v: (r) => `<b>${fmtN(r.count)}</b> / ${fmtN(r.limit)}`, html: true },
    { t: '剩餘', v: (r) => fmtN(Math.max(0, r.limit - r.count)), num: true },
    { t: '還他一次', v: (r) => `<button class="gh" data-grant="${esc(r.subject)}" data-kind="${esc(r.kind)}">＋1</button>`, html: true },
    { t: '歸零', v: (r) => `<button class="gh" data-reset="${esc(r.subject)}">歸零</button>`, html: true },
  ], q.rows) : '<div class="dim">今天還沒有人使用</div>';
  $$('#ib-quota [data-grant]').forEach((b) => b.addEventListener('click', async () => {
    await api('/quota/grant', { method: 'POST', body: JSON.stringify({
      subject: b.dataset.grant, kind: b.dataset.kind, n: 1 }) });
    queue('已還 ' + b.dataset.grant + ' 一次');
    pgInbox();
  }));
  $$('#ib-quota [data-reset]').forEach((b) => b.addEventListener('click', async () => {
    await api('/quota/reset', { method: 'POST', body: JSON.stringify({ subject: b.dataset.reset }) });
    queue('已歸零 ' + b.dataset.reset);
    pgInbox();
  }));
  $('#ib-reset-all').onclick = async () => {
    if (!confirm('把今天所有人的用量歸零？')) return;
    await api('/quota/reset', { method: 'POST', body: '{}' });
    queue('今日用量已全部歸零');
    pgInbox();
  };
  queue(`回報 ${fmtN(c.total)} 則（未處理 ${fmtN(c.new)}）`);
}
$('#ib-onlynew').addEventListener('change', () => { if (curPage === 'inbox') pgInbox(); });

// ── 總覽 ─────────────────────────────────────────
async function pgOverview() {
  const d = await api('/overview?days=' + days());
  const s = d.summary, t = d.transfer;
  const sec = (ms) => ms ? (ms / 1000).toFixed(1) + 's' : '–';
  $('#ov-kpis').innerHTML = [
    kpi('進站瀏覽', fmtN(s.page_views), `不重複訪客 ${fmtN(s.visitors)}（新 ${fmtN(s.new_visitors)} / 回訪 ${fmtN(s.returning_visitors)}）`),
    // ── 解析：成功率、平均耗時、今日即時 ──
    kpi('解析次數', fmtN(s.resolve_total),
      `成功 ${fmtN(s.resolve_ok)} · 失敗 ${fmtN(s.resolve_fail)}　成功率 <b>${s.success_rate ?? '–'}%</b>`
      + `　平均耗時 <b>${sec(s.resolve_avg_ms)}</b><br>今日 ${fmtN(s.resolve_today)} 次（成功 ${fmtN(s.resolve_today_ok)}）`),
    // ── 下載：成功率、平均大小、今日即時、解析→下載轉換率 ──
    kpi('下載次數', fmtN(s.downloads),
      `成功 ${fmtN(s.download_ok)} · 失敗 ${fmtN(s.download_fail)}　成功率 <b>${s.download_rate ?? '–'}%</b>`
      + `　總量 ${fmtBytes(s.download_bytes)}　平均 ${fmtBytes(s.download_avg_bytes)}<br>`
      + `今日 ${fmtN(s.download_today)} 次 / ${fmtBytes(s.download_today_bytes)}　`
      + `解析→下載 <b>${s.download_only_ratio ?? '–'}%</b>`),
    kpi('傳輸次數 / 總量', fmtN(s.transfers), `${fmtBytes(s.transfer_bytes)}　成功率 ${t.success_rate ?? '–'}%`),
  ].join('');
  // 畫質偏好：知道使用者都選哪個畫質，決定要不要優化那條線路
  const q = s.top_qualities || [];
  if (q.length) {
    $('#ov-quality').innerHTML = table([
      { t: '畫質', v: (r) => esc(r.name) },
      { t: '次數', v: (r) => fmtN(r.n), num: true },
      { t: '佔比', v: (r) => {
          const sum = q.reduce((a, b) => a + b.n, 0) || 1;
          const p = (r.n / sum * 100).toFixed(1);
          return `<div class="bar"><i style="width:${p}%"></i></div> ${p}%`;
        }, html: true },
    ], q);
  } else { $('#ov-quality').innerHTML = '<div class="dim">尚無資料</div>'; }
  // 逐時分佈（近 24 小時）
  const hr = s.hourly || [];
  $('#ov-hourly').innerHTML = hr.length
    ? hr.map((r) => `<div class="hb"><i style="height:${Math.min(100, r.n / Math.max(...hr.map(x => x.n)) * 100)}%" title="${r.h} 小時前：${r.n} 次"></i><span>${r.h}h</span></div>`).join('')
    : '<div class="dim">尚無資料</div>';
  const aud = d.summary.audience || {};
  $('#ov-chart').innerHTML = chart(d.series.page_view || [], '人次',
    `訪客 ${fmtN(aud.visitors)} 人　會員 ${fmtN(aud.members)} 人${aud.paid_members ? '（付費 ' + fmtN(aud.paid_members) + '）' : ''}`);
  // ⚠️ 解析與下載分開看：解析成功不等於下載成功
  $('#ov-plat').innerHTML = table([
    { t: '平台', v: (r) => esc(r.platform) },
    { t: '解析<br><small class="dim">成功 / 失敗</small>', v: (r) =>
        `<b>${fmtN(r.resolve_ok)}</b> / ${fmtN(r.resolve_fail)}<br><small class="dim">共 ${fmtN(r.resolve_total)} 次</small>`, html: true },
    { t: '解析成功率', v: (r) => rateBadge(r.resolve_rate), html: true },
    { t: '下載<br><small class="dim">成功 / 失敗</small>', v: (r) =>
        `<b>${fmtN(r.download_ok)}</b> / ${fmtN(r.download_fail)}<br><small class="dim">共 ${fmtN(r.download_total)} 次</small>`, html: true },
    { t: '下載成功率', v: (r) => rateBadge(r.download_rate), html: true },
    { t: '解析平均耗時', v: (r) => r.avg_ms ? (r.avg_ms / 1000).toFixed(1) + 's' : '–', num: true },
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
  $('#gr-chart').innerHTML = chart(d.series.resolve || [], '次');
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
  // ── 平台排行榜（可交叉比對）──
  const rank = d.platform_ranking || [];
  if (rank.length) {
    const maxR = Math.max(1, ...rank.map((x) => x.resolve_total));
    // 圖表版排行榜（小羅 2026-09-27：一堆文字像亂碼，要看得懂的圖）
    const PNAME = { douyin: '抖音', xigua: '西瓜視頻', tiktok: 'TikTok', bilibili: 'B站',
      weibo: '微博', xiaohongshu: '小紅書', instagram: 'Instagram', facebook: 'Facebook',
      toutiao: '今日頭條', threads: 'Threads', shopee: '蝦皮', x: 'X', youtube: 'YouTube' };
    // 明寫 class 名稱（不要用字串拼接，否則看不出用了哪些樣式）
    const tone = (v) => (v === null ? 'dim' : v >= 90 ? 'ok' : v >= 70 ? 'warn' : 'err');
    const toneBar = (v) => (v === null ? 'b-dim' : v >= 90 ? 'b-ok' : v >= 70 ? 'b-warn' : 'b-err');
    $('#gr-rank').innerHTML = rank.map((r, i) => {
      const w = Math.max(3, Math.round((r.resolve_total / maxR) * 100));
      const rate = r.resolve_rate;
      return `<div class="prow">
        <span class="prk">${i + 1}</span>
        <span class="pn">${esc(PNAME[r.platform] || r.platform || '未辨識')}</span>
        <div class="pbar">
          <i class="${toneBar(rate)}" style="width:${w}%"></i>
          <span class="pt">${fmtN(r.resolve_total)} 次解析　${r.share}%</span>
        </div>
        <span class="pr badge ${tone(rate)}">${rate === null ? '無資料' : rate + '%'}</span>
        <span class="pc">${r.download_per_resolve === null ? '尚未下載'
          : '下載轉換 ' + r.download_per_resolve + '%'}</span>
        ${r.avg_ms ? `<span class="pm2">${(r.avg_ms / 1000).toFixed(1)}s</span>` : ''}
      </div>`;
    }).join('') + `<div class="plegend">
      <span class="badge ok">90%↑ 很好</span>
      <span class="badge warn">70~90% 注意</span>
      <span class="badge err">70%↓ 要修</span>
      <span class="dim">（長條＝解析次數；右邊是成功率、下載轉換率、平均耗時）</span>
    </div>`;

    // 自動洞察：直接指出「哪個平台要修 / 哪個下載體驗要加強」
    const ins = [];
    const weakResolve = rank.filter((r) => r.resolve_total >= 5 && r.resolve_rate !== null && r.resolve_rate < 80);
    const weakDownload = rank.filter((r) => r.resolve_ok >= 5 && r.download_per_resolve !== null && r.download_per_resolve < 25);
    const growing = rank.filter((r) => r.growth_pct !== null && r.growth_pct > 30 && r.resolve_total >= 3);
    const top = rank[0];
    if (top) ins.push(`<div class="row">🔥 <span>最熱門：<b>${esc(top.platform)}</b>（${fmtN(top.resolve_total)} 次，佔 ${top.share}%）</span></div>`);
    if (weakResolve.length) ins.push(`<div class="row warn">⚠️ <span>解析成功率偏低（需修復）：<b>${weakResolve.map((r) => esc(r.platform) + ' ' + r.resolve_rate + '%').join('、')}</b></span></div>`);
    if (weakDownload.length) ins.push(`<div class="row warn">⚠️ <span>解析得到但很少下載（下載體驗待加強）：<b>${weakDownload.map((r) => esc(r.platform) + ' ' + r.download_per_resolve + '%').join('、')}</b></span></div>`);
    if (growing.length) ins.push(`<div class="row">📈 <span>成長最快：<b>${growing.map((r) => esc(r.platform) + ' ▲' + r.growth_pct + '%').join('、')}</b></span></div>`);
    if (!ins.length) ins.push('<div class="row dim">目前資料還少，等累積多一點就會自動指出要加強的平台。</div>');
    $('#gr-insight').innerHTML = ins.join('');
  } else {
    $('#gr-rank').innerHTML = '<p class="note">還沒有解析紀錄</p>';
    $('#gr-insight').innerHTML = '';
  }

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
    { t: '解析（成功/失敗）', v: (r) => `<b>${fmtN(r.resolve_ok)}</b> / ${fmtN(r.resolve_fail)}`, html: true },
    { t: '解析成功率', v: (r) => rateBadge(r.resolve_rate), html: true },
    { t: '下載（成功/失敗）', v: (r) => `<b>${fmtN(r.download_ok)}</b> / ${fmtN(r.download_fail)}`, html: true },
    { t: '下載成功率', v: (r) => rateBadge(r.download_rate), html: true },
    { t: '解析耗時', v: (r) => r.avg_ms ? (r.avg_ms / 1000).toFixed(1) + 's' : '–', num: true },
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
  // ── 收款設定（各金流商要設哪些環境變數）──
  const po = await api('/payout');
  $('#rev-webhook').textContent = location.origin + po.callbacks.webhook;
  $('#rev-return').textContent = location.origin + po.callbacks.return;
  $('#rev-providers').innerHTML = po.providers.map((p) => `
    <div class="metric"><span>${p.ready ? '✅' : '⬜'} ${esc(p.label)}</span>
      <span>${p.ready ? '<b>已設定，可收款</b>'
        : '還缺：' + p.env.filter((e) => !e.set).map((e) => e.key).join('、')}</span></div>`).join('')
    + `<p class="note">設定位置：Railway → ferry → Variables。設好後前台「方案」頁的付款按鈕就會出現。</p>`;

  // ── 提現 ──
  const b = po.summary;
  $('#rev-balance').innerHTML = [
    ['累計收入', 'US$ ' + fmtN(b.gross)],
    ['已提現', 'US$ ' + fmtN(b.paid_out)],
    ['處理中', 'US$ ' + fmtN(b.pending)],
    ['可提餘額', '<b>US$ ' + fmtN(b.available) + '</b>'],
  ].map(([k, v]) => `<div class="metric"><span>${k}</span><span>${v}</span></div>`).join('');
  $('#po-method').innerHTML = Object.entries(po.methods)
    .map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  $('#rev-payouts').innerHTML = po.payouts.length ? table([
    { t: '時間', v: (r) => fmtTime(r.ts) },
    { t: '金額', v: (r) => 'US$ ' + fmtN(r.amount), num: true },
    { t: '方式', v: (r) => esc((po.methods || {})[r.method] || r.method || '–') },
    { t: '備註', v: (r) => esc(r.note || '–') },
    { t: '狀態', v: (r) => r.status === 'done' ? '<span class="badge ok">已撥款</span>'
        : r.status === 'cancelled' ? '<span class="badge">已取消</span>'
        : `<button class="gh" data-po-done="${r.id}">標記已撥款</button>`, html: true },
  ], po.payouts, '還沒有提現紀錄') : '';
  $$('#rev-payouts [data-po-done]').forEach((el) => el.addEventListener('click', async () => {
    await api('/payout/' + el.dataset.poDone + '/status', { method: 'POST', body: JSON.stringify({ status: 'done' }) });
    queue('已標記提現 #' + el.dataset.poDone + ' 為已撥款');
    pgRevenue();
  }));
  $('#po-go').onclick = async () => {
    const amt = Number($('#po-amount').value || 0);
    if (!amt) return alert('請填提現金額');
    if (!confirm(`確定申請提現 US$ ${amt}？`)) return;
    try {
      await api('/payout/request', { method: 'POST', body: JSON.stringify({
        amount: amt, method: $('#po-method').value, note: $('#po-note').value }) });
      $('#po-amount').value = ''; $('#po-note').value = '';
      queue(`已建立提現申請 US$ ${amt}（到金流商後台撥款後回來標記完成）`);
      pgRevenue();
    } catch (e) { alert(e.message); }
  };
  queue(`本期 US$ ${fmtN(s.month)}　累計 US$ ${fmtN(s.total)}　訂單 ${fmtN(s.orders)} 筆　可提 US$ ${fmtN(po.summary.available)}`);
}

// ── 錯誤與告警 ───────────────────────────────────
// 錯誤碼 → 中文（小羅 2026-09-27：「這些錯誤碼我看不懂」）
const ERR_TW = {
  PLATFORM_CHANGED:   '平台改版了（我們的解析程式要更新）',
  PLATFORM_BLOCKED:   '被平台擋住（風控／需要登入 or cookies）',
  PLATFORM_TIMEOUT:   '平台回應太慢，逾時',
  PLATFORM_RATE_LIMITED: '被平台限流（請求太密集）',
  PLATFORM_DISABLED:  '這個平台目前被後台關閉',
  PLATFORM_ERROR:     '平台解析失敗（其他原因）',
  UNSUPPORTED_URL:    '不支援的網址（或連結格式不對）',
  NOT_FOUND:          '找不到這個影片（可能已刪除）',
  BAD_REQUEST:        '請求有誤',
  QUOTA_EXCEEDED:     '免費用次數用完',
  TIMEOUT:            '前端等太久（網路慢或伺服器忙）',
  NETWORK:            '前端連不上伺服器',
  CLIENT_ERROR:       '前端回報的錯誤',
  MAINTENANCE:        '全站維護中',
  FEATURE_DISABLED:   '這個功能目前關閉',
};
const errTw = (code) => ERR_TW[code] || (code ? `其他（${code}）` : '未記錄原因');

async function pgErrors() {
  const d = await api('/errors?days=' + days());
  $('#err-top').innerHTML = table([
    { t: '平台', v: (r) => esc(r.platform || '（未辨識）') },
    { t: '發生什麼事', v: (r) => `<b>${esc(errTw(r.error_code))}</b>`
        + (ERR_TW[r.error_code] ? '' : `<br><span class="dim">原始碼：${esc(r.error_code || '–')}</span>`),
      html: true },
    { t: '次數', v: 'count', num: true },
    { t: '怎麼處理', v: (r) => ({
        PLATFORM_CHANGED: '要修該平台的解析程式',
        PLATFORM_BLOCKED: '等風控解除，或提供 cookies',
        PLATFORM_TIMEOUT: '使用者網路慢，或平台當下很慢',
        PLATFORM_RATE_LIMITED: '降低頻率即可',
        UNSUPPORTED_URL: '確認貼的是正確的分享連結',
        TIMEOUT: '使用者手機網路較慢（非我們問題）',
        NETWORK: '使用者斷線（非我們問題）',
      }[r.error_code] || '觀察即可'), html: false },
  ], d.top_errors, '沒有失敗紀錄 🎉');
  $('#err-plat').innerHTML = table([
    { t: '平台', v: (r) => esc(r.platform) },
    { t: '解析失敗', v: 'resolve_fail', num: true },
    { t: '解析成功率', v: (r) => rateBadge(r.resolve_rate), html: true },
    { t: '下載失敗', v: 'download_fail', num: true },
    { t: '下載成功率', v: (r) => rateBadge(r.download_rate), html: true },
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
  ].map(([k, v]) => `<div class="metric"><span>${k}</span><span>${esc(v)}</span></div>`).join('');
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
  localStorage.setItem('fy_admin_user', $('#l-user').value || 'admin');
  $('#login').hidden = true; $('#app').hidden = false;
  go('overview');
}

/** 登入頁：預填上次帳號 ＋ 顯示帳密提示（小羅常忘記） */
function prepareLogin() {
  const last = localStorage.getItem('fy_admin_user');
  if (last && !$('#l-user').value) $('#l-user').value = last;
  const hint = document.getElementById('pw-hint');
  if (hint) hint.hidden = false;
}
document.getElementById('pw-hint')?.addEventListener('click', () => {
  alert('後台帳密：' + String.fromCharCode(10) + String.fromCharCode(10) +
        '帳號：admin' + String.fromCharCode(10) +
        '密碼：Ferry-C2cPqk-1827' + String.fromCharCode(10) + String.fromCharCode(10) +
        '（記錄在：桌面/記憶目錄_備份/小羅的人設.md）');
});
(async () => {
  if (!localStorage.getItem(TKEY)) return showLogin();
  try { await api('/session'); start(); } catch { showLogin(); }
})();
