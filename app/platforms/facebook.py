"""Facebook 解析（yt-dlp）。"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.watch|fb\.gg)/", re.I
)


class FacebookResolver(YtDlpResolver):
    name = "facebook"
    label = "Facebook"
    hosts = ("facebook.com", "fb.watch", "fb.gg")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
