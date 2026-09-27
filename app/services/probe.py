"""用 ffprobe 讀出影片的**真實解析度**（只讀檔頭，不下載整支）。

為什麼需要：
    有些平台（TikTok／微博／小紅書／頭條…）只回「高清」「原畫」，沒給幾 P
    → 使用者根本不知道是 720P 還是 4K。
    ffprobe 可以直接讀遠端檔案的 header（HTTP range request）拿到真實寬高。

⚠️ 成本：每支約 0.5～2 秒。所以**只對「沒有 height 的格式」做**，且併發執行。
   ffprobe 在容器內（Dockerfile 已安裝 ffmpeg）。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Optional

from ..core.models import VideoInfo

#: ffprobe 執行檔（找不到就整段跳過，不影響解析）
_FFPROBE = shutil.which("ffprobe") or "ffprobe"

#: 快取：url -> (width, height)，避免同一支影片重複 probe
_cache: dict[str, tuple[int, int]] = {}
_MAX_CACHE = 500


def _probe_one_sync(url: str, headers: dict[str, str], timeout: int = 25) -> Optional[tuple[int, int]]:
    if url in _cache:
        return _cache[url]
    hdr = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    cmd = [_FFPROBE, "-v", "quiet", "-print_format", "json", "-show_streams",
           "-select_streams", "v:0"]
    if hdr:
        cmd += ["-headers", hdr]
    cmd.append(url)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        info = json.loads(r.stdout)
        st = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
    if not (w and h):
        return None
    if len(_cache) > _MAX_CACHE:
        _cache.clear()
    _cache[url] = (w, h)
    return (w, h)


async def enrich_dimensions(info: VideoInfo, *, limit: int = 6) -> None:
    """把沒有 `height` 的影片格式補上真實解析度，並重新標籤。

    - 只處理影片（音訊／圖片跳過）
    - 併發執行，最多 `limit` 支
    - 失敗就維持原本的標籤（不影響解析結果）
    """
    targets = [f for f in info.formats
               if not f.height and not f.audio and f.ext not in ("jpg", "jpeg", "png", "webp")]
    if not targets:
        return
    targets = targets[:limit]

    results = await asyncio.gather(
        *(asyncio.to_thread(_probe_one_sync, f.url, f.headers or {}) for f in targets),
        return_exceptions=True,
    )

    from ..platforms._ytdlp import quality_label

    for fmt, res in zip(targets, results):
        if isinstance(res, Exception) or not res:
            continue
        w, h = res
        fmt.width, fmt.height = w, h
        fmt.quality_score = max(fmt.quality_score or 0, h)
        # ⚠️ 畫質要用**短邊**算：1080x1920 是「1080P」不是 2K
        #    （直式/橫式都一樣，大家看的是短邊那個數字）
        fmt.label = quality_label(min(w, h))
        # 已經有真實解析度了，不必再猜（例如「高清」→「1080P」）

    # 補完後重新排序（影片高→低，音訊最後）
    info.formats.sort(key=lambda f: (f.audio, -(f.quality_score or 0)))

