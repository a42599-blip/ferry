"""蝦皮（Shopee）解析 — 自製（yt-dlp 沒有 extractor）。

蝦皮影片出現在商品頁／短影音頁。策略：
  1) 抓 HTML，找 `"video_url"` / `"videoUrl"` / m3u8
  2) 退回 og:video meta
"""
from __future__ import annotations

import html as html_mod
import re

from ..core.errors import PlatformChanged
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ssr import meta_content
from .base import Resolver

#: 支援國別子網域（tw.shp.ee / shopee.tw / m.shopee.tw / sv.shopee.tw…）
_URL_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:shopee\.[a-z.]+|shp\.ee)/", re.I
)
#: 蝦皮短連結會導到 universal-link，真正的影片網址在 redir 參數裡
_UNIVERSAL = re.compile(r"[?&]redir=([^&]+)")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ShopeeResolver(Resolver):
    name = "shopee"
    label = "蝦皮"
    hosts = ("shopee.tw", "shp.ee")   # shp.ee 是短連結網域

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
        async with HttpClient(ua=_UA, timeout=25) as http:
            resp = await http.get(url, headers=headers)
            page = resp.text
            final = str(resp.url)
            # 短連結 → universal-link，真正網址在 redir 參數（要 URL 解碼）
            if "universal-link" in final:
                from urllib.parse import unquote

                m = _UNIVERSAL.search(final)
                if m:
                    real = unquote(m.group(1))
                    resp = await http.get(real, headers=headers)
                    page = resp.text

        title = _meta(page, "og:title") or "蝦皮影片"
        cover = _meta(page, "og:image") or ""

        found: list[str] = []
        for pat in (
            r'"video_url"\s*:\s*"([^"]+)"',
            r'"videoUrl"\s*:\s*"([^"]+)"',
            r'"playUrl"\s*:\s*"([^"]+)"',
            r'"url"\s*:\s*"(https?://[^"]+?\.m3u8[^"]*)"',
            r'"(https?://[^"]+?\.(?:mp4)[^"]*)"',
        ):
            found += re.findall(pat, page)

        uniq: list[str] = []
        for u in found:
            u = u.replace("\\u002F", "/").replace("\\/", "/")
            if u.startswith("http") and u not in uniq:
                uniq.append(u)

        fmts = [
            Format(
                id=f"shopee{i}",
                label="原畫" if i == 0 else f"線路 {i + 1}",
                url=u,
                ext="m3u8" if ".m3u8" in u else "mp4",
                quality_score=90 - i,
                # ⚠️ 用 relay（不是 proxy）—— 2026-09-27 實測踩到：
                #    proxy 會叫 yt-dlp 重抓蝦皮的網址 → 403 Forbidden。
                #    我們已經從分享頁拿到 CDN 網址了，直接轉發即可。
                mode="relay",
                headers={"Referer": "https://shopee.tw/", "User-Agent": _UA},
            )
            for i, u in enumerate(uniq[:6])
        ]
        if not fmts:
            raise PlatformChanged(
                "蝦皮頁面找不到影片（多數商品沒有影片，或已改版）", platform=self.name
            )

        return VideoInfo(platform=self.name, title=html_mod.unescape(title),
                         cover=cover, source_url=url, formats=fmts)



def _meta(page: str, prop: str) -> str:
    """安全版 meta 讀取。"""
    return meta_content(page, prop)
