"""yt-dlp 通用解析基底（13 平台大部分都走這裡）。

設計：
  - 一個平台 = 一個子類，只覆寫 `ytdlp_extra`（必要時）＋ hosts。
  - 失敗一律轉成 PlatformError 的子類（上層一致處理）。

⚠️ yt-dlp 是同步的 → 一律用 asyncio.to_thread 包起來，不准阻塞 event loop。
（規格書第 21-2 章）
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import yt_dlp

from ..core.errors import (
    PlatformBlocked,
    PlatformChanged,
    PlatformError,
    PlatformTimeout,
)
from ..core.models import Format, VideoInfo
from .base import Resolver

# ── 畫質標籤統一規格（規格書：1080P / 720P / 540P，往上支援 2K/4K/8K）──
_QUALITY_TABLE = (
    (4320, "8K"),
    (2160, "4K"),
    (1440, "2K"),
    (1080, "1080P"),
    (720, "720P"),
    (540, "540P"),
    (480, "480P"),
    (360, "360P"),
)


def quality_label(height: int | None) -> str:
    if not height:
        return "原畫"
    for threshold, label in _QUALITY_TABLE:
        if height >= threshold:
            return label
    return f"{height}P"


def _cache_dir():
    """yt-dlp 的快取目錄（優先用資料目錄，讓它跨重啟保留）。"""
    import os

    root = os.environ.get("DATA_DIR") or os.path.join(os.getcwd(), "data")
    path = os.path.join(root, "yt-dlp-cache")
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return True          # 建不出來就讓 yt-dlp 用它的預設位置


# ⚠️ 一定要用「桌面瀏覽器」UA，不要用手機 UA（2026-09-27 實測踩到）：
#    手機 UA 會讓 Facebook／TikTok／微博／今日頭條 回「沒有影片資料」的頁面：
#      Facebook      手機 UA → 0 種畫質　桌面 UA → 1080P/720P ✅
#      TikTok        手機 UA → 失敗　　　桌面 UA → 1920/1280 ✅
#      微博          手機 UA → 無法解析　桌面 UA → 1080P/720P ✅
#      今日頭條      手機 UA → Unsupported 桌面 UA → 1280/854 ✅
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

_BASE_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "socket_timeout": 20,
    # ⚠️ 不要自己調低重試次數！（2026-09-27 從 v8i8 對照出來的關鍵）
    #    v8i8 用預設的 extractor_retries（3）＋ retry_sleep 指數退避，
    #    YouTube 的機器人判定是「間歇性」的 —— 多試幾次就會過。
    #    我原本寫 extractor_retries: 1（試一次就放棄）→ YouTube 常常失敗。
    "retry_sleep": "extractor:exp=1:20",
    "fragment_retries": 10,
    "nocheckcertificate": True,
    # ⚠️ 一定要留快取！（2026-09-27 從 v8i8 對照出來的關鍵）
    #    yt-dlp 會把 YouTube 的 player JS／PO token 存在快取裡；
    #    關掉快取 = 每次都要重新解一次 JS 驗證 → 某些影片就被判定成機器人。
    #    v8i8 沒有關快取，所以它的 YouTube 三支測起來全部成功。
    #    這裡放在資料目錄（Railway 是 Volume）→ 重啟也不會消失。
    "cachedir": _cache_dir(),
    "no_color": True,
    "http_headers": {
        "User-Agent": _DESKTOP_UA,
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    },
}


class YtDlpResolver(Resolver):
    """所有 yt-dlp 平台的共同父類。"""

    #: 子類可覆寫：額外的 yt-dlp 參數
    ytdlp_extra: dict[str, Any] = {}

    #: 預設下載模式（見 models.Format.mode）
    default_mode: str = "proxy"

    def ytdlp_opts(self) -> dict[str, Any]:
        """解析用的 yt-dlp 參數（子類可覆寫）。"""
        return dict(self.ytdlp_extra)

    def download_opts(self) -> dict[str, Any]:
        """下載用的 yt-dlp 參數（預設跟解析一樣；子類可覆寫）。

        會分開是因為：有些平台「列清單」和「真的抓檔」要用的 client 不一樣。
        """
        return self.ytdlp_opts()

    # ── 解析 ────────────────────────────────────────
    async def resolve(self, url: str) -> VideoInfo:
        info = await self._extract(url)
        return self.build(url, info)

    async def _extract(self, url: str) -> dict:
        opts = dict(_BASE_OPTS)
        opts.update(self.ytdlp_opts())

        # 有準備 cookie 檔就帶上（IG／FB／X／微博／頭條… 需要）
        from ..services.cookies import cookiefile_for

        cf = cookiefile_for(self.name)
        # YouTube 另外支援環境變數（雲端 IP 被判定機器人時用）
        env_ck = getattr(self, "_env_cookiefile", None)
        if callable(env_ck):
            cf = env_ck() or cf
        if cf:
            opts["cookiefile"] = cf

        def run() -> dict:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            data = await asyncio.to_thread(run)
        except yt_dlp.utils.DownloadError as exc:  # noqa: PERF203
            raise self._translate(str(exc)) from exc
        except asyncio.TimeoutError as exc:
            raise PlatformTimeout(f"{self.label} 解析逾時", platform=self.name) from exc
        except Exception as exc:  # noqa: BLE001
            raise PlatformChanged(
                f"{self.label} 解析失敗：{str(exc)[:160]}", platform=self.name
            ) from exc

        if not isinstance(data, dict):
            raise PlatformChanged(f"{self.label} 沒有回傳資料", platform=self.name)
        # 播放清單／頻道：只取第一支
        if data.get("_type") == "playlist" and data.get("entries"):
            entries = [e for e in data["entries"] if e]
            if not entries:
                raise PlatformError(f"{self.label} 找不到影片", platform=self.name)
            data = entries[0]
        return data

    def _translate(self, msg: str) -> PlatformError:
        low = msg.lower()
        if any(k in low for k in ("403", "forbidden", "unable to download webpage")):
            return PlatformBlocked(
                f"{self.label} 被平台阻擋（可能需 cookies）", detail=msg[:200], platform=self.name
            )
        # ⚠️ 小羅 2026-10-10：yt-dlp 對某些平台（抖音系）要求「新鮮 cookie」＝
        #    **我們的解析程式要更新**，不是「內容需要登入」→ 不可回「限制觀賞」（會誤導客人）。
        if "fresh cookies" in low or "cookies are needed" in low:
            return PlatformChanged(
                f"{self.label} 解析程式需要更新（平台要求新的憑證）",
                detail=msg[:200], platform=self.name,
            )
        if any(k in low for k in ("login", "sign in", "log in", "account", "cookie", "age")):
            return PlatformBlocked(
                f"{self.label} 需要登入或 cookies 才能解析", detail=msg[:200], platform=self.name
            )
        if any(k in low for k in ("429", "too many requests", "rate")):
            return PlatformBlocked(
                f"{self.label} 被限流，請稍後再試", detail=msg[:200], platform=self.name
            )
        if any(k in low for k in ("timed out", "timeout")):
            return PlatformTimeout(
                f"{self.label} 解析逾時", detail=msg[:200], platform=self.name
            )
        if any(k in low for k in ("unsupported url", "not a valid url", "no video formats")):
            return PlatformChanged(
                f"{self.label} 這個連結解析不到影片", detail=msg[:200], platform=self.name
            )
        return PlatformChanged(
            f"{self.label} 解析失敗", detail=msg[:200], platform=self.name
        )

    # ── 組裝統一格式 ─────────────────────────────────
    def build(self, url: str, info: dict) -> VideoInfo:
        fmts = self.build_formats(info)
        if not fmts:
            raise PlatformError(f"{self.label} 沒有可下載的檔案", platform=self.name)

        cover = info.get("thumbnail") or ""
        if not cover:
            thumbs = info.get("thumbnails") or []
            if thumbs:
                cover = thumbs[-1].get("url") or ""

        title = (info.get("title") or info.get("description") or "").strip()
        duration = info.get("duration")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None

        headers = {}
        raw_headers = info.get("http_headers") or {}
        if isinstance(raw_headers, dict):
            headers = {
                k: str(v)
                for k, v in raw_headers.items()
                if k.lower() in ("referer", "user-agent", "origin")
            }
        for f in fmts:
            if not f.headers:
                f.headers = dict(headers)

        uploader = info.get("uploader") or info.get("channel") or info.get("uploader_id")

        return VideoInfo(
            platform=self.name,
            title=title or f"{self.label} 影片",
            cover=cover,
            source_url=url,
            formats=fmts,
            duration=duration,
            author=uploader,
            extra={
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "view_count": info.get("view_count"),
            },
        )

    def build_formats(self, info: dict) -> list[Format]:
        """把 yt-dlp 的 formats 整理成清單（**平台給幾種就列幾種**）。

        ⚠️ 小羅 2026-09-27 要求：「他平台假設提供 6 種畫質你就給 6 種，
           3 種就給 3 種，你不要去篩選。」
        → 只做必要的去重（同高度+同編碼+同 fps 才算重複，留位元率最高的），
          不同高度／不同編碼（H.264 vs H.265）／不同 fps 一律**全部保留**。
        """
        raw = [f for f in (info.get("formats") or []) if f.get("url")]
        if not raw and info.get("url"):
            raw = [info]

        # 去重鍵：高度 + 視訊編碼 + fps（同鍵才視為重複，其餘全留）
        best: dict[tuple, dict] = {}
        n_by_height: dict[int, int] = {}
        best_audio: Optional[dict] = None
        best_any: Optional[dict] = None

        for f in raw:
            vcodec = (f.get("vcodec") or "none").lower()
            acodec = (f.get("acodec") or "none").lower()
            has_v = vcodec != "none"
            has_a = acodec != "none"

            if has_v and has_a:                     # 影音合一（最理想）
                primary = f
            elif has_a and not has_v:               # 純音訊
                if best_audio is None or _score(f) > _score(best_audio):
                    best_audio = f
                continue
            elif has_v:                             # 純視訊（需合併）
                primary = f
            else:
                continue

            h = int(f.get("height") or 0)
            key = (h, (f.get("vcodec") or "none").split(".")[0], int(f.get("fps") or 0))
            prev = best.get(key)
            if prev is None or _prefer(primary, prev):
                best[key] = primary
            if best_any is None or _prefer(primary, best_any):
                best_any = primary

        for f in best.values():
            n_by_height[int(f.get("height") or 0)] = n_by_height.get(int(f.get("height") or 0), 0) + 1

        fmts: list[Format] = []
        for key in sorted(best, key=lambda k: (k[0], k[1]), reverse=True):
            f = best[key]
            h = int(f.get("height") or 0)
            fps = int(f.get("fps") or 0)
            has_audio = (f.get("acodec") or "none").lower() != "none"
            fmts.append(
                Format(
                    id=f"v{h or 'origin'}-{key[1]}-{fps}",
                    label=_variant_label(h, key[1], fps, n_by_height.get(h, 1)),
                    url=f["url"],
                    height=h or None,
                    width=int(f.get("width") or 0) or None,
                    size=_size(f),
                    ext=f.get("ext") or "mp4",
                    vcodec=f.get("vcodec"),
                    acodec=f.get("acodec"),
                    quality_score=(h or 1) * 10 + fps // 10,
                    mode=self.default_mode,
                    headers={"X-Needs-Merge": "1"} if not has_audio else {},
                )
            )

        if best_audio is not None:
            fmts.append(
                Format(
                    id="audio",
                    label="純音訊",
                    url=best_audio["url"],
                    ext=best_audio.get("ext") or "m4a",
                    audio=True,
                    size=_size(best_audio),
                    acodec=best_audio.get("acodec"),
                    quality_score=10,
                    mode=self.default_mode,
                )
            )
        return fmts


def _score(f: dict) -> float:
    return float(f.get("tbr") or f.get("abr") or f.get("vbr") or 0)


def _variant_label(h: int, vcodec: str, fps: int, same_height_count: int) -> str:
    """畫質標籤。同高度有多種時，補上編碼／fps 讓使用者分得出來。"""
    base = quality_label(h or None)
    tags: list[str] = []
    if same_height_count > 1:
        if vcodec in ("h265", "hevc"):
            tags.append("H.265")
        elif vcodec in ("vp9", "vp09"):
            tags.append("VP9")
        elif vcodec in ("av01", "av1"):
            tags.append("AV1")
    if fps >= 50:
        tags.append(f"{fps}fps")
    return f"{base} ({'·'.join(tags)})" if tags else base


def _prefer(a: dict, b: dict) -> bool:
    """a 是否比 b 更值得留下（先看有沒有聲音，再看位元率）。"""
    a_audio = (a.get("acodec") or "none").lower() != "none"
    b_audio = (b.get("acodec") or "none").lower() != "none"
    if a_audio != b_audio:
        return a_audio
    return _score(a) > _score(b)


def _size(f: dict) -> int | None:
    for k in ("filesize", "filesize_approx"):
        v = f.get(k)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return None
