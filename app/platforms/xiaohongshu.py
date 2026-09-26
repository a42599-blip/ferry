"""小紅書（Xiaohongshu）解析。

先走 yt-dlp（有內建 extractor）；若失敗再退回「直解 HTML」
（規格書第 7 章：欄位名會改，曾改過 `masterUrl`）。

小紅書 CDN 開 CORS → 前端 fetch→blob。
"""
from __future__ import annotations

import json
import re

from ..core.errors import PlatformChanged, PlatformError
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ssr import render_html
from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.|m\.)?xiaohongshu\.com/|https?://xhslink\.com/", re.I)
_XHS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class XiaohongshuResolver(YtDlpResolver):
    name = "xiaohongshu"
    label = "小紅書"
    hosts = ("xiaohongshu.com", "xhslink.com")
    default_mode = "fetch"
    ytdlp_extra = {"http_headers": {"User-Agent": _XHS_UA}}

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        try:
            return await super().resolve(url)
        except Exception:  # noqa: BLE001 — yt-dlp 失敗 → 退回直解 HTML
            return await self._resolve_html(url)

    async def _resolve_html(self, url: str) -> VideoInfo:
        # 先用真瀏覽器渲染（小紅書的筆記資料在渲染後才有；純 HTTP 多為空殼）
        html = ""
        try:
            html = await render_html(
                url,
                context_key="xiaohongshu",
                wait_for=["__INITIAL_STATE__", "masterUrl", "originVideoKey"],
                tries=25,
                user_agent=_XHS_UA,
            )
        except Exception:  # noqa: BLE001
            html = ""

        if "masterUrl" not in html and "originVideoKey" not in html:
            headers = {"User-Agent": _XHS_UA, "Referer": "https://www.xiaohongshu.com/"}
            async with HttpClient(ua=_XHS_UA) as http:
                try:
                    html = await http.get_text(url, headers=headers)
                except Exception:  # noqa: BLE001
                    pass

        # 頁面內嵌 JSON：window.__INITIAL_STATE__
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", html, re.S)
        blob = m.group(1) if m else html

        title = _search(r'"title":"(.*?)"', blob) or "小紅書影片"
        cover = _search(r'"imageList":\[\{"urlDefault":"(.*?)"', blob) or _search(
            r'"urlDefault":"(.*?)"', blob
        )

        urls: list[str] = []
        # 新版 masterUrl / 舊版 url
        urls += re.findall(r'"masterUrl":"(.*?)"', blob)
        urls += re.findall(r'"backupUrls":\[(.*?)\]', blob)
        origin = re.findall(r'"originVideoKey":"(.*?)"', blob)

        fmts: list[Format] = []
        seen: set[str] = set()
        for u in urls:
            for cand in re.findall(r'"(https?:[^"]+?)"', u) or [u]:
                cand = cand.replace("\\u002F", "/").replace("\\/", "/")
                if not cand.startswith("http") or cand in seen:
                    continue
                seen.add(cand)
                fmts.append(
                    Format(
                        id=f"xhs{len(fmts)}",
                        label="原畫" if not fmts else "備援線路",
                        url=cand,
                        quality_score=90 - len(fmts),
                        mode="fetch",
                    )
                )
        for key in origin:
            u = key.replace("\\u002F", "/").replace("\\/", "/")
            if u.startswith("http") and u not in seen:
                seen.add(u)
                fmts.append(
                    Format(id="origin", label="原畫", url=u, quality_score=100, mode="fetch")
                )

        if not fmts:
            raise PlatformChanged(
                "小紅書頁面找不到影片網址（可能改版或需登入）", platform=self.name
            )

        for f in fmts:
            f.headers = {"Referer": "https://www.xiaohongshu.com/", "User-Agent": _XHS_UA}

        return VideoInfo(
            platform=self.name,
            title=title,
            cover=cover or "",
            source_url=url,
            formats=fmts,
        )


def _search(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else ""
