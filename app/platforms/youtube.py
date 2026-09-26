"""YouTube 解析（yt-dlp）。

⚠️ YouTube 是 **IP 敏感** 平台（規格書第 7 章）：
  - CDN 擋 Origin → 一律走伺服器代理（mode="proxy"，串流不落地）
  - 高畫質是「分離軌」→ 需伺服器端 ffmpeg 合併（Dockerfile 已裝 ffmpeg）
"""
from __future__ import annotations

import re
from typing import Any

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
    #    解析（列畫質）：用預設 client → 拿得到完整畫質（4K/2K/1080P…）。
    #    下載：預設 client 會被 403 → 改用實測可用的 client 輪替。
    ytdlp_extra: dict[str, Any] = {
        "extractor_args": {"youtube": {"player_client": ["default", "web_safari"]}},
    }

    def download_opts(self) -> dict[str, Any]:
        return {
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_safari", "android", "tv", "mweb", "default"]
                }
            }
        }

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
