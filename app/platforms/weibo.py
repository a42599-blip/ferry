"""微博（Weibo）解析（yt-dlp）。"""
from __future__ import annotations

import re

from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|weibo\.|video\.)?(?:weibo\.com|weibo\.cn)/|https?://t\.cn/", re.I
)


class WeiboResolver(YtDlpResolver):
    name = "weibo"
    label = "微博"
    hosts = ("weibo.com", "weibo.cn", "t.cn")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))
