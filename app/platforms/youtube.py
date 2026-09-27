"""YouTube 解析（yt-dlp）。

⚠️ YouTube 是 **IP 敏感** 平台（規格書第 7 章）：
  - CDN 擋 Origin → 一律走伺服器代理（mode="proxy"，串流不落地）
  - 高畫質是「分離軌」→ 需伺服器端 ffmpeg 合併（Dockerfile 已裝 ffmpeg）

⚠️ 為什麼一定要 Deno（2026-09-27 從 v8i8 學到）：
    yt-dlp 要解 YouTube 的 JS 驗證（nsig），**沒有 JS runtime 就一律失敗**，
    錯誤會顯示成「需要登入或 cookies」，很容易誤判成 IP 被擋。
    → Dockerfile 已安裝 Deno，這裡明確指定 `js_runtimes`。
    → player_client 也要用 "all"（讓 yt-dlp 自己挑還活著的 client），
      不是只寫死兩三個（那些會被逐步關掉）。
"""
from __future__ import annotations

import re
from typing import Any

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", re.I
)

#: 固定用瀏覽器身分（YouTube 會看這些標頭決定給不給資料）
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


class YoutubeResolver(YtDlpResolver):
    name = "youtube"
    label = "YouTube"
    hosts = ("youtube.com", "youtu.be", "youtube-nocookie.com")
    default_mode = "proxy"

    # ⚠️ 解析與下載用同一組參數（client 輪替會互相打架，統一用 all 最穩）
    ytdlp_extra: dict[str, Any] = {
        "http_headers": _BROWSER_HEADERS,
        "extractor_args": {"youtube": {"player_client": ["all"]}},
        # Deno 解 JS 驗證（Dockerfile 已安裝）；yt-dlp 也會自動偵測，這裡明確保底
        "js_runtimes": {"deno": {}},
        "retry_sleep": "extractor:exp=1:20",
        "fragment_retries": 10,
    }

    def download_opts(self) -> dict[str, Any]:
        return dict(self.ytdlp_extra)

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
