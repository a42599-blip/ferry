"""西瓜視頻（Ixigua）解析（yt-dlp）。

同字節系 CDN（開 CORS）→ 可走前端 fetch→blob（零伺服器流量）。
"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.|m\.)?ixigua\.com/", re.I)


class XiguaResolver(YtDlpResolver):
    name = "xigua"
    label = "西瓜視頻"
    hosts = ("ixigua.com",)
    default_mode = "fetch"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
