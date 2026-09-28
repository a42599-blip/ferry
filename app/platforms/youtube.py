"""YouTube 解析（yt-dlp，多方案自動切換）。

小羅 2026-09-28：「這是對外的公開網站，次數可能很多也可能很少；
不管用哪個方案，要能好幾個方案一起上，哪個不行了就自動跳另一個。」

⚠️ 真正的失敗原因（2026-09-28 查證，見 D:/pi-agent/技術記憶_YouTube真正根因_PO_Token_2026-09-28.md）：
    yt-dlp 官方《PO Token Guide》：android／ios／web／mweb 的影片網址要 PO Token，
    沒有 → googlevideo 回 **HTTP 403**。android_vr／web_embedded／tv **不需要**。
    舊做法 player_client="all" 會混到 android 網址 → 解析成功、下載 403。

做法（全部只在本模組內）：
  1. 依序試「不需 PO Token」的身分（_STRATEGIES），每個都**先試抓 1.5MB 之後的一小段**，
     googlevideo 真的給檔才算成功；不行就自動換下一個（只抓開頭會被「前 1MB 照給」騙過）。
  2. 方案順序固定（最穩的排前面）；被擋的方案暫停 _COOLDOWN 秒（避免一直打同一個壞掉的）。
     只跟「這一支影片」有關的失敗（例：不允許嵌入）不會暫停，免得一支影片害到所有人。
  3. 同一支影片 _CACHE_TTL 秒內直接用快取；多人同時點同一支，只打 YouTube 一次。
  4. 下載走 proxy（伺服器 yt-dlp 下載後串給使用者），沒被暫停的身分依序一起交給 yt-dlp（見 download_opts）。
     為什麼不用 relay：googlevideo 不接受一次抓整個大檔（要分段 ≈10MB），
     共用的 relay 是一次抓整檔 → 大影片 403（2026-09-28 實測：600KB 可、10 分鐘影片全 403）。
     yt-dlp 會自動分段、合併影音軌。

⚠️⚠️ 真正的關鍵點（2026-09-28 實測證實）：解 YouTube 的 JS 驗證要「兩樣東西」
    ① Deno＝執行引擎（Dockerfile 已裝、鎖 2.8.3）
    ② **EJS 解題程式庫**（challenge-solver/lib.json）＝要跑的腳本 → 靠 `remote_components: ["ejs:github"]`
       讓 yt-dlp 自己下載，存進快取（轉運站快取在 /data 永久空間，部署不會清掉，只下載一次）。
    少了 ②：伺服器永遠解不了 JS → web/web_embedded 全部「Requested format is not available」
            → 只剩 App 身分（android 要通行證 403、android_vr 只給前 1MB）→ 時好時壞。
    本機之所以正常：這台電腦的預設快取（D:/ComfyUI/cache/yt-dlp）以前就存過 lib.json。
    v8i8 在 2026-06 把 remote_components 拿掉（以為會卡住）→ 就是它 YouTube 一直不穩的根本原因。
    實測：空快取＋不開 → ❌；空快取＋開 → ✅ 41 種格式、10MB 之後也能抓（206）。
⚠️ 為什麼不帶 cookies、不塞自訂 UA（v8i8 的教訓）：
    帶 cookies 會讓 yt-dlp 跳過 App 身分；自訂 UA 會和身分對不上。
    唯一例外：小羅在 Railway 設了 YT_COOKIES_JSON → 最後才用「cookies」方案。
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import tempfile
import time
from typing import Any, Optional

import httpx
import yt_dlp

from ..core.errors import PlatformBlocked, PlatformChanged, PlatformError
from ..core.models import VideoInfo
from ._ytdlp import _BASE_OPTS, YtDlpResolver

_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/", re.I
)
_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})")

#: 方案清單（依序嘗試）。第三欄＝是否需要 YT_COOKIES_JSON。
#: ⚠️ 2026-09-28 實測（Big Buck Bunny 18MB）：android_vr 只給「前 1MB」，之後 403；
#:    web_embedded 全段都給 → 排第一。所以試抓一定要抓 1MB 之後（見 _probe）。
_STRATEGIES: tuple[tuple[str, dict[str, Any], bool], ...] = (
    ("web_embedded", {"player_client": ["web_embedded"]}, False),  # 不需 PO Token（限可嵌入影片）
    ("android_vr", {"player_client": ["android_vr"]}, False),      # 不需 PO Token，但長片常只給前 1MB（不穩，只當備用）
    ("tv", {"player_client": ["tv"]}, False),                      # 不需 PO Token（DRM 格式會被濾掉）
    ("default", {}, False),                                        # yt-dlp 自己挑（最後手段）
    ("cookies", {"player_client": ["tv", "web_safari"]}, True),    # 只有設了 YT_COOKIES_JSON 才用
)

_COOLDOWN = 300.0          # 失敗的方案暫停幾秒
_CACHE_TTL = 1200.0        # 同一支影片快取幾秒（googlevideo 網址約 6 小時失效）
_CACHE_MAX = 500
_PER_TRY_TIMEOUT = 12.0    # 單一方案最多等幾秒
_TOTAL_BUDGET = 34.0       # 全部方案合計上限（解析總逾時是 40 秒）

#: 轉發時的預設 UA（與 proxy.relay 相同；格式自己的 UA 會蓋過它）
_RELAY_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36")

_state: dict[str, Any] = {"last_ok": None, "down_until": {}}
_cache: dict[str, tuple[float, VideoInfo]] = {}
_inflight: dict[str, asyncio.Future] = {}


class YoutubeResolver(YtDlpResolver):
    name = "youtube"
    label = "YouTube"
    hosts = ("youtube.com", "youtu.be", "youtube-nocookie.com")
    default_mode = "proxy"

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    def download_opts(self) -> dict[str, Any]:
        """下載用參數：把「目前沒被暫停」的身分依序一起交給 yt-dlp（同一格式會優先用前面的身分）。

        最近一次是靠 cookies 方案才成功 → 下載也用 cookies。
        會蓋掉共用下載器的 cookiefile 與 http_headers（只影響 YouTube）。
        """
        opts: dict[str, Any] = {
            "cookiefile": None,
            "http_headers": {},
            "js_runtimes": {"deno": {}},
            "remote_components": ["ejs:github"],
            "fragment_retries": 10,
        }
        if _state["last_ok"] == "cookies":
            opts["cookiefile"] = self._env_cookiefile()
            opts["extractor_args"] = {"youtube": dict(_STRATEGIES[-1][1])}
            return opts
        clients = [c for name, ea, needs_cookie in self._ordered() if not needs_cookie
                   for c in ea.get("player_client", [])]
        opts["extractor_args"] = {"youtube": {"player_client": clients}}
        return opts

    # ── 解析（快取 → 同影片合併請求 → 多方案）────────────────
    async def resolve(self, url: str) -> VideoInfo:
        m = _ID_RE.search(url)
        key = m.group(1) if m else url
        hit = _cache.get(key)
        if hit and hit[0] > time.time():
            return copy.deepcopy(hit[1])

        running = _inflight.get(key)
        if running is not None:
            return copy.deepcopy(await asyncio.shield(running))

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        _inflight[key] = fut
        try:
            info = await self._resolve_with_failover(url)
            fut.set_result(info)
            if len(_cache) >= _CACHE_MAX:
                for k in [k for k, v in _cache.items() if v[0] <= time.time()] or list(_cache)[:50]:
                    _cache.pop(k, None)
            _cache[key] = (time.time() + _CACHE_TTL, info)
            return copy.deepcopy(info)
        except Exception as exc:
            fut.set_exception(exc)
            fut.exception()          # 已處理，避免「未取用的例外」警告
            raise
        finally:
            _inflight.pop(key, None)

    async def _resolve_with_failover(self, url: str) -> VideoInfo:
        started = time.monotonic()
        tried: list[str] = []
        last_err: Optional[PlatformError] = None

        for name, ea, needs_cookie in self._ordered():
            left = _TOTAL_BUDGET - (time.monotonic() - started)
            if left < 4:
                break
            cookiefile = self._env_cookiefile() if needs_cookie else None
            if needs_cookie and not cookiefile:
                continue
            try:
                info = await asyncio.wait_for(
                    self._try(url, name, ea, cookiefile), timeout=min(_PER_TRY_TIMEOUT, left))
            except asyncio.TimeoutError:
                last_err = PlatformBlocked(f"{self.label} 回應逾時", platform=self.name)
                tried.append(f"{name}: 逾時")
                _state["down_until"][name] = time.time() + _COOLDOWN
            except PlatformError as exc:
                last_err = exc
                tried.append(f"{name}: {exc.message}")
                if isinstance(exc, PlatformBlocked):          # 被擋才暫停；單支影片的問題不暫停
                    _state["down_until"][name] = time.time() + _COOLDOWN
            else:
                _state["last_ok"] = name
                _state["down_until"].pop(name, None)
                info.extra["yt_strategy"] = name
                info.extra["yt_tried"] = tried
                return info
            finally:
                if cookiefile:
                    try:
                        os.remove(cookiefile)
                    except OSError:
                        pass

        raise PlatformBlocked(
            f"{self.label} 暫時無法下載，請稍後再試",
            detail=" | ".join(tried)[:400] or (last_err.detail if last_err else None),
            platform=self.name,
        )

    def _ordered(self) -> list[tuple[str, dict[str, Any], bool]]:
        """固定順序；暫停中的方案移到最後（全部都暫停時仍會試）。"""
        now = time.time()
        down = _state["down_until"]
        return sorted(_STRATEGIES, key=lambda s: 1 if down.get(s[0], 0) > now else 0)

    async def _try(self, url: str, name: str, ea: dict[str, Any],
                   cookiefile: Optional[str]) -> VideoInfo:
        opts = dict(_BASE_OPTS)
        opts["js_runtimes"] = {"deno": {}}
        opts["remote_components"] = ["ejs:github"]      # EJS 解題程式庫（見檔頭「真正的關鍵點」）
        if ea:
            opts["extractor_args"] = {"youtube": dict(ea)}
        if cookiefile:
            opts["cookiefile"] = cookiefile

        def run() -> dict:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            data = await asyncio.to_thread(run)
        except yt_dlp.utils.DownloadError as exc:
            raise self._translate(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — 預期外的錯誤也要讓自動切換繼續
            raise PlatformChanged(f"{self.label}（{name}）解析失敗：{str(exc)[:120]}",
                                  platform=self.name) from exc
        if not isinstance(data, dict):
            raise PlatformBlocked(f"{self.label} 沒有回傳資料", platform=self.name)
        if data.get("_type") == "playlist" and data.get("entries"):
            data = next((e for e in data["entries"] if e), data)

        # 只留「伺服器可以直接轉發」的格式：有網址、非 DRM、一般 http(s)（不收 m3u8／dash 片段）
        raw = [f for f in (data.get("formats") or [])
               if f.get("url") and not f.get("has_drm")
               and (f.get("protocol") or "https") in ("https", "http")]
        if not raw:
            raise PlatformBlocked(f"{self.label}（{name}）沒有可轉發的格式", platform=self.name)
        data = dict(data, formats=raw)

        info = self.build(url, data)          # 共用的組裝（畫質清單、標題、封面）
        by_url = {f["url"]: f for f in raw}
        merge_audio = self._merge_audio(raw)

        def hdr(u: str) -> dict[str, str]:
            return _cdn_headers(by_url.get(u, {}).get("http_headers") or data.get("http_headers"))

        # 先試抓（1.5MB 之後的一小段）：googlevideo 真的給檔才算數；不給的格式從清單拿掉。
        #   • 分離格式（影＋音）同一身分規則相同 → 抽一個代表試
        #   • 影音合一格式（通常只有 itag 18）每個都試 —— 2026-09-28 實測：itag 18 不論哪種 UA 都 403
        #   ⚠️ 不設 f.audio_url：設了共用層會強制改成 relay（大檔會 403，見檔頭第 4 點）
        silent = [f for f in info.formats if not f.audio and (f.acodec or "none").lower() == "none"]
        dash_ok = bool(silent and merge_audio) and (
            await _probe(silent[0].url, hdr(silent[0].url)) < 400
            and await _probe(merge_audio, hdr(merge_audio)) < 400)
        keep = []
        for f in info.formats:
            if f in silent:
                ok = dash_ok
            elif f.audio:
                ok = dash_ok or await _probe(f.url, hdr(f.url)) < 400
            else:
                ok = await _probe(f.url, hdr(f.url)) < 400
            if ok:
                f.mode = "proxy"
                keep.append(f)
        if not keep:
            raise PlatformBlocked(f"{self.label}（{name}）影片伺服器拒絕下載", platform=self.name)
        info.formats = keep
        return info

    @staticmethod
    def _merge_audio(raw: list[dict]) -> Optional[str]:
        """合併用音軌：優先 m4a（iPhone 相容），沒有才用其他。"""
        auds = [f for f in raw
                if (f.get("vcodec") or "none") == "none" and (f.get("acodec") or "none") != "none"]
        if not auds:
            return None
        auds.sort(key=lambda f: (f.get("ext") == "m4a", f.get("abr") or f.get("tbr") or 0),
                  reverse=True)
        return auds[0]["url"]

    @staticmethod
    def _env_cookiefile() -> Optional[str]:
        """從 `YT_COOKIES_JSON` 環境變數產生 cookie 檔（只給「cookies」方案用）。

        設定格式（Railway 環境變數）：YT_COOKIES_JSON = {"SID":"...","HSID":"...", ...}
        """
        raw = os.environ.get("YT_COOKIES_JSON", "").strip()
        if not raw:
            return None
        try:
            pairs = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(pairs, dict) or not pairs:
            return None
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="yt_ck_")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for name, value in pairs.items():
                f.write(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
        return path


def _cdn_headers(raw: Any) -> dict[str, str]:
    """轉發 googlevideo 要帶的標頭：用「該身分自己的」UA／Referer／Origin。"""
    if not isinstance(raw, dict):
        return {}
    return {k: str(v) for k, v in raw.items() if k.lower() in ("user-agent", "referer", "origin")}


async def _probe(url: str, headers: dict[str, str]) -> int:
    """試抓 1.5MB 位置的 1KB，回傳 HTTP 狀態碼。

    ⚠️ 不能只抓開頭：沒有 PO Token 時 googlevideo 常「前 1MB 照給、之後 403」。
       檔案不到 1.5MB 會回 416 → 改抓開頭（小檔整個都在免費範圍內）。
    """
    base = {"User-Agent": _RELAY_UA, "Accept": "*/*", **headers}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers={**base, "Range": "bytes=1572864-1573887"})
            if r.status_code == 416:
                r = await c.get(url, headers={**base, "Range": "bytes=0-1023"})
            return r.status_code
    except httpx.HTTPError:
        return 599
