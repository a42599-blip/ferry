/* 轉運站 — 無損傳輸（WebRTC DataChannel）
   檔案在兩台裝置之間「點對點」直傳：不經我們的伺服器、不暫存、不壓縮、不改檔。
   我們只做兩件事：發 6 位配對碼、交換 WebRTC 名片（SDP/ICE）。 */
'use strict';

(() => {
  const $ = (s) => document.querySelector(s);
  if (!$('#panel-transfer')) return;

  const ICE = {
    iceServers: [
      { urls: 'stun:stun.l.google.com:19302' },
      { urls: 'stun:stun1.l.google.com:19302' },
    ],
  };
  const CHUNK = 64 * 1024;            // 64KB / 塊
  const HIGH_WATER = 8 * 1024 * 1024; // 緩衝超過 8MB 就等
  const LOW_WATER = 1 * 1024 * 1024;

  const S = {
    files: [],            // {file, name, size}
    code: null,
    peerId: null,
    peer: null,           // 對方的 peer id
    pc: null,
    dc: null,
    poll: null,
    sending: false,
    receiving: null,      // 進行中的接收
    stats: { bytes: 0, at: 0, timer: null },
    known: [],
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
  const fmtSpeed = (bps) => fmtBytes(bps) + '/s';
  const fmtEta = (sec) => {
    if (!isFinite(sec) || sec <= 0) return '–';
    if (sec < 60) return Math.ceil(sec) + ' 秒';
    const m = Math.floor(sec / 60);
    return m + ' 分 ' + Math.ceil(sec % 60) + ' 秒';
  };
  const setStatus = (msg, kind = '') => {
    const el = $('#tr-status');
    el.hidden = false;
    el.className = 'status ' + kind;
    el.textContent = msg;
  };
  const status = (msg, kind) => setStatus(msg, kind);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ── 選檔 ─────────────────────────────────────────
  function addFiles(list) {
    for (const f of list) {
      const name = f.webkitRelativePath || f.name;
      if (S.files.some((x) => x.name === name && x.size === f.size)) continue;
      S.files.push({ file: f, name, size: f.size });
    }
    renderFiles();
  }
  function renderFiles() {
    const box = $('#tr-list');
    if (!S.files.length) { box.innerHTML = '<li class="muted">還沒有選擇檔案</li>'; }
    else {
      box.innerHTML = S.files.map((f, i) => `
        <li><span class="fname">${esc(f.name)}</span>
        <span class="fsize">${fmtBytes(f.size)}</span>
        <button class="mini" data-rm="${i}" title="移除">✕</button></li>`).join('');
      box.querySelectorAll('[data-rm]').forEach((b) => b.addEventListener('click', () => {
        S.files.splice(Number(b.dataset.rm), 1); renderFiles();
      }));
    }
    const total = S.files.reduce((a, f) => a + f.size, 0);
    $('#tr-total').textContent = S.files.length ? `${S.files.length} 個檔案 · ${fmtBytes(total)}` : '';
    $('#tr-send').disabled = !S.files.length || !S.dc || S.dc.readyState !== 'open';
  }

  $('#tr-pick-files').addEventListener('click', () => $('#tr-input-files').click());
  $('#tr-pick-folder')?.addEventListener('click', () => $('#tr-input-folder').click());
  $('#tr-pick-photo')?.addEventListener('click', () => $('#tr-input-photo').click());
  $('#tr-input-files').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  $('#tr-input-folder')?.addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  $('#tr-input-photo')?.addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });

  const drop = $('#tr-drop');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove('over');
  }));
  drop.addEventListener('drop', async (e) => {
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
    } else if (e.dataTransfer?.files) {
      addFiles(e.dataTransfer.files);
    }
  });

  // ── 配對 ─────────────────────────────────────────
  const api = async (path, opt = {}) => {
    const r = await fetch('/api/signal' + path, {
      ...opt, headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) },
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.ok === false) throw new Error(j.message || j.detail || `HTTP ${r.status}`);
    return j;
  };

  S.peerId = deviceId();

  async function join(code) {
    const j = await api('/join', {
      method: 'POST',
      body: JSON.stringify({ code: code || null, peer_id: S.peerId, name: navigator.platform }),
    });
    S.code = j.code;
    S.known = j.known || [];
    $('#tr-mycode').textContent = j.code;
    $('#tr-code-box').hidden = false;
    renderKnown();
    if (j.peers && j.peers.length) { S.peer = j.peers[0]; onPeerFound(); }
    else { status('等待對方加入…（把這組 6 位碼給對方）'); startPoll(); }
  }

  function renderKnown() {
    const box = $('#tr-known');
    if (!S.known || !S.known.length) { box.innerHTML = ''; return; }
    box.innerHTML = '配對過的裝置（可直接重連）：' + S.known.map((p) =>
      `<button class="mini known" data-peer="${esc(p)}">${esc(String(p).slice(0, 18))}…</button>`).join(' ');
    box.querySelectorAll('[data-peer]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const j = await api('/pair', { method: 'POST', body: JSON.stringify({ peer_id: S.peerId, target: b.dataset.peer }) });
        S.code = j.code; S.peer = j.peers[0];
        $('#tr-mycode').textContent = j.code; $('#tr-code-box').hidden = false;
        onPeerFound();
      } catch (err) { status(err.message, 'err'); }
    }));
  }

  $('#tr-gen').addEventListener('click', async () => {
    try { await join(null); } catch (e) { status(e.message, 'err'); }
  });
  $('#tr-join').addEventListener('click', async () => {
    const code = ($('#tr-code').value || '').trim();
    if (code.length !== 6) return status('請輸入 6 位數字', 'err');
    try { await join(code); } catch (e) { status(e.message, 'err'); }
  });
  $('#tr-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#tr-join').click(); });

  // ── signaling 輪詢 ───────────────────────────────
  function startPoll() {
    stopPoll();
    S.poll = setInterval(async () => {
      if (!S.code) return;
      try {
        const j = await api(`/poll?code=${S.code}&peer_id=${encodeURIComponent(S.peerId)}`);
        if (!S.peer && j.peers.length) { S.peer = j.peers[0]; onPeerFound(); }
        for (const m of j.messages || []) await handleSignal(m);
      } catch (e) {
        // 房間過期
        if (String(e.message).includes('過期') || String(e.message).includes('不存在')) {
          stopPoll(); status('配對已過期，請重新產生配對碼', 'err');
        }
      }
    }, 900);
  }
  function stopPoll() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }

  const sendSignal = (payload) => api('/send', {
    method: 'POST',
    body: JSON.stringify({ code: S.code, from_peer: S.peerId, to_peer: S.peer, payload }),
  }).catch(() => {});

  async function handleSignal(m) {
    const p = m.payload || {};
    if (!S.pc && p.type === 'offer') {
      await createPeer(false);
    }
    if (!S.pc) return;
    if (p.type === 'offer') {
      await S.pc.setRemoteDescription(new RTCSessionDescription(p.sdp));
      const ans = await S.pc.createAnswer();
      await S.pc.setLocalDescription(ans);
      await sendSignal({ type: 'answer', sdp: S.pc.localDescription });
    } else if (p.type === 'answer') {
      if (S.pc.signalingState !== 'stable') {
        await S.pc.setRemoteDescription(new RTCSessionDescription(p.sdp));
      }
    } else if (p.type === 'ice' && p.candidate) {
      try { await S.pc.addIceCandidate(p.candidate); } catch { /* 忽略 */ }
    }
  }

  // ── WebRTC ───────────────────────────────────────
  async function onPeerFound() {
    stopPoll();
    status('找到對方裝置，正在建立直連…');
    startPoll();
    // 決定誰發 offer（用 peer id 比大小，避免同時發）
    const iAmOfferer = S.peerId < S.peer;
    if (iAmOfferer) await createPeer(true);
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
        status('已連線 ✅ 可以開始傳送了', 'ok');
        $('#tr-send').disabled = !S.files.length;
      } else if (st === 'failed' || st === 'disconnected') {
        status('連線中斷。請確認兩台裝置在同一個 WiFi，並保持頁面開啟。', 'err');
      }
    };
    S.pc.ondatachannel = (e) => bindChannel(e.channel);

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
      if (S.pc && S.pc.connectionState === 'connected') {
        status('已連線 ✅ 可以開始傳送了', 'ok');
      }
      $('#tr-send').disabled = !S.files.length;
    };
    dc.onclose = () => { status('連線已關閉', 'err'); $('#tr-send').disabled = true; };
    dc.onmessage = (e) => onData(e.data);
  }

  // ── 傳送 ─────────────────────────────────────────
  $('#tr-send').addEventListener('click', startSend);

  async function startSend() {
    if (S.sending || !S.dc || S.dc.readyState !== 'open') return;
    S.sending = true;
    $('#tr-send').disabled = true;

    const list = S.files.slice();
    const totalBytes = list.reduce((a, f) => a + f.size, 0);
    const started = performance.now();
    let sent = 0;
    let ok = 0, fail = 0;

    renderTransferList(list);
    startStats(sent, totalBytes);

    try {
      for (let i = 0; i < list.length; i++) {
        const item = list[i];
        updateRow(i, 0, '計算校驗碼…');
        const sha = await sha256OfFile(item.file);
        const id = `${Date.now()}-${i}`;

        dcSend(JSON.stringify({ t: 'start', id, name: item.name, size: item.size, sha }));
        updateRow(i, 0, '傳送中…');

        let off = 0;
        while (off < item.size) {
          if (S.dc.readyState !== 'open') throw new Error('連線已中斷');
          if (S.dc.bufferedAmount > HIGH_WATER) {
            await new Promise((r) => {
              const h = () => { S.dc.removeEventListener('bufferedamountlow', h); r(); };
              S.dc.addEventListener('bufferedamountlow', h);
            });
          }
          const end = Math.min(off + CHUNK, item.size);
          const buf = await item.file.slice(off, end).arrayBuffer();
          S.dc.send(buf);
          off = end;
          sent += buf.byteLength;
          updateRow(i, off / item.size, `${fmtBytes(off)} / ${fmtBytes(item.size)}`);
          tuneChunk(S.dc.bufferedAmount);
        }
        dcSend(JSON.stringify({ t: 'end', id }));
        updateRow(i, 1, '等待對方校驗…');
      }
      status('已送出，等待對方確認校驗結果…');
    } catch (err) {
      status('傳輸中斷：' + err.message, 'err');
    } finally {
      S.sending = false;
      stopStats();
      $('#tr-send').disabled = !S.files.length || S.dc?.readyState !== 'open';
      try {
        await api('/done', {
          method: 'POST',
          body: JSON.stringify({
            peer_id: S.peerId, ok: fail === 0, files: ok || list.length,
            total_bytes: sent, duration_ms: Math.round(performance.now() - started),
          }),
        });
      } catch { /* 統計失敗不影響 */ }
    }
  }

  function dcSend(s) { if (S.dc && S.dc.readyState === 'open') S.dc.send(s); }

  let chunkSize = CHUNK;
  function tuneChunk(buffered) {
    if (buffered > HIGH_WATER * 2 && chunkSize > 8 * 1024) chunkSize = Math.max(8 * 1024, chunkSize / 2);
    else if (buffered < LOW_WATER && chunkSize < 256 * 1024) chunkSize = Math.min(256 * 1024, chunkSize * 2);
  }

  // ── 接收 ─────────────────────────────────────────
  function onData(data) {
    if (typeof data === 'string') {
      let m; try { m = JSON.parse(data); } catch { return; }
      onControl(m);
      return;
    }
    // 二進位 → 目前的接收中檔案
    const r = S.receiving;
    if (!r) return;
    const bytes = new Uint8Array(data);
    r.chunks.push(bytes);
    r.got += bytes.length;
    r.hasher.update(bytes);
    S.stats.bytes += bytes.length;
    updateRow(r.index, r.got / r.size, `${fmtBytes(r.got)} / ${fmtBytes(r.size)}`);
  }

  function onControl(m) {
    if (m.t === 'start') {
      const idx = $('#tr-recv-list').children.length;
      S.receiving = {
        id: m.id, name: m.name, size: m.size, sha: m.sha,
        chunks: [], got: 0, hasher: new SHA256(), index: idx,
      };
      addRecvRow(m.name, m.size);
      status(`正在接收：${m.name}`);
    } else if (m.t === 'end') {
      finishReceive();
    } else if (m.t === 'ack') {
      // 對方回報校驗結果（sender 端只顯示）
      status(m.ok ? `✅ 對方已確認檔案完整（校驗一致）` : `⚠️ 對方回報校驗失敗`, m.ok ? 'ok' : 'err');
    }
  }

  async function finishReceive() {
    const r = S.receiving;
    if (!r) return;
    S.receiving = null;
    const sha = r.hasher.hex();
    const ok = (sha === r.sha);
    updateRow(r.index, 1, ok ? '校驗一致 ✅' : '校驗不一致 ❌', ok ? 'ok' : 'err');
    saveBlob(r.name, r.chunks, r.size);
    dcSend(JSON.stringify({ t: 'ack', id: r.id, ok }));
    status(ok ? `✅ ${r.name} 接收完成，校驗一致` : `⚠️ ${r.name} 校驗不一致（檔案可能不完整）`, ok ? 'ok' : 'err');
    try {
      await api('/done', {
        method: 'POST',
        body: JSON.stringify({ peer_id: S.peerId, ok, files: 1, total_bytes: r.size }),
      });
    } catch { /* 忽略 */ }
  }

  function saveBlob(name, chunks, size) {
    const blob = new Blob(chunks);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name.split('/').pop() || 'file';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }

  // ── 進度 UI ──────────────────────────────────────
  function renderTransferList(list) {
    $('#tr-progress').hidden = false;
    $('#tr-send-list').innerHTML = list.map((f, i) => `
      <li id="snd-${i}">
        <div class="row-between"><span class="fname">${esc(f.name)}</span>
          <span class="fsize">${fmtBytes(f.size)}</span></div>
        <div class="pbar"><i style="width:0%"></i></div>
        <div class="pmeta"><span class="pstate">等待中</span></div>
      </li>`).join('');
  }
  function addRecvRow(name, size) {
    const ul = $('#tr-recv-list');
    const i = ul.children.length;
    const li = document.createElement('li');
    li.id = 'rcv-' + i;
    li.innerHTML = `<div class="row-between"><span class="fname">${esc(name)}</span>
      <span class="fsize">${fmtBytes(size)}</span></div>
      <div class="pbar"><i style="width:0%"></i></div>
      <div class="pmeta"><span class="pstate">接收中…</span></div>`;
    ul.appendChild(li);
  }
  function updateRow(i, ratio, state, cls = '') {
    const li = $(`#snd-${i}`) || $(`#rcv-${i}`);
    if (!li) return;
    li.querySelector('.pbar i').style.width = Math.round(Math.min(1, ratio) * 100) + '%';
    const st = li.querySelector('.pstate');
    st.textContent = state || '';
    st.className = 'pstate ' + cls;
  }

  function startStats(done, total) {
    S.stats.bytes = 0; S.stats.at = performance.now();
    const t0 = performance.now();
    S.stats.timer = setInterval(() => {
      const dt = (performance.now() - S.stats.at) / 1000;
      const bps = dt > 0 ? S.stats.bytes / dt : 0;
      S.stats.bytes = 0; S.stats.at = performance.now();
      const elapsed = (performance.now() - t0) / 1000;
      const avg = elapsed > 0 ? (done + (S.stats.total || 0)) / elapsed : bps;
      $('#tr-speed').textContent = fmtSpeed(bps);
      S.stats.total = (S.stats.total || 0);
    }, 800);
  }
  function stopStats() { if (S.stats.timer) { clearInterval(S.stats.timer); S.stats.timer = null; } }
})();
