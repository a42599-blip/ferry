"""Instagram 解析（yt-dlp）。"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/", re.I)


class InstagramResolver(YtDlpResolver):
    name = "instagram"
    label = "Instagram"
    hosts = ("instagram.com", "instagr.am")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
