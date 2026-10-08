"""脆（Threads）解析 — 自製（yt-dlp 沒有 extractor）。

Threads 貼文頁的 HTML 內嵌 JSON 會帶 `video_versions`（Meta CDN），
CDN 開 CORS → 走前端 fetch→blob（零伺服器流量）。

策略（規格書第 7 章「Threads」）：
  1) 抓貼文 HTML，找 `video_versions` / `image_versions2`
  2) 退回 og:video / og:image meta
"""
from __future__ import annotations

import html as html_mod
import json
import re

from ..core.errors import PlatformChanged, PlatformError
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ssr import meta_content, render_html
from .base import Resolver

_URL_RE = re.compile(r"https?://(?:www\.)?(?:threads\.net|threads\.com)/", re.I)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ThreadsResolver(Resolver):
    name = "threads"
    label = "脆 Threads"
    hosts = ("threads.net", "threads.com")

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        # ① 先用真瀏覽器渲染（Threads 的資料只在渲染後才有）
        page = ""
        try:
            page = await render_html(
                url,
                context_key="threads",
                # 等 `video_dash_manifest`（或純圖的 image_versions2）；❌ 不要放 video_versions：
                # 它會先出現就返回 → 較晚載入的音軌永遠拿不到（小羅 2026-10-09）。
                wait_for=["video_dash_manifest", "image_versions2"],
                tries=22,
                user_agent=_UA,
            )
        except Exception:  # noqa: BLE001 — 退回純 HTTP
            page = ""

        # ② 退回純 HTTP（部分公開貼文其實直接抓就有）
        if not page or "video_versions" not in page:
            headers = {
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
            async with HttpClient(ua=_UA, timeout=25) as http:
                resp = await http.get(url, headers=headers)
                plain = resp.text
            if "video_versions" in plain or not page:
                page = plain

        title = _meta(page, "og:title") or _meta(page, "twitter:title") or "Threads 貼文"
        cover = _meta(page, "og:image") or _meta(page, "twitter:image") or ""

        fmts: list[Format] = []

        # ① video_versions（Meta 標準結構）
        for blob in re.findall(r'"video_versions"\s*:\s*(\[.*?\])', page, re.S):
            try:
                items = json.loads(blob)
            except json.JSONDecodeError:
                continue
            for v in items:
                u = (v.get("url") or "").replace("\\/", "/")
                if not u.startswith("http"):
                    continue
                h = v.get("height") or None
                fmts.append(
                    Format(
                        id=f"v{h or len(fmts)}",
                        label=_quality(h),
                        url=u,
                        height=h,
                        width=v.get("width") or None,
                        quality_score=h or 80,
                        mode="fetch",
                        headers={"Referer": "https://www.threads.com/", "User-Agent": _UA},
                    )
                )

        # ② og:video 後備
        if not fmts:
            ogv = _meta(page, "og:video") or _meta(page, "og:video:secure_url")
            if ogv:
                fmts.append(
                    Format(
                        id="origin",
                        label="原畫",
                        url=ogv,
                        quality_score=80,
                        mode="fetch",
                        headers={"Referer": "https://www.threads.com/", "User-Agent": _UA},
                    )
                )

        # ③ ★DASH 純音軌（小羅 2026-10-09）★
        #   Threads／IG 的 `video_versions` 只有「影像軌」→ 不填 audio_url 就會下載到**無聲影片**。
        #   音訊在貼文頁的 `video_dash_manifest`（DASH XML）→ 找 mimeType="audio..." 的 <BaseURL>。
        #   共用層看到 audio_url 會「自動改 relay ＋ 伺服器 ffmpeg 合併」（B 站同一套，已驗證）。
        try:
            _dm = re.search(r'"(?:video_)?dash_manifest"\s*:\s*"((?:[^"\\]|\\.)*)"', page)
            if _dm:
                _xml = json.loads('"' + _dm.group(1) + '"')
                for _blk in re.findall(r"<AdaptationSet.*?</AdaptationSet>", _xml, re.S):
                    if 'mimeType="audio' not in _blk:
                        continue
                    _b = re.search(r"<BaseURL>(.*?)</BaseURL>", _blk, re.S)
                    if not _b:
                        continue
                    _au = _b.group(1).strip().replace("\\/", "/")
                    if _au.startswith("http"):
                        for _f in fmts:
                            _f.audio_url = _au
                    break
        except Exception:  # noqa: BLE001 — 找不到音軌就照舊（至少影像能下）
            pass

        # ④ 去重
        seen: set[str] = set()
        uniq: list[Format] = []
        for f in sorted(fmts, key=lambda x: -(x.quality_score or 0)):
            if f.url not in seen:
                seen.add(f.url)
                uniq.append(f)

        if not uniq:
            # ① 先判斷「內容是否未公開／需登入」→ 給客人正確的白話訊息（小羅 2026-10-06）
            low = page.lower()
            if ("並非對所有人開放" in page or "并非对所有人开放" in page
                    or "未開放所有人查看" in page or "未开放所有人查看" in page
                    or "特定受眾" in page or "特定受众" in page
                    or "部分受眾無法看到" in page or "部分受众无法看到" in page
                    or "此內容並未開放" in page or "此内容并未开放" in page
                    or "not available to everyone" in low):
                raise PlatformError("Threads 這則貼文未開放所有人查看（特定受眾）",
                                    platform=self.name, code="NOT_PUBLIC")
            # 可能是純文字／圖片貼文
            imgs = re.findall(r'"image_versions2"\s*:\s*\{\s*"candidates"\s*:\s*\[\s*\{(.*?)\}', page, re.S)
            for i, blob in enumerate(imgs[:10], 1):
                u = _search(r'"url"\s*:\s*"([^"]+)"', blob)
                if u:
                    u = u.replace("\\/", "/")
                    uniq.append(
                        Format(id=f"img{i}", label=f"圖 {i}", url=u, ext="jpg",
                               quality_score=50 - i, mode="fetch")
                    )
            if not uniq:
                raise PlatformChanged(
                    "Threads 這個貼文找不到影片（可能是純文字或需登入）", platform=self.name
                )

        return VideoInfo(platform=self.name, title=html_mod.unescape(title),
                         cover=cover, source_url=url, formats=uniq)


def _quality(h) -> str:
    try:
        h = int(h)
    except (TypeError, ValueError):
        return "原畫"
    for t, lab in ((2160, "4K"), (1440, "2K"), (1080, "1080P"), (720, "720P"), (480, "480P")):
        if h >= t:
            return lab
    return f"{h}P" if h else "原畫"



def _meta(page: str, prop: str) -> str:
    """安全版 meta 讀取（見 _ssr.meta_content 的說明）。"""
    return meta_content(page, prop)


def _search(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else ""
