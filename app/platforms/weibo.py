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
from ._ssr import page_video_info
from ._ytdlp import YtDlpResolver, quality_label

_SHOW = "https://weibo.com/ajax/statuses/show"
_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|weibo\.|video\.)?(?:weibo\.com|weibo\.cn)/|https?://t\.cn/", re.I
)
#: 支援多種形式：tv/show/1034:<mid>、detail/<mid>、status/<mid>、?fid=1034:<mid>
_NUM_ID = re.compile(r"(?:tv/show/1034:|fid=1034:|detail/|status/|/tv/)(\d{15,20})")
_MBLOG = re.compile(r"weibo\.com/(?:\d{6,12}|u/\d{6,12})/([0-9A-Za-z]{8,12})")


class WeiboResolver(YtDlpResolver):
    name = "weibo"
    label = "微博"
    hosts = ("weibo.com", "weibo.cn", "t.cn")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    #: video.weibo.com / h5.video.weibo.com 的影片頁
    #: 形式有兩種：?fid=1034:xxx 與 /show/1034:xxx → 直接抓「數字:數字」
    _VIDEO_PAGE = re.compile(r"(?:video|h5\.video)\.weibo\.com/.*?(\d{3,6}:\d{10,25})")

    # ── 主流程：影片頁(瀏覽器) → 匿名 API → yt-dlp ──────
    async def resolve(self, url: str) -> VideoInfo:
        # ⓪ video.weibo.com 的影片頁是 JS 應用 → 用真瀏覽器讀 <video>
        m = self._VIDEO_PAGE.search(url)
        if m:
            try:
                info = await self._via_video_page(m.group(1))
                if info is not None:
                    info.source_url = url
                if info is not None:
                    return info
            except PlatformError:
                raise
            except Exception:  # noqa: BLE001
                pass
        # ① 一般微博貼文 → 匿名訪客 API
        try:
            info = await self._via_ajax(url)
            if info is not None:
                return info
        except PlatformError:
            pass
        except Exception:  # noqa: BLE001
            pass
        # ② yt-dlp（m.weibo.cn 形式；weibo.com/detail 會跳訪客驗證頁）
        canonical = await self._canonical(url)
        return await YtDlpResolver.resolve(self, canonical)

    async def _via_video_page(self, fid: str) -> VideoInfo | None:
        """video.weibo.com 的影片頁 → 真瀏覽器讀 <video>。"""
        data = await page_video_info(
            f"https://h5.video.weibo.com/show/{fid}",
            context_key="weibo",
            wait_for=["mp4", "stream_url", "video_url"],
            tries=22,
        )
        urls = data.get("urls") or []
        if not urls:
            return None
        fmts = [
            Format(id=f"wb{i}", label="原畫" if i == 0 else f"備援線路 {i}",
                   url=u, quality_score=90 - i, mode="proxy",
                   headers={"Referer": "https://weibo.com/"})
            for i, u in enumerate(urls[:4])
        ]
        return VideoInfo(platform=self.name,
                         title=(data.get("title") or "微博影片"),
                         cover=data.get("poster") or "", source_url=data.get("source_url", ""),
                         formats=fmts, extra={"route": "video-page"})

    async def _canonical(self, url: str) -> str:
        mid = await self._status_id(url)
        if mid and re.search(r"weibo\.com/(?:detail|\d+/[0-9A-Za-z]{6,12})", url):
            return f"https://m.weibo.cn/detail/{mid}"
        return url

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

    #: 微博的畫質欄位 → (標籤, 分數)。同一支影片常有多個欄位指向同一條 URL，
    #: 所以最後會用「URL 去重、同名保留最高分」處理。
    _QUALITY_KEYS: tuple[tuple[str, str, int], ...] = (
        ("stream_url_hd", "高清", 95),
        ("mp4_hd_url", "高清", 95),
        ("mp4_720p_mp4", "720P", 85),
        ("hevc_mp4_720p", "720P（HEVC）", 84),
        ("stream_url", "原畫", 80),
        ("mp4_sd_url", "標清", 70),
        ("h265_mp4_hd", "高清（HEVC）", 94),
        ("h265_mp4_ld", "標清（HEVC）", 69),
        ("inch_4_mp4_hd", "高清", 90),
        ("inch_5_mp4_hd", "高清", 90),
        ("inch_5_5_mp4_hd", "高清", 90),
    )

    def _build(self, url: str, d: dict) -> VideoInfo:
        page = d.get("page_info") or {}
        media = page.get("media_info") or {}

        title = (d.get("text_raw") or "").strip() or (media.get("name") or "").strip() or "微博影片"
        title = re.sub(r"\s+", " ", title)[:120]
        author = (d.get("user") or {}).get("screen_name") or media.get("author_name")
        cover = _first_str(
            media.get("cover_image_url"),
            media.get("big_pic_info"),
            media.get("page_pic"),
            page.get("page_pic"),
        )

        duration = None
        for key in ("duration", "duration_time", "video_duration"):
            v = media.get(key) or page.get(key)
            if v:
                try:
                    duration = int(float(v))
                    break
                except (TypeError, ValueError):
                    continue

        # 收集所有畫質 URL（同一條 URL 只留分數最高的標籤）
        best: dict[str, tuple[str, int]] = {}
        for key, label, score in self._QUALITY_KEYS:
            u = media.get(key)
            if not isinstance(u, str) or not u.startswith("http"):
                continue
            prev = best.get(u)
            if prev is None or score > prev[1]:
                best[u] = (label, score)

        # 新版：playback_list（DASH）／variants
        for item in (media.get("playback_list") or []):
            if not isinstance(item, dict):
                continue
            for p in (item.get("play_info") or []):
                if not isinstance(p, dict):
                    continue
                u = p.get("url")
                if isinstance(u, str) and u.startswith("http"):
                    h = int(p.get("height") or 0)
                    label = quality_label(h) if h else (p.get("quality_label") or "原畫")
                    best[u] = (label, h or 60)

        # 後備：任何看起來是影片的欄位
        if not best:
            for k, v in media.items():
                if isinstance(v, str) and v.startswith("http") and (
                        "video" in v or "weibocdn" in v or ".mp4" in v):
                    best[v] = ("原畫", 60)

        fmts = [
            Format(id=f"wb{i}", label=label, url=u, quality_score=score,
                   mode="proxy", headers={"Referer": "https://weibo.com/"})
            for i, (u, (label, score)) in enumerate(
                sorted(best.items(), key=lambda kv: -kv[1][1]))
        ]
        if not fmts:
            raise PlatformError("微博這則貼文沒有影片", platform=self.name)

        return VideoInfo(
            platform=self.name, title=title, cover=cover, source_url=url,
            formats=fmts, duration=duration, author=author,
            extra={"route": "visitor-api"},
        )


def _first_str(*vals) -> str:
    """從一堆可能是 str / dict / list 的值裡，取出第一個看起來像網址的字串。

    ⚠️ 微博的 `page_pic` 有時是字串、有時是 `{"url": ...}` —— 不能直接 .get()。
    """
    for v in vals:
        if not v:
            continue
        if isinstance(v, str):
            if v.startswith("http"):
                return v
        elif isinstance(v, dict):
            u = v.get("url") or v.get("pic") or ""
            if isinstance(u, str) and u.startswith("http"):
                return u
        elif isinstance(v, list) and v:
            u = _first_str(v[0])
            if u:
                return u
    return ""
