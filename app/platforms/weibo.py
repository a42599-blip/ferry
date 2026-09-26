"""微博（Weibo）解析。

策略：
  1) **匿名訪客 cookie ＋ 官方 ajax API**（不用登入；見 `_weibo_visitor.py`）
  2) yt-dlp（有 cookies 時最準）

微博影片的實際網址藏在 `page_info.media_info`：
  - `stream_url` / `stream_url_hd`（mp4）
  - 或 `media_info.variants` / `playback_list`（新版）
"""
from __future__ import annotations

import re

from ..core.errors import PlatformError
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from . import _weibo_visitor as wv
from ._ytdlp import YtDlpResolver, quality_label

_SHOW = "https://weibo.com/ajax/statuses/show"
_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|weibo\.|video\.)?(?:weibo\.com|weibo\.cn)/|https?://t\.cn/", re.I
)
_NUM_ID = re.compile(r"(?:tv/show/1034:|detail/|status/)(\d{15,20})")
_MBLOG = re.compile(r"weibo\.com/(?:\d{6,12}|u/\d{6,12})/([0-9A-Za-z]{8,12})")


class WeiboResolver(YtDlpResolver):
    name = "weibo"
    label = "微博"
    hosts = ("weibo.com", "weibo.cn", "t.cn")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    # ── 主流程：匿名 API → yt-dlp ─────────────────────
    async def resolve(self, url: str) -> VideoInfo:
        try:
            info = await self._via_ajax(url)
            if info is not None:
                return info
        except PlatformError:
            pass
        except Exception:  # noqa: BLE001
            pass
        return await YtDlpResolver.resolve(self, url)

    # ── 路線①：匿名訪客 API ──────────────────────────
    async def _via_ajax(self, url: str) -> VideoInfo | None:
        mid = await self._status_id(url)
        if not mid:
            return None

        headers = await wv.ajax_headers()
        async with HttpClient(ua=headers.get("User-Agent"), timeout=20) as http:
            resp = await http.get(_SHOW, params={"id": mid}, headers=headers)
            if resp.status_code != 200:
                return None
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                return None

        if not isinstance(data, dict) or not data.get("id"):
            return None
        return self._build(url, data)

    async def _status_id(self, url: str) -> str | None:
        m = _NUM_ID.search(url)
        if m:
            return m.group(1)
        m = _MBLOG.search(url)
        if m:
            mid = wv.mblogid_to_mid(m.group(1))
            return mid or None
        return None

    def _build(self, url: str, d: dict) -> VideoInfo:
        page = d.get("page_info") or {}
        media = page.get("media_info") or {}

        title = (d.get("text_raw") or "").strip() or "微博影片"
        title = re.sub(r"\s+", " ", title)[:120]
        author = (d.get("user") or {}).get("screen_name")
        cover = (
            media.get("cover_image_url")
            or media.get("page_pic")
            or ((page.get("page_pic") or {}).get("url"))
            or ""
        )
        duration = None
        if media.get("duration_time"):
            try:
                duration = int(float(media["duration_time"]))
            except (TypeError, ValueError):
                duration = None

        candidates: list[tuple[str, str, int]] = []   # (url, label, score)

        for key, label, score in (
            ("stream_url_hd", "高清", 95),
            ("stream_url", "原畫", 85),
            ("mp4_hd_url", "高清", 95),
            ("mp4_sd_url", "標清", 70),
        ):
            u = media.get(key)
            if u:
                candidates.append((u, label, score))

        # 新版：variants / playback_list
        for coll in (media.get("variants"), (media.get("playback_list") or [])):
            for item in coll or []:
                if not isinstance(item, dict):
                    continue
                u = item.get("url") or item.get("play_url") or ""
                if not u:
                    continue
                h = item.get("height") or item.get("height_pixel") or 0
                try:
                    h = int(h)
                except (TypeError, ValueError):
                    h = 0
                candidates.append((u, quality_label(h) if h else "原畫", h or 50))

        fmts: list[Format] = []
        seen: set[str] = set()
        for u, label, score in candidates:
            if u in seen:
                continue
            seen.add(u)
            fmts.append(
                Format(
                    id=f"wb{len(fmts)}",
                    label=label,
                    url=u,
                    quality_score=score,
                    mode="proxy",
                    headers={"Referer": "https://weibo.com/"},
                )
            )

        if not fmts:
            # 這則貼文沒有直接可用的影片網址 → 換 yt-dlp 路線
            return None

        fmts.sort(key=lambda f: -(f.quality_score or 0))
        return VideoInfo(
            platform=self.name, title=title, cover=cover, source_url=url,
            formats=fmts, duration=duration, author=author,
            extra={"route": "visitor-api"},
        )
