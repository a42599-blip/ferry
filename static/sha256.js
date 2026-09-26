/* 增量 SHA-256（FIPS 180-4）—— 邊收邊算，大檔不佔記憶體。
   Web Crypto 只能「一次算一整包」，所以這裡自己實作串流版。 */
'use strict';

const SHA256 = (() => {
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  const rotr = (x, n) => (x >>> n) | (x << (32 - n));

  return class SHA256 {
    constructor() {
      this.h = new Uint32Array([
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
      ]);
      this.block = new Uint8Array(64);
      this.blockLen = 0;
      this.total = 0;                    // 位元組數
      this._w = new Uint32Array(64);
    }

    update(bytes) {
      const b = (bytes instanceof Uint8Array) ? bytes : new Uint8Array(bytes);
      this.total += b.length;
      let i = 0;

      if (this.blockLen > 0) {
        const need = 64 - this.blockLen;
        const take = Math.min(need, b.length);
        this.block.set(b.subarray(0, take), this.blockLen);
        this.blockLen += take;
        i = take;
        if (this.blockLen === 64) { this._process(this.block); this.blockLen = 0; }
      }

      for (; i + 64 <= b.length; i += 64) {
        this._process(b.subarray(i, i + 64));
      }
      if (i < b.length) {
        this.block.set(b.subarray(i), 0);
        this.blockLen = b.length - i;
      }
      return this;
    }

    _process(chunk) {
      const w = this._w, h = this.h;
      for (let i = 0; i < 16; i++) {
        w[i] = (chunk[i * 4] << 24) | (chunk[i * 4 + 1] << 16)
             | (chunk[i * 4 + 2] << 8) | chunk[i * 4 + 3];
      }
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }

      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        hh = g; g = f; f = e;
        e = (d + t1) >>> 0;
        d = c; c = b; b = a;
        a = (t1 + t2) >>> 0;
      }
      h[0] = (h[0] + a) >>> 0; h[1] = (h[1] + b) >>> 0;
      h[2] = (h[2] + c) >>> 0; h[3] = (h[3] + d) >>> 0;
      h[4] = (h[4] + e) >>> 0; h[5] = (h[5] + f) >>> 0;
      h[6] = (h[6] + g) >>> 0; h[7] = (h[7] + hh) >>> 0;
    }

    hex() {
      const bitLen = this.total * 8;
      const pad = new Uint8Array(((this.blockLen < 56) ? 56 : 120) - this.blockLen);
      pad[0] = 0x80;
      // 長度（64-bit big-endian；JS 檔案大小遠小於 2^53，故高位用除法補）
      const hi = Math.floor(bitLen / 0x100000000);
      const lo = bitLen >>> 0;
      const dv = new DataView(pad.buffer);
      dv.setUint32(pad.length - 8, hi);
      dv.setUint32(pad.length - 4, lo);
      this.update(pad);

      let out = '';
      for (const v of this.h) out += v.toString(16).padStart(8, '0');
      return out;
    }
  };
})();

/* 用 stream 分塊算整個檔案的 SHA-256（記憶體用量固定）*/
async function sha256OfFile(file, onProgress) {
  const hasher = new SHA256();
  const CHUNK = 4 * 1024 * 1024;
  let done = 0;
  for (let off = 0; off < file.size; off += CHUNK) {
    const buf = await file.slice(off, off + CHUNK).arrayBuffer();
    hasher.update(new Uint8Array(buf));
    done += buf.byteLength;
    if (onProgress) onProgress(done / file.size);
    await new Promise((r) => setTimeout(r, 0));   // 讓 UI 有呼吸空間
  }
  return hasher.hex();
}
