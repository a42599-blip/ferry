"""小紅書（Xiaohongshu）解析。

**不需要登入、也不需要 cookies**（與 v8i8 同一套作法）。

順序：
  1) 直解 HTML（**iPhone 身分**）← 主力，最快
     小紅書的影片網址在頁面 JSON 裡，欄位名會改（`masterUrl` / `backupUrls` /
     `contentUrl` / `originVideoKey`），所以用多組樣式輪流試。
  2) 真瀏覽器渲染（有些貼文要 JS 跑完才有資料）
  3) yt-dlp（有 cookies 時）
"""
from __future__ import annotations

import re

from ..core.errors import PlatformError
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ssr import meta_content, page_video_info
from ._ytdlp import YtDlpResolver

#: 短連結有三個網域：xhslink.com / xhslink.cn / xhs.link
_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?xiaohongshu\.com/"
    r"|https?://(?:www\.)?xhslink\.(?:com|cn)/"
    r"|https?://xhs\.link/",
    re.I,
)
_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

#: 影片網址在 HTML 裡可能長這樣（多組樣式輪流試，欄位常改）
_VIDEO_PATTERNS = (
    r'"masterUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
    r'"backupUrls"\s*:\s*\[\s*"([^"]+\.mp4[^"]*)"',
    r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
    r'"originVideoKey"\s*:\s*"([^"]+\.mp4[^"]*)"',
    r'"url"\s*:\s*"(https://[^"]+\.mp4[^"]*)"',
    r'<video[^>]+src="([^"]+\.mp4[^"]*)"',
)
_ORIGIN_KEY = re.compile(r'"originVideoKey"\s*:\s*"([^"]+)"')
_HDRS = {"Referer": "https://www.xiaohongshu.com/", "User-Agent": _IPHONE_UA}


class XiaohongshuResolver(YtDlpResolver):
    name = "xiaohongshu"
    label = "小紅書"
    hosts = ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "xhs.link")
    default_mode = "fetch"          # 小紅書 CDN 開 CORS（規格書第 8 章）
    ytdlp_extra = {"http_headers": {"User-Agent": _IPHONE_UA}}

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        # ① 直解 HTML（最快）
        try:
            info = await self._via_html(url)
            if info:
                return info
        except PlatformError:
            raise
        except Exception:  # noqa: BLE001
            pass

        # ② 真瀏覽器渲染（筆記頁的資料是 JS 載入的）
        try:
            data = await page_video_info(url, context_key="xiaohongshu",
                                         wait_for=["masterUrl", "originVideoKey"],
                                         tries=22, user_agent=_IPHONE_UA)
            if data.get("urls"):
                fmts = [
                    Format(id=f"xhs{i}", label="原畫" if i == 0 else f"備援線路 {i}",
                           url=u, quality_score=90 - i, mode="fetch", headers=dict(_HDRS))
                    for i, u in enumerate(data["urls"][:6])
                ]
                return VideoInfo(platform=self.name,
                                 title=data.get("title") or "小紅書影片",
                                 cover=data.get("poster") or "", source_url=url,
                                 formats=fmts, extra={"route": "browser"})
        except Exception:  # noqa: BLE001
            pass

        # ③ yt-dlp（有 cookies 時）
        return await YtDlpResolver.resolve(self, url)

    async def _via_html(self, url: str) -> VideoInfo | None:
        async with HttpClient(ua=_IPHONE_UA, timeout=20) as http:
            resp = await http.get(url, headers=_HDRS)
            html = resp.text
            final = str(resp.url)
        info = self._build(url, html)
        if info:
            info.extra["final_url"] = final
            return info
        return None

    def _build(self, url: str, html: str) -> VideoInfo | None:
        # 小紅書會把網址轉義（\u002F / \/）→ 先還原
        flat = (html.replace("\\u002F", "/").replace("\\u002F", "/")
                    .replace("\\/", "/"))

        found: list[str] = []
        for pat in _VIDEO_PATTERNS:
            for u in re.findall(pat, flat):
                u = u.strip()
                if u.startswith("http") and u not in found:
                    found.append(u)

        # originVideoKey 是「影片代號」，要自己組 CDN 網址
        for key in _ORIGIN_KEY.findall(flat):
            if key.startswith("http") and key not in found:
                found.append(key)
            elif "/" not in key and len(key) > 10:
                u = f"https://sns-video-bd.xhscdn.com/{key}"
                if u not in found:
                    found.append(u)

        if not found:
            return None

        title = (meta_content(html, "og:title")
                 or _search(r'<title[^>]*>([^<]{1,120})</title>', html)
                 or "小紅書影片")
        title = title.removesuffix(" - 小紅書").strip() or "小紅書影片"
        cover = meta_content(html, "og:image")
        author = _search(r'"nickname"\s*:\s*"([^"]{1,40})"', flat)

        fmts = [
            Format(id=f"xhs{i}", label="原畫" if i == 0 else f"備援線路 {i + 1}",
                   url=u, quality_score=90 - i, mode="fetch", headers=dict(_HDRS))
            for i, u in enumerate(found[:6])
        ]
        return VideoInfo(platform=self.name, title=title, cover=cover, source_url=url,
                         formats=fmts, author=author, extra={"route": "html"})


def _search(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    return m.group(1).strip() if m else ""
