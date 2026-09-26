"""X（Twitter）解析（yt-dlp）。"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.|mobile\.|m\.)?(?:twitter\.com|x\.com)/|https?://t\.co/", re.I)


class TwitterXResolver(YtDlpResolver):
    name = "x"
    label = "X"
    hosts = ("x.com", "twitter.com", "t.co")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
