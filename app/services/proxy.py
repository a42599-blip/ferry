"""平台 CDN 串流代理（支援 Range ＋ 影音合併）。

═══════════════════════════════════════════════════════════════
 為什麼一定要有這個（2026-09-27 從 v8i8 學到）
═══════════════════════════════════════════════════════════════
 1. **Referer 檢查**：平台的 CDN 會看 Referer。瀏覽器送出的是
    `https://ferry.v8i8.com/` → CDN 判定不是自家網站 → 403。
    由伺服器補上正確 Referer 轉發就通了。
 2. **影音分離（DASH）**：B站等平台把影像、聲音放兩條網址，
    前端直連只會拿到「無聲影片」。這裡用 ffmpeg 合併後再給使用者。

⚠️ 防止變成開放代理：只轉發「剛由 /api/resolve 產生的網址」（登記制，30 分失效）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time
from typing import Optional

import httpx
from fastapi.responses import StreamingResponse

#: key -> (video_url, audio_url | None, headers, 到期時間)
_relays: dict[str, tuple[str, Optional[str], dict, float]] = {}
_TTL = 1800.0            # 30 分鐘（平台 CDN 連結多半 1～6 小時失效）
_MAX = 3000              # 上限，避免記憶體無限長大


def register(video: str, audio: Optional[str] = None, headers: dict | None = None) -> str:
    """登記一組可轉發的網址，回傳短鍵（前端只拿得到鍵）。"""
    now = time.time()
    if len(_relays) > _MAX:
        for k in [k for k, v in _relays.items() if v[3] < now]:
            _relays.pop(k, None)
    key = hashlib.sha256(f"{video}|{audio or ''}".encode()).hexdigest()[:20]
    _relays[key] = (video, audio or None, dict(headers or {}), now + _TTL)
    return key


def lookup(key: str) -> Optional[tuple[str, Optional[str], dict]]:
    row = _relays.get(key or "")
    if not row:
        return None
    video, audio, headers, exp = row
    if exp < time.time():
        _relays.pop(key, None)
        return None
    return video, audio, headers


def _clean(stream: httpx.Response) -> dict[str, str]:
    """只保留對播放／續傳有意義的回應標頭。"""
    out: dict[str, str] = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for k in ("content-range", "content-length", "content-type", "last-modified", "etag"):
        v = stream.headers.get(k)
        if v:
            out[k.replace("-", " ").title().replace(" ", "-")] = v
    return out


async def _pump(stream: httpx.Response, client: httpx.AsyncClient):
    try:
        async for chunk in stream.aiter_bytes(256 * 1024):
            yield chunk
    finally:
        await stream.aclose()
        await client.aclose()


async def relay(video: str, audio: Optional[str], headers: dict, range_header: str | None):
    """轉發（若 audio 有值則先合併成一個 mp4 再回）。

    回傳 Starlette 的 StreamingResponse。
    """
    # ⚠️ 必須帶瀏覽器 UA：B站等 CDN 會擋沒有 UA 的請求（403）
    hdr = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"),
        "Accept": "*/*",
        **headers,
    }
    client = httpx.AsyncClient(timeout=httpx.Timeout(60, read=300), follow_redirects=True)
    req = client.build_request("GET", video, headers=hdr)
    if range_header:
        req.headers["Range"] = range_header
    resp = await client.send(req, stream=True)

    if resp.status_code >= 400:
        await resp.aclose()
        await client.aclose()
        # ⚠️ 不用 5xx：Cloudflare 會攔截並換成自己的錯誤頁（使用者看不到原因）
        return StreamingResponse(
            iter([b""]), status_code=422,
            media_type="application/json",
            headers={"X-Relay-Error": f"CDN {resp.status_code}"},
        )

    if audio:
        # 影音分離 → 合併後回傳（整支下載完才回；使用者看到的是「準備中」）
        await resp.aclose()
        await client.aclose()
        path = await _merge(video, audio, hdr)
        f = open(path, "rb")

        def _reader():
            try:
                while True:
                    b = f.read(512 * 1024)
                    if not b:
                        break
                    yield b
            finally:
                f.close()
                _rm_temp(path)

        return StreamingResponse(_reader(), media_type="video/mp4",
                                 headers={"Content-Disposition": 'attachment; filename="video.mp4"'})

    return StreamingResponse(_pump(resp, client), status_code=resp.status_code,
                             media_type=resp.headers.get("content-type", "video/mp4"),
                             headers=_clean(resp))


async def _merge(video: str, audio: str, headers: dict) -> str:
    """用 ffmpeg 把分離的影、音軌合成 mp4（-c copy，不重編碼）。"""
    tmpdir = tempfile.mkdtemp(prefix="ferry_relay_")
    vpath, apath = os.path.join(tmpdir, "v.bin"), os.path.join(tmpdir, "a.bin")
    out = os.path.join(tmpdir, "out.mp4")

    async def grab(url: str, path: str) -> None:
        """抓單一軌。⚠️ B站 CDN 會中途斷線 → 用 Range 續傳重試。"""
        got = 0
        for attempt in range(6):
            h = dict(headers)
            if got:
                h["Range"] = f"bytes={got}-"
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=300),
                                             follow_redirects=True) as c:
                    async with c.stream("GET", url, headers=h) as r:
                        if r.status_code >= 400:
                            r.raise_for_status()
                        mode = "ab" if got else "wb"
                        with open(path, mode) as f:
                            async for chunk in r.aiter_bytes(262144):
                                f.write(chunk)
                                got += len(chunk)
                return
            except (httpx.HTTPError, httpx.StreamError, OSError):
                if attempt == 5:
                    raise
                await asyncio.sleep(0.6 * (attempt + 1))

    await asyncio.gather(grab(video, vpath), grab(audio, apath))

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", vpath, "-i", apath,
        "-c", "copy", "-movflags", "+faststart", out,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    for f in (vpath, apath):
        try:
            os.remove(f)
        except OSError:
            pass
    if proc.returncode != 0 or not os.path.exists(out):
        _rm_dir(tmpdir)
        raise RuntimeError(f"合併失敗：{(err or b'').decode('utf-8', 'ignore')[:140]}")
    return out


def _rm_temp(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
    _rm_dir(os.path.dirname(path))


def _rm_dir(folder: str) -> None:
    if not folder or "ferry_relay_" not in folder:
        return
    try:
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        os.rmdir(folder)
    except OSError:
        pass
