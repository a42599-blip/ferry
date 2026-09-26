"""TikTok 解析（走 tikwm 第三方 API）。

特點：CDN 開 CORS → 可「檔案零伺服器流量」（前端 fetch→blob）。
（規格書第 7 章：已實測 206 ＋ CORS `*`）
"""
from __future__ import annotations

import re

from ..core.errors import PlatformChanged, PlatformError, PlatformTimeout
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from .base import Resolver

_API = "https://www.tikwm.com/api/"
_URL_RE = re.compile(r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/", re.I)


class TiktokResolver(Resolver):
    name = "tiktok"
    label = "TikTok"
    hosts = ("tiktok.com",)

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        try:
            async with HttpClient() as http:
                data = await http.get_json(_API, params={"url": url, "hd": 1})
        except Exception as exc:  # noqa: BLE001
            raise PlatformTimeout(f"TikTok 解析逾時：{exc}", platform=self.name) from exc

        if not isinstance(data, dict) or data.get("code") != 0:
            raise PlatformChanged(
                f"TikTok 回傳異常：{str(data)[:120]}", platform=self.name
            )
        d = data.get("data") or {}
        return self._build(url, d)

    def _build(self, url: str, d: dict) -> VideoInfo:
        title = (d.get("title") or "").strip() or "TikTok 影片"
        cover = d.get("origin_cover") or d.get("cover") or ""
        author = (d.get("author") or {}).get("unique_id")
        duration = d.get("duration")

        fmts: list[Format] = []
        play = d.get("play")          # 無水印（標清）
        hd = d.get("hdplay")          # 無水印（高清）
        music = d.get("music")

        if hd:
            fmts.append(Format(id="hd", label="高清", url=_abs(hd), height=1080,
                               size=d.get("hd_size"), quality_score=90, mode="fetch"))
        if play:
            fmts.append(Format(id="origin", label="原畫", url=_abs(play), height=720,
                               size=d.get("size"), quality_score=80, mode="fetch"))
        if music:
            fmts.append(Format(id="audio", label="純音訊", url=_abs(music), audio=True,
                               ext="mp3", quality_score=10, mode="fetch"))

        if not fmts:
            raise PlatformError("TikTok 沒有可下載的檔案", platform=self.name)

        return VideoInfo(platform=self.name, title=title, cover=cover, source_url=url,
                         formats=fmts, duration=duration, author=author,
                         extra={"cover_fallback": d.get("cover")})


def _abs(u: str) -> str:
    """tikwm 有時回相對路徑。"""
    if u.startswith("http"):
        return u
    return "https://www.tikwm.com" + (u if u.startswith("/") else "/" + u)
