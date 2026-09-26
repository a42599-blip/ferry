"""YouTube 解析（yt-dlp）。

⚠️ YouTube 是 **IP 敏感** 平台（規格書第 7 章）：
  - CDN 擋 Origin → 一律走伺服器代理（mode="proxy"，串流不落地）
  - 高畫質是「分離軌」→ 需伺服器端 ffmpeg 合併（Dockerfile 已裝 ffmpeg）
"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", re.I
)


class YoutubeResolver(YtDlpResolver):
    name = "youtube"
    label = "YouTube"
    hosts = ("youtube.com", "youtu.be", "youtube-nocookie.com")
    default_mode = "proxy"

    # ⚠️ YouTube 是 **IP 敏感** 平台（規格書第 7 章）。
    #    實測：本機（住宅 IP）用 android／web_safari 可下載；資料中心 IP 可能被 CDN 403。
    #    用多個 client 輪流嘗試，提高命中率。
    ytdlp_extra = {
        "extractor_args": {
            "youtube": {"player_client": ["web_safari", "android", "tv", "mweb"]}
        },
    }

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
