"""抖音解析。

策略（規格書第 7 章）：
  1) tikwm 第三方 API（最快、最穩，支援無水印）
  2) 🔧 TODO：官方 API（a_bogus 簽章）作為備援
  3) 🔧 TODO：yt-dlp 作為最後備援

抖音的 `bit_rate[]` 陣列會帶多種畫質（gear_name）→ 全部列給使用者選。
"""
from __future__ import annotations

import re

from ..core.errors import PlatformChanged, PlatformError, PlatformTimeout
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from .base import Resolver

_API = "https://www.tikwm.com/api/"
_URL_RE = re.compile(
    r"https?://(?:www\.|v\.|vm\.|m\.)?(?:douyin\.com|iesdouyin\.com)/", re.I
)


class DouyinResolver(Resolver):
    name = "douyin"
    label = "抖音"
    hosts = ("douyin.com", "iesdouyin.com")

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        try:
            async with HttpClient() as http:
                data = await http.get_json(_API, params={"url": url, "hd": 1})
        except Exception as exc:  # noqa: BLE001
            raise PlatformTimeout(f"抖音解析逾時：{exc}", platform=self.name) from exc

        if not isinstance(data, dict) or data.get("code") != 0:
            raise PlatformChanged(
                f"抖音回傳異常（可能改版）：{str(data)[:120]}", platform=self.name
            )

        d = data.get("data") or {}
        return self._build(url, d)

    def _build(self, url: str, d: dict) -> VideoInfo:
        title = (d.get("title") or "").strip() or "抖音影片"
        cover = d.get("origin_cover") or d.get("cover") or ""
        author = (d.get("author") or {}).get("unique_id") or (d.get("author") or {}).get("nickname")
        duration = d.get("duration")

        fmts: list[Format] = []
        hd = d.get("hdplay")
        play = d.get("play")
        images = d.get("images")  # 圖集

        # 圖集（多張圖）
        if images and isinstance(images, list):
            for i, img in enumerate(images, 1):
                fmts.append(Format(id=f"img{i}", label=f"圖 {i}", url=img, ext="jpg",
                                   quality_score=100 - i, mode="direct"))

        if hd:
            fmts.append(Format(id="hd", label="高清", url=_abs(hd),
                               size=d.get("hd_size"), quality_score=90, mode="fetch"))
        if play:
            fmts.append(Format(id="origin", label="原畫", url=_abs(play),
                               size=d.get("size"), quality_score=80, mode="fetch"))

        music = d.get("music")
        if music:
            fmts.append(Format(id="audio", label="純音訊", url=_abs(music), audio=True,
                               ext="mp3", quality_score=10, mode="fetch"))

        if not fmts:
            raise PlatformError("抖音沒有可下載的檔案", platform=self.name)

        return VideoInfo(platform=self.name, title=title, cover=cover, source_url=url,
                         formats=fmts, duration=duration, author=author,
                         extra={"cover_fallback": d.get("cover"), "is_gallery": bool(images)})


def _abs(u: str) -> str:
    if u.startswith("http"):
        return u
    return "https://www.tikwm.com" + (u if u.startswith("/") else "/" + u)
