"""YouTube 解析（yt-dlp）。

⚠️ YouTube 是 **IP 敏感** 平台（規格書第 7 章）：
  - CDN 擋 Origin → 走伺服器「原樣轉發」（mode="relay"，見 resolve()）
  - 高畫質是「分離軌」→ 附上音軌網址，由伺服器 ffmpeg 合併（Dockerfile 已裝 ffmpeg）

⚠️ 為什麼一定要 Deno（2026-09-27 從 v8i8 學到）：
    yt-dlp 要解 YouTube 的 JS 驗證（nsig），**沒有 JS runtime 就一律失敗**，
    錯誤會顯示成「需要登入或 cookies」，很容易誤判成 IP 被擋。
    → Dockerfile 已安裝 Deno，這裡明確指定 `js_runtimes`。
    → 解析用 player_client "all"（讓 yt-dlp 自己挑還活著的 client）。

⚠️ 解析 ≠ 下載（2026-09-28 從 v8i8 學到，小羅指示只讀不動 v8i8）：
    症狀：解析成功、畫質清單都有，但下載回 `HTTP Error 403: Forbidden`。
    原因：下載也用 "all" → yt-dlp 會挑到 web／tv 的影片網址，
          那種網址要 PO token，伺服器直接抓 googlevideo 就被擋。
    第一次只改「下載身分」（照 v8i8 的 ios/android…）→ 部署後**仍然 403**。
    真正關鍵（再讀 v8i8 前端 dlUrl()）：
          v8i8 手機版**不會重新跑 yt-dlp**，而是把「解析時拿到的那個網址」
          透過伺服器原樣轉發（/api/dl-stream）。
    → 本模組改成同樣做法：resolve() 後把格式標成 relay（見下方），
      proxy 路徑（download_opts）只剩直接呼叫 /api/download 時才會用到。
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Any

from ..core.models import VideoInfo
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

#: 轉發 googlevideo 時帶的標頭（照 v8i8 /api/dl-stream：Referer＝來源網站）
_CDN_HEADERS = {"Referer": "https://www.youtube.com/", "Origin": "https://www.youtube.com"}


class YoutubeResolver(YtDlpResolver):
    name = "youtube"
    label = "YouTube"
    hosts = ("youtube.com", "youtu.be", "youtube-nocookie.com")
    default_mode = "relay"

    # 解析用的參數（下載另外用 download_opts，見檔頭說明）
    #
    # 雲端 IP 的兩層難關：
    #   ① JS 驗證（nsig）→ 靠 Deno 解（Dockerfile 已裝）
    #   ② 機器人判定「Sign in to confirm you're not a bot」→ 靠 cookies 解
    #      （公開的 Invidious／Piped 代理 2026-09 已全數失效，v8i8 也是這樣記錄的）
    ytdlp_extra: dict[str, Any] = {
        "http_headers": _BROWSER_HEADERS,
        # 加入 embedded 系列：它們本來就是給第三方網站內嵌用的，風控較寬
        "extractor_args": {"youtube": {"player_client": ["all"]}},
        "js_runtimes": {"deno": {}},
        "retry_sleep": "extractor:exp=1:20",
        "fragment_retries": 10,
    }

    def download_opts(self) -> dict[str, Any]:
        """下載專用參數：照 v8i8 用 App 身分，避開 web/tv 網址的 PO token（403）。"""
        return {
            "extractor_args": {"youtube": {
                "player_client": ["ios", "android", "android_embedded", "web"]}},
            "js_runtimes": {"deno": {}},
            "retry_sleep": "extractor:exp=1:20",
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 8,
        }

    @staticmethod
    def _env_cookiefile() -> str | None:
        """從 `YT_COOKIES_JSON` 環境變數產生 cookie 檔（v8i8 的備案做法）。

        為什麼要這個：Railway 的雲端 IP 會被 YouTube 判定成機器人，
        帶上真實帳號的 cookies 才能過。設定格式（Railway 環境變數）：
            YT_COOKIES_JSON = {"SID":"...","HSID":"...","SSID":"...", ...}
        """
        raw = os.environ.get("YT_COOKIES_JSON", "").strip()
        if not raw:
            return None
        try:
            import json

            pairs = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(pairs, dict) or not pairs:
            return None
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="yt_ck_")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for name, value in pairs.items():
                f.write(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
        return path

    async def resolve(self, url: str) -> VideoInfo:
        """解析後改成「原樣轉發」：無聲的純影像格式附上音軌，伺服器合併成有聲 mp4。"""
        info = await super().resolve(url)
        audio = next((f for f in info.formats if f.audio), None)
        for f in info.formats:
            f.mode = "relay"
            f.headers = dict(_CDN_HEADERS)
            silent = (f.acodec or "none").lower() == "none"
            if not f.audio and silent and audio is not None:
                f.audio_url = audio.url
        return info

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
