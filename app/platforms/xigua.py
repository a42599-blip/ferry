"""西瓜視頻（Ixigua／抖音系）解析。

**不需要登入、也不需要 cookies**。

西瓜與抖音同屬字節跳動，分享出來的短網址是 **iesdouyin.com/xg/video/<id>**，
這個 `<id>` 就是**抖音的 aweme_id** → 直接走抖音的 a_bogus 官方 API。

順序：
  1) iesdouyin.com / ixigua.com 的 `<id>` → 抖音官方 API（`_douyin_shared`）
  2) yt-dlp（有 cookies 時）
"""
from __future__ import annotations

import re

from ..core.errors import PlatformError
from ._douyin_shared import resolve_via_douyin
from ._ytdlp import YtDlpResolver

#: 只認「西瓜自己的」路徑：
#:   - ixigua.com/<id>、ixigua.com/video/<id>
#:   - iesdouyin.com/**xg**/video/<id>（有 /xg/ 才是西瓜分享）
_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?ixigua\.com/"
    r"|https?://(?:www\.|m\.)?iesdouyin\.com/xg/",
    re.I,
)
_ID_RE = re.compile(r"/xg/video/(\d{15,25})|ixigua\.com/(?:video/)?(\d{15,25})")

class XiguaResolver(YtDlpResolver):
    name = "xigua"
    label = "西瓜視頻"
    hosts = ("ixigua.com", "iesdouyin.com")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str):
        aweme_id = self._aweme_id(url)
        if aweme_id:
            try:
                info = await resolve_via_douyin(f"https://www.douyin.com/video/{aweme_id}")
                if info is not None:
                    info.platform = self.name
                    info.extra["route"] = "douyin-api"
                    return info
            except PlatformError:
                # 抖音 API 失敗（例如影片只在西瓜上架）→ 換 yt-dlp 試
                pass
            except Exception:  # noqa: BLE001
                pass
        # yt-dlp 只認得 douyin.com/video/<id>（iesdouyin 會說 Unsupported URL）
        canonical = (f"https://www.douyin.com/video/{aweme_id}" if aweme_id else url)
        return await YtDlpResolver.resolve(self, canonical)

    @staticmethod
    def _aweme_id(url: str) -> str | None:
        m = _ID_RE.search(url)
        if not m:
            return None
        return m.group(1) or m.group(2)
