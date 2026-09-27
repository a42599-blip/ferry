"""西瓜視頻（Ixigua）解析。

**不需要 cookies**（與 v8i8 同一套作法）：
    西瓜與抖音同屬字節跳動，影片 ID 在抖音的 Web API 通用
    → 把 ixigua 網址轉成 `douyin.com/video/<id>`，走抖音的 a_bogus 官方流程。

順序：
  1) 轉成抖音網址 → 用抖音 resolver（a_bogus ＋ ttwid）
  2) yt-dlp（有 cookies 時）
"""
from __future__ import annotations

import re

from ..core.errors import PlatformError
from ._douyin_shared import resolve_via_douyin
from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.|m\.)?ixigua\.com/", re.I)
_ID_RE = re.compile(r"ixigua\.com/(\d{15,25})")


class XiguaResolver(YtDlpResolver):
    name = "xigua"
    label = "西瓜視頻"
    hosts = ("ixigua.com",)
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str):
        m = _ID_RE.search(url)
        if m:
            try:
                info = await resolve_via_douyin(f"https://www.douyin.com/video/{m.group(1)}")
                if info is not None:
                    info.platform = self.name
                    info.extra["route"] = "douyin-api"
                    return info
            except PlatformError:
                raise
            except Exception:  # noqa: BLE001 — 換下一條路
                pass
        return await YtDlpResolver.resolve(self, url)
