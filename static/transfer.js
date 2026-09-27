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
  };

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

  // ── 選檔 ─────────────────────────────────────────
  function addFiles(list) {
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
    if (j.peers?.length) { S.peer = j.peers[0]; onPeerFound(); }
    else { status(t('tr_waiting')); startPoll(); }
  }

  function renderKnown() {
    const box = $('#known');
    if (!S.known?.length) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="lbl" style="margin-bottom:6px">' + t('tr_known') + '</div>' + S.known.map((p, i) =>
      `<button class="big gh sm" style="margin-top:6px" data-peer="${esc(p)}">${esc(String(p).slice(0, 22))}… ${t('tr_reconnect')}</button>`).join('');
    box.querySelectorAll('[data-peer]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const j = await api('/pair', { method: 'POST', body: JSON.stringify({ peer_id: S.peerId, target: b.dataset.peer }) });
        S.code = j.code; S.peer = j.peers[0];
        $('#mycode').textContent = j.code; $('#codebox').hidden = false;
        onPeerFound();
      } catch (err) { status(err.message, 'err'); }
    }));
  }

  $('#tr-gen').addEventListener('click', async () => {
    try { await join(null); } catch (e) { status(e.message, 'err'); }
  });
  $('#tr-join').addEventListener('click', async () => {
    const code = ($('#join-code').value || '').trim();
    if (code.length !== 6) return status(t('tr_enter6'), 'err');
    try { await join(code); } catch (e) { status(e.message, 'err'); }
  });
  $('#join-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#tr-join').click(); });

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
        if (!S.peer && j.peers?.length) { S.peer = j.peers[0]; onPeerFound(); }
        for (const m of j.messages || []) await handleSignal(m);
      } catch (e) {
        if (String(e.message).includes('過期') || String(e.message).includes('不存在')) {
          stopPoll(); status(t('tr_expired'), 'err');
        }
      }
    }, 900);
  }
  function stopPoll() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

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
      if (S.pc?.connectionState === 'connected') status(t('tr_connected'), 'ok');
      $('#tr-send').disabled = !S.files.length;
    };
    dc.onclose = () => { status(t('tr_closed'), 'err'); $('#tr-send').disabled = true; };
    dc.onmessage = (e) => onData(e.data);
  }

  // ── 傳送 ─────────────────────────────────────────
  $('#tr-send').addEventListener('click', startSend);

  async function startSend() {
    if (S.sending || !S.dc || S.dc.readyState !== 'open') return;
    // 先扣一次「無損傳輸」免費次數（與無水印下載分開計算；會員無限制）
    try {
      await api('/claim', { method: 'POST', body: JSON.stringify({
        peer_id: S.peerId, files: S.files.length }) });
      // claim 回的是單一 kind 的結果；重新抓完整狀態才能同時更新下載／傳輸兩個面板
      window._loadQuota?.();
    } catch (e) {
      status(e.message, 'err');
      return;
    }
    S.sending = true; $('#tr-send').disabled = true;
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
        dcSend(JSON.stringify({ t: 'start', id, name: item.name, size: item.size, sha }));
        updateRow('send', i, 0, t('tr_xfer'));

        let off = 0;
        while (off < item.size) {
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
    const bytes = new Uint8Array(data);
    r.chunks.push(bytes);
    r.got += bytes.length;
    r.hasher.update(bytes);
    S.speed.bytes += bytes.length;
    updateRow('recv', r.index, r.got / r.size, `${fmtBytes(r.got)} / ${fmtBytes(r.size)}`);
  }

  function onControl(m) {
    if (m.t === 'start') {
      const idx = $('#recv-list').children.length;
      S.receiving = { id: m.id, name: m.name, size: m.size, sha: m.sha,
                      chunks: [], got: 0, hasher: new SHA256(), index: idx };
      addRow('recv', m.name, m.size);
      status(t('tr_receiving') + m.name);
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
          <span class="nm">${esc(name)}</span><span class="sz">${fmtBytes(size)}</span></div>
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
