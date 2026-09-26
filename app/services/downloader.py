"""伺服器代理下載（給「CDN 擋 Origin」的平台用，例如 YouTube）。

原則：**串流不落地**（用完即刪，不保存任何檔案）。
（規格書第 8 章 C 模式）

⚠️ 安全：只接受「已註冊平台認得的網址」，避免變成開放代理。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Optional

import yt_dlp

from ..core import registry
from ..core.config import settings
from ..core.errors import PlatformChanged, PlatformTimeout, UnsupportedUrl


def build_selector(height: Optional[int], audio: bool) -> str:
    """畫質 → yt-dlp format selector。"""
    if audio:
        return "bestaudio/best"
    if height:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )
    return "bestvideo+bestaudio/best"


def _filename_of(path: str, fallback: str) -> str:
    base = os.path.basename(path)
    return base or fallback


async def fetch_to_temp(
    src: str, *, height: Optional[int] = None, audio: bool = False
) -> tuple[str, str]:
    """下載到暫存檔，回傳 (路徑, 建議檔名)。呼叫端負責刪除。"""
    src = (src or "").strip()
    if not src:
        raise UnsupportedUrl("缺少影片網址")
    resolver = await registry.detect(src)
    if resolver is None:
        raise UnsupportedUrl("這個網址不支援代理下載")

    tmpdir = tempfile.mkdtemp(prefix="ferry_dl_")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "socket_timeout": 20,
        "retries": 2,
        "format": build_selector(height, audio),
        "outtmpl": os.path.join(tmpdir, "%(title).80s.%(ext)s"),
        "merge_output_format": "mp4",
        "restrictfilenames": False,
        "nopart": False,
        "http_headers": {"User-Agent": settings.user_agent},
    }

    from .cookies import cookiefile_for

    cf = cookiefile_for(resolver.name)
    if cf:
        opts["cookiefile"] = cf

    # 平台自己的 yt-dlp 參數（例如 YouTube 的 player_client）
    extra = getattr(resolver, "ytdlp_opts", None)
    if callable(extra):
        opts.update(extra())

    def run() -> str:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(src, download=True)
            path = ydl.prepare_filename(info)
            if not os.path.exists(path):
                # 合併後副檔名可能變了
                stem = os.path.splitext(path)[0]
                for cand in (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".jpg"):
                    if os.path.exists(stem + cand):
                        path = stem + cand
                        break
            return path

    try:
        path = await asyncio.wait_for(
        asyncio.to_thread(run), timeout=settings.proxy_download_timeout
    )
    except asyncio.TimeoutError as exc:
        raise PlatformTimeout("下載逾時，請換較低畫質", platform=resolver.name) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise PlatformChanged(
            f"下載失敗：{str(exc)[:160]}", platform=resolver.name
        ) from exc

    if not os.path.exists(path):
        raise PlatformChanged("下載後找不到檔案", platform=resolver.name)

    return path, _filename_of(path, "video.mp4")


def cleanup(path: str) -> None:
    """刪掉暫存資料夾（整個）。"""
    try:
        folder = os.path.dirname(path)
        if folder and os.path.isdir(folder) and "ferry_dl_" in folder:
            for name in os.listdir(folder):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
            os.rmdir(folder)
    except OSError:
        pass
