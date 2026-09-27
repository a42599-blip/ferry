"""今日頭條（Toutiao）解析。

**不需要登入、也不需要 cookies**。

今日頭條的影片是 JS 載入的（純 HTTP 只會拿到空殼），所以用真瀏覽器：
  1) 讀 `<video>` 元素的 src
  2) 抓頁面裡的 `main_url` / `backup_url`（⚠️ 這些值是 URL 編碼的，要解碼）
  3) yt-dlp（有 cookies 時）
"""
from __future__ import annotations

import re

from ..core.errors import PlatformError
from ..core.models import Format, VideoInfo
from ._ssr import page_video_info
from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.|m\.)?toutiao\.com/", re.I)


class ToutiaoResolver(YtDlpResolver):
    name = "toutiao"
    label = "今日頭條"
    hosts = ("toutiao.com",)
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        try:
            info = await self._via_page(url)
            if info is not None:
                return info
        except PlatformError:
            raise
        except Exception:  # noqa: BLE001
            pass
        return await YtDlpResolver.resolve(self, url)

    async def _via_page(self, url: str) -> VideoInfo | None:
        data = await page_video_info(url, context_key="toutiao",
                                     wait_for=["main_url", "videoResource"])
        urls = data.get("urls") or []
        if not urls:
            return None
        seen: set[str] = set()
        fmts: list[Format] = []
        for i, u in enumerate(urls[:6]):
            if u in seen:
                continue
            seen.add(u)
            fmts.append(Format(id=f"tt{i}", label="原畫" if i == 0 else f"備援線路 {i}",
                               url=u, quality_score=90 - i, mode="proxy",
                               headers={"Referer": "https://www.toutiao.com/"}))
        if not fmts:
            return None
        return VideoInfo(
            platform=self.name,
            title=(data.get("title") or "今日頭條影片"),
            cover=data.get("poster") or "",
            source_url=url,
            formats=fmts,
            extra={"route": "browser"},
        )
