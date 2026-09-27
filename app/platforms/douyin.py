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
from . import _douyin_shared as _shared
from ._ytdlp import YtDlpResolver, quality_label

_API = "https://www.tikwm.com/api/"
#: ⚠️ 排除 `/xg/`：那是**西瓜視頻**的分享路徑（iesdouyin.com/xg/video/<id>）
_URL_RE = re.compile(
    r"https?://(?:www\.|v\.|vm\.|m\.)?(?:douyin\.com|iesdouyin\.com)/(?!xg/)", re.I
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

    # ── 主流程：多條路線「並行」，誰先成功用誰（照 v8i8 的做法）──
    #
    # ⚠️ 為什麼要並行（小羅 2026-09-27：手機版常回「伺服器忙碌」）：
    #    原本是「官方失敗才試瀏覽器、再失敗才試 tikwm」→ 每條要 5～20 秒，
    #    累積起來很容易超過 Cloudflare／Railway 的請求上限（502）。
    #    並行後整體時間 ≈ 最快那一條，手機也不會逾時。
    async def resolve(self, url: str) -> VideoInfo:
        url = await self._normalize(url)      # iesdouyin 分享頁 → 標準 douyin 網址

        routes = [self._via_official(url), self._via_browser(url), self._via_tikwm(url)]
        tasks = [asyncio.create_task(r) for r in routes]
        try:
            for fut in asyncio.as_completed(tasks):
                try:
                    info = await fut
                except Exception:  # noqa: BLE001 — 這條路掛了就等下一條
                    continue
                if info is not None:
                    return info
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        try:
            return await YtDlpResolver.resolve(self, url)
        except PlatformError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlatformTimeout(f"抖音解析失敗：{exc}", platform=self.name) from exc

    @staticmethod
    async def _normalize(url: str) -> str:
        """把 iesdouyin 分享頁轉成標準 douyin 網址。

        ⚠️ 為什麼要轉（2026-09-27 從 v8i8 學到）：
            `www.iesdouyin.com/share/video/<id>` 對後備路線（tikwm／yt-dlp）
            是「Unsupported URL」→ 第一條路一失敗，後面全部跟著掛，
            使用者只會看到「這個連結解析不到影片」。
            轉成 `www.douyin.com/video/<id>` 後每條路都認得。
        """
        if "iesdouyin.com" not in url:
            return url
        m = re.search(r"/(?:xg/)?(?:share/)?video/(\d{15,25})", url)
        return f"https://www.douyin.com/video/{m.group(1)}" if m else url

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
        seen_h: set[tuple] = set()   # (高度, 碼率) —— 同高度不同碼率是不同畫質，不能篩掉

        # ① bitRateList（多畫質：1920x1080 / 1280x720 / 1024x576 …）
        pat_br = re.compile(
            r'\{"uri":"[^"]+","dataSize":(\d+),"width":(\d+),"height":(\d+),'
            r'"playAddr":\[\{"src":"([^"]+)"'
        )
        for size, w, hh, src in pat_br.findall(h):
            hh = int(hh)
            key = (hh, int(br.get("bit_rate") or 0))
            if key in seen_h:
                continue
            seen_h.add(key)
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
        """官方 Web API＋a_bogus（與西瓜共用 `_douyin_shared`）。"""
        aweme_id = await self._aweme_id(url)
        if not aweme_id:
            return None
        detail = await _shared.fetch_detail(aweme_id)
        if not detail:
            return None
        return self._build_official(url, detail)

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

        # ① bit_rate[]（多畫質）→ 依高度分組，每個高度只留最高碼率那一個
        buckets: dict[int, tuple[int, dict]] = {}
        for br in video.get("bit_rate") or []:
            addr = (br.get("play_addr") or {}).get("url_list") or []
            if not addr:
                continue
            h = int(br.get("height") or 0)
            w = int(br.get("width") or 0)
            gear = br.get("gear_name") or ""
            kbps = int(br.get("bit_rate") or 0) // 1000
            if not h:
                # gear_name 例如 `normal_1080_0` / `adapt_lowest_1440_1` / `adapt_lowest_4_1`
                nums = [int(x) for x in re.findall(r"\d+", gear)]
                cand = [n for n in nums if 240 <= n <= 4320]
                if cand:
                    h = cand[0]
                elif w:
                    h = round(w * 9 / 16 / 2) * 2
                else:
                    h = 1080 if kbps >= 1600 else 720 if kbps >= 1000 else 480
            prev = buckets.get(h)
            if prev is None or kbps > prev[0]:
                buckets[h] = (kbps, br)

        for h in sorted(buckets, reverse=True):
            kbps, br = buckets[h]
            u = (br.get("play_addr") or {}).get("url_list", [""])[0]
            if u in seen:
                continue
            seen.add(u)
            fmts.append(Format(
                id=f"v{h}", label=quality_label(h), url=u,
                height=h, width=br.get("width") or None,
                size=br.get("data_size") or None, quality_score=h, mode="relay",
            ))

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
                        mode="relay",
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
                       ext="mp3", quality_score=10, mode="relay")
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
                               size=d.get("hd_size"), quality_score=90, mode="relay"))
        if play:
            fmts.append(Format(id="origin", label="原畫", url=_abs(play),
                               size=d.get("size"), quality_score=80, mode="relay"))
        if music:
            fmts.append(Format(id="audio", label="純音訊", url=_abs(music), audio=True,
                               ext="mp3", quality_score=10, mode="relay"))

        if not fmts:
            raise PlatformError("抖音沒有可下載的檔案", platform=self.name)

        return VideoInfo(platform=self.name, title=title, cover=cover, source_url=url,
                         formats=fmts, duration=duration, author=author,
                         extra={"cover_fallback": d.get("cover"),
                                "is_gallery": bool(images), "route": "tikwm"})




def _abs(u: str) -> str:
    if u.startswith("http"):
        return u
    return "https://www.tikwm.com" + (u if u.startswith("/") else "/" + u)
