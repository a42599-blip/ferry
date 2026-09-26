"""抖音解析。

策略（規格書第 7 章）：
  1) **官方 Web API ＋ a_bogus 簽章**（最準，能拿 bit_rate 多畫質）
  2) **tikwm 第三方 API**（備援）
  3) **yt-dlp**（有 cookies 時）

抖音的 `bit_rate[]` 陣列會帶多種畫質（gear_name）→ 全部列給使用者選。
"""
from __future__ import annotations

import asyncio
import json
import re

from ..core.errors import PlatformChanged, PlatformError, PlatformTimeout
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ytdlp import YtDlpResolver, quality_label

_API = "https://www.tikwm.com/api/"
_DETAIL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
_TTWID = "https://ttwid.bytedance.com/ttwid/union/register/"
_URL_RE = re.compile(
    r"https?://(?:www\.|v\.|vm\.|m\.)?(?:douyin\.com|iesdouyin\.com)/", re.I
)
_ID_RE = re.compile(r"/(?:video|note|share/video|share/note)/(\d{15,25})")

# ⚠️ a_bogus 的 ua_code 是綁「Chrome 90 / Win32」這組 UA 算出來的 → 不可亂改。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
)

#: ttwid 快取（有效期很長；同一個 process 共用，不必每次重拿）
_ttwid_cache: str | None = None


class DouyinResolver(YtDlpResolver):
    name = "douyin"
    label = "抖音"
    hosts = ("douyin.com", "iesdouyin.com")
    default_mode = "fetch"          # 字節系 CDN 開 CORS（規格書第 8 章）

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    # ── 主流程：官方 API → 真瀏覽器 → tikwm → yt-dlp ──
    async def resolve(self, url: str) -> VideoInfo:
        for route in (self._via_official, self._via_browser, self._via_tikwm):
            try:
                info = await route(url)
                if info is not None:
                    return info
            except PlatformError:
                continue
            except Exception:  # noqa: BLE001 — 換下一條路
                continue

        try:
            return await YtDlpResolver.resolve(self, url)
        except PlatformError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlatformTimeout(f"抖音解析失敗：{exc}", platform=self.name) from exc

    # ── 路線②：真瀏覽器（新版無頭）讀 SSR 資料 ───────────
    async def _via_browser(self, url: str) -> VideoInfo | None:
        """用無頭 Chromium（`channel="chromium"` 新版模式）開頁，讀頁面內嵌的影片資料。

        ⚠️ 抖音會把資料 SSR 進 HTML（`bitRateList` / `playAddr` / `desc`），
        但**只在真瀏覽器**才給；預設的 headless shell 拿到的是空殼（會被判為機器人）。
        """
        from ..services.browser import get_context

        aweme_id = await self._aweme_id(url)
        target = f"https://www.douyin.com/video/{aweme_id}" if aweme_id else url

        ctx = await get_context("douyin")
        page = await ctx.new_page()
        try:
            try:
                # `commit`：導覽一送出就回來（不等 domcontentloaded，抖音腳本很重）
                await page.goto(target, wait_until="commit", timeout=15000)
            except Exception:  # noqa: BLE001 — 沒載完也可能已有資料
                pass
            html = ""
            # ⚠️ 要等到「指定的那一支」出現，不能只等 bitRateList
            #    （抖音對載不出來的 ID 會直接顯示推薦影片，那是錯的資料）
            marker = f'"awemeId":"{aweme_id}"' if aweme_id else "bitRateList"
            for _ in range(50):                      # 最多等 20 秒
                await asyncio.sleep(0.4)
                try:
                    html = await page.content()
                except Exception:  # noqa: BLE001 — 頁面正在換頁
                    continue
                if marker in html:
                    break
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass

        if not html or ("bitRateList" not in html and "playAddr" not in html):
            return None

        # ⚠️ 再確認一次：頁面資料真的屬於「我們要的那一支」。
        #    抖音對無效 ID 會直接顯示推薦影片（會拿到錯的影片），必須擋掉。
        if aweme_id and f'"awemeId":"{aweme_id}"' not in html:
            return None

        return self._parse_ssr(url, html)

    # ── SSR HTML 解析（抖音把資料直接刻在頁面裡）──────
    def _parse_ssr(self, url: str, html: str) -> VideoInfo:
        h = html.replace('\\"', '"').replace('\\u0026', '&').replace('\\u002F', '/')

        def g(pattern: str, default: str = "") -> str:
            m = re.search(pattern, h, re.S)
            return m.group(1) if m else default

        title = g(r'"desc":"((?:[^"\\]|\\.)*)"') or "抖音影片"
        try:                                        # 把 \uXXXX 之類的跳脫還原
            title = json.loads(f'"{title}"')
        except Exception:  # noqa: BLE001
            pass
        author = g(r'"nickname":"([^"]+)"') or None
        cover = (g(r'"originCover":"([^"]+)"')
                 or g(r'"dynamicCover":"([^"]+)"')
                 or g(r'"cover":"([^"]+)"'))

        fmts: list[Format] = []
        seen_h: set[int] = set()

        # ① bitRateList（多畫質：1920x1080 / 1280x720 / 1024x576 …）
        pat_br = re.compile(
            r'\{"uri":"[^"]+","dataSize":(\d+),"width":(\d+),"height":(\d+),'
            r'"playAddr":\[\{"src":"([^"]+)"'
        )
        for size, w, hh, src in pat_br.findall(h):
            hh = int(hh)
            if hh in seen_h:
                continue
            seen_h.add(hh)
            fmts.append(Format(
                id=f"v{hh}", label=quality_label(hh), url=src,
                height=hh, width=int(w) or None, size=int(size) or None,
                quality_score=hh, mode="proxy",
            ))

        # ② 主 video 物件（原畫；bitRateList 沒東西時用）
        if not fmts:
            m = re.search(
                r'"video":\{"width":(\d+),"height":(\d+),"ratio":"[^"]*",'
                r'"duration":(\d+),"dataSize":(\d+),"uri":"[^"]*",'
                r'"playAddr":\[\{"src":"([^"]+)"',
                h,
            )
            if m:
                w, hh, _dur, size, src = m.groups()
                fmts.append(Format(id="origin", label=quality_label(int(hh)), url=src,
                                   height=int(hh), width=int(w) or None,
                                   size=int(size) or None, quality_score=int(hh),
                                   mode="proxy"))

        # ③ 圖集
        for i, img in enumerate(re.findall(r'"images":\[\{"url":"([^"]+)"', h)[:20], 1):
            fmts.append(Format(id=f"img{i}", label=f"圖 {i}", url=img, ext="jpg",
                               quality_score=100 - i, mode="direct"))

        if not fmts:
            raise PlatformChanged("抖音頁面找不到影片網址", platform=self.name)

        # 時長（毫秒 → 秒）
        duration = None
        m = re.search(r'"video":\{[^}]*?"duration":(\d{4,})', h)
        if m:
            duration = int(m.group(1)) // 1000 or None

        fmts.sort(key=lambda f: -(f.quality_score or 0))
        return VideoInfo(
            platform=self.name, title=title, cover=cover, source_url=url,
            formats=fmts, duration=duration, author=author,
            extra={"route": "browser-ssr"},
        )

    # ── 路線①：官方 Web API（a_bogus 簽章）────────────
    async def _via_official(self, url: str) -> VideoInfo | None:
        global _ttwid_cache

        aweme_id = await self._aweme_id(url)
        if not aweme_id:
            return None

        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": "1",
            "version_code": "190500",
            "version_name": "19.5.0",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_online": "true",
            "engine_name": "Blink",
            "os_name": "Windows",
            "os_version": "10",
            "platform": "PC",
            "browser_version": "90.0.4430.212",
            "engine_version": "90.0.4430.212",
            "cpu_core_num": "12",
            "device_memory": "8",
            "aweme_id": aweme_id,
        }

        params["a_bogus"] = ""          # 佔位，下面每輪重算

        headers = {
            "User-Agent": _UA,
            "Referer": f"https://www.douyin.com/video/{aweme_id}",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        # 抖音會偶發限流 → 重試（每次重算 a_bogus）
        for attempt in range(3):
            params["a_bogus"] = _abogus().get_value(
                {k: v for k, v in params.items() if k != "a_bogus"}
            )
            async with HttpClient(ua=_UA, timeout=20) as http:
                if not _ttwid_cache:
                    _ttwid_cache = await self._ttwid(http) or ""
                if _ttwid_cache:
                    headers["Cookie"] = f"ttwid={_ttwid_cache}"
                resp = await http.get(_DETAIL, params=params, headers=headers)
                if resp.status_code != 200:
                    if attempt < 2:
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    return None
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    return None

            detail = (data or {}).get("aweme_detail")
            if detail:
                return self._build_official(url, detail)
            if attempt < 2:
                await asyncio.sleep(0.8 * (attempt + 1))
        return None

    @staticmethod
    async def _ttwid(http: HttpClient) -> str | None:
        """向 bytedance 註冊一個匿名 ttwid（不需要登入）。"""
        body = {
            "region": "cn",
            "aid": 1768,
            "needFid": False,
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https",
            "union": True,
        }
        try:
            resp = await http.post(_TTWID, json=body)
        except Exception:  # noqa: BLE001
            return None
        for cookie in resp.headers.get_list("set-cookie"):
            m = re.search(r"ttwid=([^;]+)", cookie)
            if m:
                return m.group(1)
        return None

    @staticmethod
    async def _aweme_id(url: str) -> str | None:
        """從網址取 aweme_id；短連結（v.douyin.com）先解轉址。"""
        m = _ID_RE.search(url)
        if m:
            return m.group(1)
        if re.search(r"v\.douyin\.com|douyin\.com/[A-Za-z0-9]{6,}/?", url):
            try:
                async with HttpClient(timeout=15) as http:
                    resp = await http.get(url)
                    m = _ID_RE.search(str(resp.url))
                    if m:
                        return m.group(1)
                    m = _ID_RE.search(resp.text)
                    if m:
                        return m.group(1)
            except Exception:  # noqa: BLE001
                return None
        return None

    def _build_official(self, url: str, detail: dict) -> VideoInfo:
        title = (detail.get("desc") or "").strip() or "抖音影片"
        author = (detail.get("author") or {}).get("nickname")
        duration = detail.get("duration")
        if duration:
            duration = int(duration) // 1000 or None   # 抖音是毫秒

        video = detail.get("video") or {}
        cover = ""
        for key in ("origin_cover", "cover", "dynamic_cover"):
            urls = (video.get(key) or {}).get("url_list") or []
            if urls:
                cover = urls[0]
                break

        fmts: list[Format] = []
        seen: set[str] = set()
        seen_h: set = set()

        # ① bit_rate[]（多畫質）
        for br in video.get("bit_rate") or []:
            addr = (br.get("play_addr") or {}).get("url_list") or []
            if not addr:
                continue
            u = addr[0]
            if u in seen:
                continue
            seen.add(u)

            h = int(br.get("height") or 0)
            gear = br.get("gear_name") or ""
            if not h:
                # 抖音的 gear_name 例如 `normal_1080_0` / `adapt_lowest_1440_1`
                m = re.search(r"(\d{3,4})", gear)
                h = int(m.group(1)) if m else 0
            key = h or gear
            if key in seen_h:
                continue
            seen_h.add(key)

            fmts.append(
                Format(
                    id=f"v{h}" if h else f"br{len(fmts)}",
                    label=quality_label(h) if h else (gear or "原畫"),
                    url=u,
                    height=h or None,
                    width=br.get("width") or None,
                    size=br.get("data_size") or None,
                    quality_score=h or (int(br.get("bit_rate") or 0) // 1000) or 50,
                    mode="fetch",
                )
            )

        # ② play_addr（原畫）
        for key in ("play_addr", "download_addr"):
            addr = (video.get(key) or {}).get("url_list") or []
            if addr and addr[0] not in seen:
                seen.add(addr[0])
                fmts.append(
                    Format(
                        id="origin" if key == "play_addr" else "download",
                        label="原畫" if key == "play_addr" else "下載版",
                        url=addr[0],
                        quality_score=85 if key == "play_addr" else 70,
                        mode="fetch",
                    )
                )

        # ③ 圖集
        images = detail.get("images") or []
        for i, img in enumerate(images, 1):
            urls = (img or {}).get("url_list") or []
            if urls:
                fmts.append(
                    Format(id=f"img{i}", label=f"圖 {i}", url=urls[-1], ext="jpg",
                           quality_score=100 - i, mode="direct")
                )

        # ④ 音樂
        music = ((detail.get("music") or {}).get("play_url") or {}).get("url_list") or []
        if music:
            fmts.append(
                Format(id="audio", label="純音訊", url=music[0], audio=True,
                       ext="mp3", quality_score=10, mode="fetch")
            )

        if not fmts:
            raise PlatformError("抖音沒有可下載的檔案", platform=self.name)

        return VideoInfo(
            platform=self.name, title=title, cover=cover, source_url=url,
            formats=fmts, duration=duration, author=author,
            extra={"is_gallery": bool(images), "route": "official"},
        )

    # ── 路線②：tikwm ────────────────────────────────
    async def _via_tikwm(self, url: str) -> VideoInfo | None:
        try:
            async with HttpClient() as http:
                data = await http.get_json(_API, params={"url": url, "hd": 1})
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict) or data.get("code") != 0:
            return None
        return self._build_tikwm(url, data.get("data") or {})

    def _build_tikwm(self, url: str, d: dict) -> VideoInfo:
        title = (d.get("title") or "").strip() or "抖音影片"
        cover = d.get("origin_cover") or d.get("cover") or ""
        author = (d.get("author") or {}).get("unique_id") or (d.get("author") or {}).get("nickname")
        duration = d.get("duration")

        fmts: list[Format] = []
        images = d.get("images")
        for i, img in enumerate(images or [], 1):
            fmts.append(Format(id=f"img{i}", label=f"圖 {i}", url=img, ext="jpg",
                               quality_score=100 - i, mode="direct"))

        hd, play, music = d.get("hdplay"), d.get("play"), d.get("music")
        if hd:
            fmts.append(Format(id="hd", label="高清", url=_abs(hd),
                               size=d.get("hd_size"), quality_score=90, mode="fetch"))
        if play:
            fmts.append(Format(id="origin", label="原畫", url=_abs(play),
                               size=d.get("size"), quality_score=80, mode="fetch"))
        if music:
            fmts.append(Format(id="audio", label="純音訊", url=_abs(music), audio=True,
                               ext="mp3", quality_score=10, mode="fetch"))

        if not fmts:
            raise PlatformError("抖音沒有可下載的檔案", platform=self.name)

        return VideoInfo(platform=self.name, title=title, cover=cover, source_url=url,
                         formats=fmts, duration=duration, author=author,
                         extra={"cover_fallback": d.get("cover"),
                                "is_gallery": bool(images), "route": "tikwm"})


def _abogus():
    """延遲載入（sm3 需要 Python 3.12+，失敗時讓其他路線仍可用）。"""
    from ._douyin_abogus import ABogus

    return ABogus()


def _abs(u: str) -> str:
    if u.startswith("http"):
        return u
    return "https://www.tikwm.com" + (u if u.startswith("/") else "/" + u)
