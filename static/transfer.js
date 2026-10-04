/* 轉運站 — 無損傳輸（WebRTC DataChannel）
   檔案在兩台裝置之間點對點直傳：不經我們的伺服器、不暫存、不壓縮、不改檔。
   我們只做兩件事：發 6 位配對碼、交換 WebRTC 名片（SDP/ICE）。 */
'use strict';

(() => {
  const $ = (s) => document.querySelector(s);
  if (!$('#p-transfer')) return;
  const t = (k, d) => (window.FY?.t ? window.FY.t(k, d) : d) || d || k;

  // ⚠️ 只有 STUN 的話，遇到手機電信網路（CGNAT）或不同網路時常常連不上，
  //    使用者看到的就是「配對成功但一直沒傳」。所以一定要有 TURN 中繼。
  const ICE = { iceServers: [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' },
    // 免費公開 TURN（Metered OpenRelay）—— 連不上時的最後手段
    { urls: 'turn:openrelay.metered.ca:80', username: 'openrelayproject', credential: 'openrelayproject' },
    { urls: 'turn:openrelay.metered.ca:443', username: 'openrelayproject', credential: 'openrelayproject' },
    { urls: 'turn:openrelay.metered.ca:443?transport=tcp', username: 'openrelayproject', credential: 'openrelayproject' },
  ], iceCandidatePoolSize: 4 };
  const CHUNK = 64 * 1024;
  const HIGH_WATER = 8 * 1024 * 1024;
  const LOW_WATER = 1 * 1024 * 1024;

  const S = {
    files: [], code: null, peer: null, pc: null, dc: null, poll: null,
    sending: false, receiving: null, speed: { bytes: 0, at: 0, timer: null }, known: [],
    //: 收到但還沒辦法加入的 ICE 候選（要等 setRemoteDescription 之後才加）
    pendingIce: [], connectTimer: null,
    //: 被取消的傳送索引／接收中的檔案（小羅 2026-09-29：卡住或傳錯要能取消）
    cancelIdx: new Set(), cancelledIds: new Set(), sendIds: {},
    //: 最後一次「有動作」的時間（閒置 5 分鐘要自動斷開；輪詢不算動作）
    lastAct: Date.now(), idleTimer: null,
  };

  //: 閒置多久自動斷開（小羅 2026-09-29 指定：5 分鐘）
  const IDLE_MS = 5 * 60 * 1000;
  const LEAVE_GRACE = 6;          // 關頁面時給的寬限秒數（重新整理會馬上回來 → 不算離開）
  const touch = () => { S.lastAct = Date.now(); };

  const deviceId = () => {
    let id = localStorage.getItem('fy_device_id');
    if (!id) { id = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)); localStorage.setItem('fy_device_id', id); }
    return 'dev:' + id;
  };
  const fmtBytes = (n) => {
    if (!n && n !== 0) return '–';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 && i > 0 ? 1 : 0) + ' ' + u[i];
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const status = (t, kind = '') => {
    const el = $('#tr-status'); el.hidden = false; el.className = 'msg ' + kind; el.textContent = t;
  };

  S.peerId = deviceId();

  // ── 上次配對的裝置（自動重連用；只存裝置 id，不存任何檔案／帳號資訊）──
  const LAST_KEY = 'fy_last_peer';
  const rememberLast = () => { try { if (S.peer) localStorage.setItem(LAST_KEY, S.peer); } catch { /* 忽略 */ } };
  const forgetLast = () => { try { localStorage.removeItem(LAST_KEY); } catch { /* 忽略 */ } };

  // ── 選檔 ─────────────────────────────────────────
  function addFiles(list) {
    touch();
    for (const f of list) {
      const name = f.webkitRelativePath || f.name;
      if (S.files.some((x) => x.name === name && x.size === f.size)) continue;
      S.files.push({ file: f, name, size: f.size });
    }
    renderFiles();
  }
  const ICONS = { mp4: '🎬', mov: '🎬', mkv: '🎬', mp3: '🎵', m4a: '🎵', wav: '🎵', flac: '🎵',
                  jpg: '🖼️', jpeg: '🖼️', png: '🖼️', heic: '🖼️', gif: '🖼️', webp: '🖼️',
                  pdf: '📄', doc: '📄', docx: '📄', xlsx: '📊', zip: '🗜️', rar: '🗜️', '7z': '🗜️' };
  function renderFiles() {
    const box = $('#tr-list');
    if (!S.files.length) box.innerHTML = `<li class="fr"><span class="dim">${t('tr_no_files')}</span></li>`;
    else {
      box.innerHTML = S.files.map((f, i) => {
        const ext = (f.name.split('.').pop() || '').toLowerCase();
        return `<li class="fr">
          <span class="th">${ICONS[ext] || '📦'}</span>
          <span class="nm">${esc(f.name)}</span>
          <span class="sz">${fmtBytes(f.size)}</span>
          <button class="rm" data-rm="${i}" title="移除">✕</button></li>`;
      }).join('');
      box.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', () => {
        S.files.splice(Number(b.dataset.rm), 1); renderFiles();
      }));
    }
    const total = S.files.reduce((a, f) => a + f.size, 0);
    $('#tr-total').textContent = S.files.length ? `（${S.files.length} ${t('tr_n_items')} · ${fmtBytes(total)}）` : '';
    $('#tr-send').disabled = !S.files.length || !S.dc || S.dc.readyState !== 'open';
  }

  const openPicker = (id) => { const el = $(id); el.value = ''; el.click(); };
  $('#pick-files').addEventListener('click', () => openPicker('#in-files'));
  $('#pick-folder').addEventListener('click', () => openPicker('#in-folder'));
  $('#pick-photo').addEventListener('click', () => openPicker('#in-photo'));
  $('#in-files').addEventListener('change', (e) => addFiles(e.target.files));
  $('#in-folder').addEventListener('change', (e) => addFiles(e.target.files));
  $('#in-photo').addEventListener('change', (e) => addFiles(e.target.files));

  const rack = $('#rack');
  ['dragenter', 'dragover'].forEach((ev) => rack.addEventListener(ev, (e) => {
    e.preventDefault(); rack.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((ev) => rack.addEventListener(ev, (e) => {
    e.preventDefault(); rack.classList.remove('over');
  }));
  rack.addEventListener('drop', async (e) => {
    const items = e.dataTransfer?.items;
    if (items && items.length && items[0].webkitGetAsEntry) {
      const files = [];
      const walk = async (entry, path = '') => {
        if (entry.isFile) {
          const f = await new Promise((res, rej) => entry.file(res, rej));
          Object.defineProperty(f, 'webkitRelativePath', { value: path + f.name });
          files.push(f);
        } else if (entry.isDirectory) {
          const reader = entry.createReader();
          const ents = await new Promise((res) => reader.readEntries(res, () => res([])));
          for (const en of ents) await walk(en, path + entry.name + '/');
        }
      };
      for (const it of items) { const en = it.webkitGetAsEntry?.(); if (en) await walk(en); }
      if (files.length) addFiles(files);
    } else if (e.dataTransfer?.files) addFiles(e.dataTransfer.files);
  });

  // ── signaling ────────────────────────────────────
  const api = async (path, opt = {}) => {
    // ⚠️ 一定要帶裝置 ID／時區（跟 app.js 同一組），否則伺服器會當成另一台裝置
    //    → 免費次數會扣錯人、時區也會算錯（2026-09-27 實際踩到）
    const base = window.FY_HEADERS ? window.FY_HEADERS() : { 'Content-Type': 'application/json' };
    const r = await fetch('/api/signal' + path, {
      ...opt, headers: { ...base, ...(opt.headers || {}) },
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.message || j.detail || `HTTP ${r.status}`);
    return j;
  };

  async function join(code) {
    const j = await api('/join', { method: 'POST', body: JSON.stringify({
      code: code || null, peer_id: S.peerId, name: navigator.platform }) });
    S.code = j.code; S.known = j.known || [];

    // ⚠️ 配對碼的角色不一樣（小羅 2026-09-27 反映「兩邊的數字怎麼一樣」）：
    //   產生方 → 要顯示號碼讓對方輸入
    //   加入方 → 不要顯示那組號碼（會被誤會成自己也要分享），改成「已連上」
    if (code) {
      $('#codebox').hidden = true;
      $('#joined-note').hidden = false;
      $('#joined-note').textContent = t('tr_joined_note');
    } else {
      $('#mycode').textContent = j.code;
      $('#codebox').hidden = false;
      $('#joined-note').hidden = true;
    }
    renderKnown();
    touch();
    if (j.peers?.length) { S.peer = j.peers[0]; rememberLast(); onPeerFound(); }
    else { status(t('tr_waiting')); startPoll(); }
  }

  function renderKnown() {
    const box = $('#known');
    if (!S.known?.length) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="lbl" style="margin-bottom:6px">' + t('tr_known') + '</div>' + S.known.map((p, i) =>
      `<button class="big gh sm" style="margin-top:6px" data-peer="${esc(p)}">${esc(String(p).slice(0, 22))}… ${t('tr_reconnect')}</button>`).join('');
    box.querySelectorAll('[data-peer]').forEach((b) => b.addEventListener('click', async () => {
      if (S.sending) { status(t('tr_busy_switch'), 'err'); return; }   // 傳輸中就別換
      try {
        // ⚠️ 換一台之前先離開舊配對（一次只配一台；不然舊房間會殘留幽靈裝置）
        if (S.code && S.peer && S.peer !== b.dataset.peer) await leavePair(0);
        const j = await api('/pair', { method: 'POST', body: JSON.stringify({ peer_id: S.peerId, target: b.dataset.peer }) });
        S.code = j.code; S.peer = j.peers[0];
        $('#mycode').textContent = j.code; $('#codebox').hidden = false;
        rememberLast();
        touch();
        onPeerFound();
      } catch (err) { status(err.message, 'err'); }
    }));
  }

  /** 自動重連上次的裝置（小羅：「同一個裝置回來時自動重連，不用再點一次配對」）。
   *  ⚠️ 對方不在線上／已經過期 → 安靜回到「還沒配對」，不吵使用者。 */
  let _autoTried = false;
  async function autoReconnect() {
    if (_autoTried || S.code || S.peer) return;
    _autoTried = true;
    let target = '';
    try { target = localStorage.getItem(LAST_KEY) || ''; } catch { target = ''; }
    if (!target) return;
    status(t('tr_auto_reconnecting'));
    try {
      if (S.code) await leavePair(0);          // 換一台前先離開舊的
      const j = await api('/pair', { method: 'POST', body: JSON.stringify({ peer_id: S.peerId, target }) });
      S.code = j.code; S.peer = j.peers[0];
      $('#mycode').textContent = j.code; $('#codebox').hidden = false;
      touch();
      onPeerFound();
    } catch {
      forgetLast();                     // 對方不在線上（或已過期）→ 不要再一直試
      status(t('tr_auto_failed'));
    }
  }

  // 打開「傳輸」分頁時試一次（使用者本來就是來傳檔的，才不會白連）
  document.addEventListener('click', (e) => {
    const tab = e.target.closest && e.target.closest('[data-tab="transfer"]');
    if (tab) setTimeout(autoReconnect, 400);
  });
  // 載入時若本來就在「傳輸」分頁（例如重新整理）→ 也自動重連
  if (document.querySelector('#p-transfer')?.classList.contains('on')) setTimeout(autoReconnect, 800);

  $('#tr-gen').addEventListener('click', async () => {
    if (S.sending) return status(t('tr_busy_switch'), 'err');
    try {
      if (S.code) await leavePair(0);          // 重新產生＝換一台
      await join(null);
    } catch (e) { status(e.message, 'err'); }
  });
  $('#tr-join').addEventListener('click', async () => {
    const code = ($('#join-code').value || '').trim();
    if (code.length !== 6) return status(t('tr_enter6'), 'err');
    if (S.sending) return status(t('tr_busy_switch'), 'err');
    try {
      if (S.code) await leavePair(0);          // 換一台前先離開舊的
      await join(code);
    } catch (e) { status(e.message, 'err'); }
  });
  $('#join-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#tr-join').click(); });

  // 小羅 2026-10-05：手動「斷開配對」（不用關網頁、不用刷開）
  $('#tr-leave').addEventListener('click', async () => {
    if (S.sending || S.receiving) { status(t('tr_busy_stop'), 'err'); return; }
    await leavePair(0);
    renderPeers(t('tr_left_ok'));
    renderKnown();
    status(t('tr_left_ok'), 'ok');
  });

  /** 顯示「這台裝置／對方裝置」與連線狀態（使用者才知道接對了沒） */
  function renderPeers(state) {
    const box = $('#peerbox');
    if (!box) return;
    box.hidden = !(S.peerId || S.peer);
    $('#pv-me').textContent = (S.myName || S.peerId || '–').slice(0, 24);
    $('#pv-other').textContent = S.peer ? String(S.peer).slice(0, 24) : t('tr_none_yet');
    if (state) {
      $('#pv-state').textContent = state;
      $('#pv-dot').classList.toggle('on', state === t('tr_connected'));
    }
  }

  function startPoll() {
    stopPoll();
    S.poll = setInterval(async () => {
      if (!S.code) return;
      try {
        const j = await api(`/poll?code=${S.code}&peer_id=${encodeURIComponent(S.peerId)}`);
        // ⚠️ 2026-09-29（小羅：「對方關掉網頁要自動斷開」）：對方不在名單裡＝已經離開
        if (S.peer && Array.isArray(j.peers) && !j.peers.includes(S.peer)) {
          try { S.dc?.close(); } catch { /* 忽略 */ }
          try { S.pc?.close(); } catch { /* 忽略 */ }
          S.dc = null; S.pc = null; S.peer = null; S.sending = false; S.receiving = null;
          $('#tr-send').disabled = true;
          renderPeers(t('tr_peer_left'));      // 配對狀態列也寫，訊息不會被後面的連線訊息蓋掉
          status(t('tr_peer_left'), 'err');
        }
        if (!S.peer && j.peers?.length) { S.peer = j.peers[0]; rememberLast(); onPeerFound(); }
        for (const m of j.messages || []) await handleSignal(m);
      } catch (e) {
        if (String(e.message).includes('過期') || String(e.message).includes('不存在')) {
          stopPoll(); status(t('tr_expired'), 'err');
        }
      }
    }, 900);
  }
  function stopPoll() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

  /** 主動離開配對（清乾淨，回到「還沒配對」的狀態）。 */
  async function leavePair(grace = 0) {
    if (!S.code) return;
    try {
      await api('/leave', { method: 'POST', body: JSON.stringify({
        peer_id: S.peerId, code: S.code, grace }) });
    } catch { /* 忽略 */ }
    if (grace) return;                       // 只是關頁面：先不要清 UI（寬限期內可能又回來）
    stopPoll();
    try { S.dc?.close(); } catch { /* 忽略 */ }
    try { S.pc?.close(); } catch { /* 忽略 */ }
    S.dc = null; S.pc = null; S.peer = null; S.code = null; S.sending = false;
    S.receiving = null; S.cancelIdx.clear(); S.sendIds = {};
    forgetLast();
    $('#tr-send').disabled = true;
    // 小羅 2026-10-05：斷開後「已連上」絲框（joined-note）與我的配對碼區
    // 必須一起清掉 —— 不然綠色框還在，會讓人誤會「還連著」。
    const jn = $('#joined-note'); if (jn) jn.hidden = true;
    const cb = $('#codebox'); if (cb) cb.hidden = true;
    renderPeers(t('tr_none_yet'));
  }

  /** 關掉網頁（不是重新整理）→ 帶寬限通知伺服器；重新整理會在幾秒內回來，所以不算離開。 */
  window.addEventListener('pagehide', () => {
    if (!S.code) return;
    try {
      const base = window.FY_HEADERS ? window.FY_HEADERS() : {};
      fetch('/api/signal/leave', {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', ...base },
        body: JSON.stringify({ peer_id: S.peerId, code: S.code, grace: LEAVE_GRACE }),
      }).catch(() => {});
    } catch { /* 忽略 */ }
  });

  /** 閒置檢查：5 分鐘沒有動作（沒有傳、沒有收、沒有操作）→ 自動斷開。 */
  function startIdleWatch() {
    if (S.idleTimer) return;
    S.idleTimer = setInterval(() => {
      if (!S.code || !S.peer) return;
      if (S.sending || S.receiving) return;                  // 正在傳＝有動作
      if (Date.now() - S.lastAct < IDLE_MS) return;
      status(t('tr_idle_off'), 'err');
      leavePair(0);
      renderPeers(t('tr_idle_off'));
      renderKnown();
    }, 30000);
  }
  startIdleWatch();

  const sendSignal = (payload) => api('/send', { method: 'POST', body: JSON.stringify({
    code: S.code, from_peer: S.peerId, to_peer: S.peer, payload }) }).catch(() => {});

  async function handleSignal(m) {
    const p = m.payload || {};
    if (!S.pc && p.type === 'offer') await createPeer(false);
    if (!S.pc) return;
    if (p.type === 'offer') {
      await S.pc.setRemoteDescription(new RTCSessionDescription(p.sdp));
      await flushIce();
      const ans = await S.pc.createAnswer();
      await S.pc.setLocalDescription(ans);
      await sendSignal({ type: 'answer', sdp: S.pc.localDescription });
    } else if (p.type === 'answer') {
      if (S.pc.signalingState !== 'stable') await S.pc.setRemoteDescription(new RTCSessionDescription(p.sdp));
      await flushIce();
    } else if (p.type === 'ice' && p.candidate) {
      // ⚠️ 關鍵：setRemoteDescription 之前呼叫 addIceCandidate 會失敗，
      //    而 ICE 候選是「邊收集邊送」→ 常常比 offer/answer 更早到。
      //    直接丟掉就永遠連不上（這正是「配對成功卻傳不動」的主因）。
      const ready = S.pc.remoteDescription && S.pc.remoteDescription.type;
      if (ready) {
        try { await S.pc.addIceCandidate(p.candidate); } catch { /* 重複或過期，忽略 */ }
      } else {
        S.pendingIce.push(p.candidate);       // 先排隊，等 remoteDescription 設好再一起加
      }
    }
  }

  /** 把排隊中的 ICE 候選補進連線（remoteDescription 設好後才能加） */
  async function flushIce() {
    const list = S.pendingIce.splice(0);
    for (const c of list) {
      try { await S.pc.addIceCandidate(c); } catch { /* 重複或過期 */ }
    }
  }

  // ── WebRTC ───────────────────────────────────────
  async function onPeerFound() {
    stopPoll();
    status(t('tr_found'));
    startPoll();
    renderPeers(t('tr_pairing'));
    if (S.peerId < S.peer) { if (!S.pc) await createPeer(true); }
    else if (!S.pc) await createPeer(false);
  }

  async function createPeer(offerer) {
    S.pc = new RTCPeerConnection(ICE);
    S.pc.onicecandidate = (e) => {
      if (e.candidate) sendSignal({ type: 'ice', candidate: e.candidate.toJSON ? e.candidate.toJSON() : e.candidate });
    };
    S.pc.onconnectionstatechange = () => {
      const st = S.pc.connectionState;
      if (st === 'connected') {
      clearTimeout(S.connectTimer);
      touch();
      // 讓「誰要做什麼」一目了然：送方按開始傳送，收方什麼都不用按
      status(S.files.length ? t('tr_connected_send') : t('tr_connected_recv'), 'ok');
      renderPeers(t('tr_connected'));
      $('#tr-send').disabled = !S.files.length;
    } else if (st === 'failed') {
      clearTimeout(S.connectTimer);
      status(t('tr_failed_hint'), 'err');
    } else if (st === 'disconnected') {
      status(t('tr_broken'), 'err');
    }
    };
    S.pc.ondatachannel = (e) => bindChannel(e.channel);
    // 逾時提示：兩台裝置若不在同一個網路，或防火牆擋住，就不會連上
    clearTimeout(S.connectTimer);
    S.connectTimer = setTimeout(() => {
      if (S.pc && S.pc.connectionState !== 'connected') status(t('tr_timeout_hint'), 'err');
    }, 20000);
    if (offerer) {
      bindChannel(S.pc.createDataChannel('ferry', { ordered: true }));
      const offer = await S.pc.createOffer();
      await S.pc.setLocalDescription(offer);
      await sendSignal({ type: 'offer', sdp: S.pc.localDescription });
    }
  }

  function bindChannel(dc) {
    S.dc = dc;
    dc.binaryType = 'arraybuffer';
    dc.bufferedAmountLowThreshold = LOW_WATER;
    dc.onopen = () => {
      touch();
      if (S.pc?.connectionState === 'connected') status(t('tr_connected'), 'ok');
      $('#tr-send').disabled = !S.files.length;
    };
    dc.onclose = () => { status(t('tr_closed'), 'err'); $('#tr-send').disabled = true; };
    dc.onmessage = (e) => onData(e.data);
  }

  // ── 傳送 ─────────────────────────────────────────
  $('#tr-send').addEventListener('click', () => {
  // 無損傳輸也要守同一套廣告規則（小羅 2026-09-29 確認：次數分開算、規則一樣）
  //   訪客每用 3 次／免費會員每用 5 次 → 下一次動作前先看一次廣告
  if (window.FY?.adGate?.('transfer', () => startSend())) return;
  startSend();
});

  async function startSend() {
    if (S.sending || !S.dc || S.dc.readyState !== 'open') return;
    // 先扣一次「無損傳輸」免費次數（與無水印下載分開計算；會員無限制）
    try {
      await api('/claim', { method: 'POST', body: JSON.stringify({
        peer_id: S.peerId, files: S.files.length }) });
      // claim 回的是單一 kind 的結果；重新抓完整狀態才能同時更新下載／傳輸兩個面板
      window._loadQuota?.();
    } catch (e) {
      // 次數用完 → 若其實是該看廣告，直接跳廣告（看完自動重送）
      if ((e.code === 'AD_REQUIRED' || e.code === 'QUOTA_EXCEEDED')
          && await window.FY?.quotaAdRecover?.('transfer', () => startSend())) return;
      status(e.message, 'err');
      return;
    }
    S.sending = true; $('#tr-send').disabled = true; touch();
    const list = S.files.slice();
    const started = performance.now();
    let sent = 0;
    renderSendList(list);
    startSpeed();

    try {
      for (let i = 0; i < list.length; i++) {
        const item = list[i];
        updateRow('send', i, 0, t('tr_calc'));
        const sha = await sha256OfFile(item.file);
        const id = `${Date.now()}-${i}`;
        S.sendIds = S.sendIds || {};
        S.sendIds[i] = id;
        S.cancelIdx.delete(i);
        dcSend(JSON.stringify({ t: 'start', id, name: item.name, size: item.size, sha }));
        updateRow('send', i, 0, t('tr_xfer'));

        let off = 0;
        while (off < item.size) {
          if (S.cancelIdx.has(i)) {                 // 使用者按了 ✕ → 這一個不傳了
            S.cancelIdx.delete(i);
            dcSend(JSON.stringify({ t: 'cancel', id }));
            updateRow('send', i, 0, t('tr_cancelled'), 'err');
            break;
          }
          if (S.dc.readyState !== 'open') throw new Error('連線已中斷');
          if (S.dc.bufferedAmount > HIGH_WATER) {
            await new Promise((r) => {
              const h = () => { S.dc.removeEventListener('bufferedamountlow', h); r(); };
              S.dc.addEventListener('bufferedamountlow', h);
            });
          }
          const end = Math.min(off + chunkSize, item.size);
          const buf = await item.file.slice(off, end).arrayBuffer();
          S.dc.send(buf);
          off = end; sent += buf.byteLength;
          updateRow('send', i, off / item.size, `${fmtBytes(off)} / ${fmtBytes(item.size)}`);
          tuneChunk(S.dc.bufferedAmount);
        }
        if (S.cancelIdx.has(i)) continue;          // 已取消的檔案不要送 end
        touch();
        dcSend(JSON.stringify({ t: 'end', id }));
        updateRow('send', i, 1, t('tr_waitack'));
      }
      status(t('tr_sent'));
    } catch (err) {
      status(t('tr_interrupted') + err.message, 'err');
    } finally {
      S.sending = false; stopSpeed();
      $('#tr-send').disabled = !S.files.length || S.dc?.readyState !== 'open';
      api('/done', { method: 'POST', body: JSON.stringify({
        peer_id: S.peerId, ok: true, files: list.length,
        total_bytes: sent, duration_ms: Math.round(performance.now() - started) }) }).catch(() => {});
    }
  }

  function dcSend(s) { if (S.dc?.readyState === 'open') S.dc.send(s); }

  let chunkSize = CHUNK;
  function tuneChunk(buffered) {
    if (buffered > HIGH_WATER * 2 && chunkSize > 8 * 1024) chunkSize = Math.max(8 * 1024, chunkSize / 2);
    else if (buffered < LOW_WATER && chunkSize < 256 * 1024) chunkSize = Math.min(256 * 1024, chunkSize * 2);
  }

  // ── 接收 ─────────────────────────────────────────
  function onData(data) {
    if (typeof data === 'string') {
      let m; try { m = JSON.parse(data); } catch { return; }
      onControl(m); return;
    }
    const r = S.receiving;
    if (!r) return;
    // ⚠️ 已取消的檔案：DataChannel 緩衝中還在飛的資料要丟掉，不然「已取消」會被進度蓋回去
    if (S.cancelledIds.has(r.id)) return;
    touch();
    const bytes = new Uint8Array(data);
    r.chunks.push(bytes);
    r.got += bytes.length;
    r.hasher.update(bytes);
    S.speed.bytes += bytes.length;
    updateRow('recv', r.index, r.got / r.size, `${fmtBytes(r.got)} / ${fmtBytes(r.size)}`);
  }

  function onControl(m) {
    if (m.t === 'start') {
      S.cancelledIds.delete(m.id);
      const idx = $('#recv-list').children.length;
      S.receiving = { id: m.id, name: m.name, size: m.size, sha: m.sha,
                      chunks: [], got: 0, hasher: new SHA256(), index: idx };
      addRow('recv', m.name, m.size);
      status(t('tr_receiving') + m.name);
    } else if (m.t === 'cancel') {
      // 對方按了 ✕（或他取消傳送）→ 把這一個丟掉、顯示已取消
      const r = S.receiving;
      if (m.id) S.cancelledIds.add(m.id);
      if (r && (!m.id || r.id === m.id)) {
        S.receiving = null;
        updateRow('recv', r.index, 0, t('tr_cancelled_peer'), 'err');
        status(t('tr_cancelled_peer') + '：' + r.name, 'err');
      }
      if (m.id) S.cancelIdx.add(m.id);
    } else if (m.t === 'end') {
      finishReceive();
    } else if (m.t === 'ack') {
      status(m.ok ? t('tr_acked_ok') : t('tr_acked_bad'), m.ok ? 'ok' : 'err');
    }
  }

  async function finishReceive() {
    const r = S.receiving;
    if (!r) return;
    S.receiving = null;
    const sha = r.hasher.hex();
    const ok = (sha === r.sha);
    updateRow('recv', r.index, 1, ok ? t('tr_status_ok') : t('tr_status_bad'), ok ? 'ok' : 'err');
    const blob = new Blob(r.chunks);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = r.name.split('/').pop() || 'file';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
    dcSend(JSON.stringify({ t: 'ack', id: r.id, ok }));
    status(ok ? `${r.name} ${t('tr_recv_done')}` : `${r.name} ${t('tr_recv_bad')}`, ok ? 'ok' : 'err');
    api('/done', { method: 'POST', body: JSON.stringify({
      peer_id: S.peerId, ok, files: 1, total_bytes: r.size }) }).catch(() => {});
  }

  // ── 進度列 ───────────────────────────────────────
  function rowHtml(name, size) {
    return `<span class="th">📦</span>
      <div style="flex:1;min-width:0">
        <div class="row-between" style="display:flex;justify-content:space-between;gap:8px">
          <span class="nm">${esc(name)}</span>
          <span style="display:flex;gap:6px;align-items:center;white-space:nowrap">
            <span class="sz">${fmtBytes(size)}</span>
            <button class="linkbtn tr-x" type="button" title="${esc(t('tr_cancel_title'))}"
                    aria-label="${esc(t('tr_cancel_title'))}">✕</button></span></div>
        <div class="track" style="margin-top:8px"><i style="width:0"></i></div>
        <div class="pm" style="margin-top:6px"><span class="st"></span></div>
      </div>`;
  }
  function addRow(kind, name, size) {
    const ul = $(kind === 'send' ? '#send-list' : '#recv-list');
    const li = document.createElement('li');
    li.className = 'fr';
    li.dataset.k = kind + '-' + ul.children.length;
    li.innerHTML = rowHtml(name, size);
    ul.appendChild(li);
  }
  function renderSendList(list) {
    const ul = $('#send-list');
    ul.innerHTML = '';
    list.forEach((f, i) => {
      const li = document.createElement('li');
      li.className = 'fr';
      li.dataset.k = 'send-' + i;
      li.innerHTML = rowHtml(f.name, f.size);
      ul.appendChild(li);
    });
  }
  function updateRow(kind, i, ratio, text, cls = '') {
    const li = document.querySelector(`[data-k="${kind}-${i}"]`);
    if (!li) return;
    li.querySelector('.track i').style.width = Math.round(Math.min(1, ratio) * 100) + '%';
    const st = li.querySelector('.st');
    if (st) { st.textContent = text || ''; st.className = 'st ' + cls; }
  }

  /** 使用者按了某一列的 ✕：取消那個檔案（傳送中／接收中都支援）。 */
  function cancelRow(k) {
    if (!k) return;
    const [kind, idxs] = k.split('-');
    const i = Number(idxs);
    if (kind === 'send') {
      S.cancelIdx.add(i);                       // 傳送迴圈會在下一塊之前停下來
      updateRow('send', i, 0, t('tr_cancelled'), 'err');
      status(t('tr_cancelled'), 'err');
      const id = S.sendIds[i];
      if (id) { S.cancelledIds.add(id); dcSend(JSON.stringify({ t: 'cancel', id })); }
      return;
    }
    // 接收中：丟掉半成品、通知對方停
    const r = S.receiving;
    if (r && r.index === i) {
      S.cancelledIds.add(r.id);
      S.receiving = null;
      dcSend(JSON.stringify({ t: 'cancel', id: r.id }));
      updateRow('recv', i, 0, t('tr_cancelled'), 'err');
      status(t('tr_cancelled') + '：' + r.name, 'err');
    }
  }
  for (const sel of ['#send-list', '#recv-list']) {
    const box = $(sel);
    if (!box) continue;
    box.addEventListener('click', (e) => {
      const b = e.target.closest && e.target.closest('.tr-x');
      if (!b) return;
      cancelRow(b.closest('li')?.dataset.k);
    });
  }

  function startSpeed() {
    S.speed.bytes = 0; S.speed.at = performance.now();
    S.speed.timer = setInterval(() => {
      const dt = (performance.now() - S.speed.at) / 1000;
      const bps = dt > 0 ? S.speed.bytes / dt : 0;
      S.speed.bytes = 0; S.speed.at = performance.now();
      $('#tr-speed').textContent = bps > 0 ? '· ' + fmtBytes(bps) + t('tr_per_sec') : '';
    }, 800);
  }
  function stopSpeed() {
    if (S.speed.timer) { clearInterval(S.speed.timer); S.speed.timer = null; }
    $('#tr-speed').textContent = '';
  }

  renderFiles();
})();
