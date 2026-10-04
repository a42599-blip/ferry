"""西瓜視頻（Ixigua／抖音系）解析。

**不需要登入、也不需要 cookies**。

西瓜與抖音同屬字節跳動，分享出來的短網址是 **iesdouyin.com/xg/video/<id>**，
這個 `<id>` 就是**抖音的 aweme_id** → 直接走抖音的 a_bogus 官方 API。

⚠️ 小羅 2026-10-04：西瓜 App 分享有時也是 **v.douyin.com/xxxx** 短連結（跟抖音共用），
只憑短連結分不出來 → 要**展開看落點**（跳到 iesdouyin.com/xg/ 或 ixigua.com 的才是西瓜），
否則後台統計會把它誤記成抖音。

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

#: 抖音短連結（v.douyin.com/xxxx／vm.douyin.com）：抖音與西瓜共用 → 要展開才知道落點。
_SHORT_RE = re.compile(r"https?://(?:v|vm)\.douyin\.com/", re.I)
_XG_FINAL_RE = re.compile(r"iesdouyin\.com/xg/|ixigua\.com/", re.I)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")
_EXPAND_CACHE: dict[str, str] = {}


async def _expand_short(url: str) -> str:
    """把 v.douyin.com 短連結展開成落點網址（用來分辨抖音／西瓜）。

    結果會快取（同一個短連結只查一次）；查不到就回原網址（交給抖音處理）。
    """
    key = (url or "").strip()
    if not _SHORT_RE.match(key):
        return url
    hit = _EXPAND_CACHE.get(key)
    if hit:
        return hit
    final = key
    try:
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0,
                                     headers={"User-Agent": _UA}) as cli:
            final = str((await cli.get(key)).url)
    except Exception:  # noqa: BLE001 — 展開失敗就當一般抖音處理
        final = key
    if len(_EXPAND_CACHE) > 500:
        _EXPAND_CACHE.clear()
    _EXPAND_CACHE[key] = final
    return final


class XiguaResolver(YtDlpResolver):
    name = "xigua"
    label = "西瓜視頻"
    hosts = ("ixigua.com", "iesdouyin.com")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        if _URL_RE.search(url):
            return True
        # 抖音短連結要展開才知道是不是西瓜（v.douyin.com 兩邊共用）
        if _SHORT_RE.match(url):
            return bool(_XG_FINAL_RE.search(await _expand_short(url)))
        return False

    async def resolve(self, url: str):
        url = await _expand_short(url)
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
