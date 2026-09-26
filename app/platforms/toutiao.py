"""今日頭條（Toutiao）解析（yt-dlp）。"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.|m\.)?(?:toutiao\.com|ixigua\.com)/", re.I)


class ToutiaoResolver(YtDlpResolver):
    name = "toutiao"
    label = "今日頭條"
    hosts = ("toutiao.com",)
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        # 只認 toutiao.com；ixigua 由 XiguaResolver 先攔
        return bool(re.search(r"https?://(?:www\.|m\.)?toutiao\.com/", url, re.I))
