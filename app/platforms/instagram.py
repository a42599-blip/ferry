"""Instagram 解析。

**不需要登入、也不需要 cookies**（與 v8i8 同一套作法，但做得更完整）。

原理：
    IG 的 `/reel/<code>/embed/captioned/` 頁面在**手機身分**下，會把整包貼文資料
    塞在 `contextJSON` 這個屬性裡（`gql_data.shortcode_media`）→ 裡面就有
    `video_url` / `video_versions` / `dash_info`。

順序：
  1) embed 頁面（iPhone UA）＋ 解析 contextJSON ← 主力，零 cookies
  2) yt-dlp（若使用者另外提供了 cookies，可解限時動態等）
"""
from __future__ import annotations

import html as html_lib
import json
import re

from ..core.errors import PlatformChanged, PlatformError
from ..core.http import HttpClient
from ..core.models import Format, VideoInfo
from ._ytdlp import YtDlpResolver

_URL_RE = re.compile(r"https?://(?:www\.)?(?:instagram\.com|instagr\.am)/", re.I)
_CODE_RE = re.compile(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})")
_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_EMBED_PATHS = ("/reel/{code}/embed/captioned/", "/p/{code}/embed/captioned/")


class InstagramResolver(YtDlpResolver):
    name = "instagram"
    label = "Instagram"
    hosts = ("instagram.com", "instagr.am")
    # ⚠️ 一定要用 relay（不是 proxy）—— 2026-09-27 實測踩到：
    #    proxy 會叫 yt-dlp「重新去解析一次 Instagram」→ IG 要登入 → 一定失敗。
    #    但我們自己已經用 embed 方法拿到 CDN 網址了，直接轉發就好。
    default_mode = "relay"
    ytdlp_extra = {"http_headers": {"User-Agent": _IPHONE_UA}}

    async def match(self, url: str) -> bool:
        return bool(_URL_RE.search(url))

    async def resolve(self, url: str) -> VideoInfo:
        try:
            info = await self._via_embed(url)
            if info is not None:
                return info
        except PlatformError:
            raise
        except Exception:  # noqa: BLE001 — 退回 yt-dlp
            pass
        return await YtDlpResolver.resolve(self, url)

    # ── 主力：embed 頁面 ＋ contextJSON ────────────────
    async def _via_embed(self, url: str) -> VideoInfo | None:
        m = _CODE_RE.search(url)
        if not m:
            return None
        code = m.group(1)

        headers = {"User-Agent": _IPHONE_UA,
                   "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}
        media: dict | None = None
        async with HttpClient(ua=_IPHONE_UA, timeout=20) as http:
            for tpl in _EMBED_PATHS:
                try:
                    resp = await http.get("https://www.instagram.com" + tpl.format(code=code),
                                          headers=headers)
                except Exception:  # noqa: BLE001
                    continue
                if resp.status_code != 200:
                    continue
                data = _extract_context(resp.text)
                if not isinstance(data, dict):
                    continue
                sm = ((data.get("gql_data") or {}).get("shortcode_media")) or {}
                if sm:
                    media = sm
                    ctx = data.get("context") or {}
                    if ctx.get("copyright_blocked"):
                        media = dict(sm)
                        media["__copyright"] = True
                    break

        if not media:
            return None
        return self._build(url, media)

    def _build(self, url: str, sm: dict) -> VideoInfo:
        caption = ((sm.get("edge_media_to_caption") or {}).get("edges") or [{}])[0]
        title = (sm.get("title")
                 or ((caption.get("node") or {}).get("text") or "").strip()
                 or "Instagram 影片")
        title = re.sub(r"\s+", " ", title)[:140]
        cover = sm.get("display_url") or sm.get("thumbnail_src") or ""
        author = ((sm.get("owner") or {}).get("username")
                  or (sm.get("owner") or {}).get("full_name"))
        duration = None
        if sm.get("video_duration"):
            try:
                duration = int(float(sm["video_duration"]))
            except (TypeError, ValueError):
                duration = None

        fmts = self._formats(sm)

        # 圖集（sidecar）
        if not fmts:
            for i, edge in enumerate(
                    ((sm.get("edge_sidecar_to_children") or {}).get("edges") or [])[:20], 1):
                node = edge.get("node") or {}
                u = node.get("display_url") or node.get("thumbnail_src")
                if u:
                    fmts.append(Format(id=f"img{i}", label=f"圖 {i}", url=u, ext="jpg",
                                       quality_score=50 - i, mode="relay",
                                       headers={"Referer": "https://www.instagram.com/"}))

        if not fmts:
            if sm.get("__copyright") or sm.get("copyright_blocked"):
                raise PlatformError("這則 Instagram 貼文因版權限制無法取得影片",
                                    platform=self.name)
            raise PlatformChanged(
                "Instagram 這則貼文沒有可下載的影片（可能是圖片或已刪除）",
                platform=self.name,
            )

        return VideoInfo(
            platform=self.name, title=title, cover=cover, source_url=url,
            formats=fmts, duration=duration, author=author,
            extra={"route": "embed", "note": "免登入（embed 頁面）"},
        )

    def _formats(self, sm: dict) -> list[Format]:
        """收集所有可下載的影片網址（依畫質排序、去重）。"""
        found: dict[str, tuple[str, int]] = {}   # url -> (label, score)
        hdrs = {"Referer": "https://www.instagram.com/"}

        def add(u: str, label: str, score: int) -> None:
            if isinstance(u, str) and u.startswith("http"):
                u = html_lib.unescape(u).replace("\\/", "/")
                prev = found.get(u)
                if prev is None or score > prev[1]:
                    found[u] = (label, score)

        # ① 主影片
        add(sm.get("video_url"), "原畫", 90)

        # ② video_versions（多畫質）
        for v in sm.get("video_versions") or []:
            if isinstance(v, dict):
                h = v.get("height") or 0
                add(v.get("url"), _label(h, "原畫"), int(h) or 80)

        # ③ video_resources（較新格式，有品質標籤）
        for v in sm.get("video_resources") or []:
            if isinstance(v, dict):
                add(v.get("src"), v.get("quality_label") or "原畫",
                    int(v.get("config_height") or 0) or 75)

        # ④ DASH manifest（最高畫質常只在這裡）
        dash = (sm.get("dash_info") or {}).get("video_dash_manifest") or ""
        for u, h in re.findall(r'<BaseURL>([^<]+)</BaseURL>\s*</Representation>', dash):
            add(u, _label(h, "DASH"), 70)

        return [
            Format(id=f"ig{i}", label=label, url=u, quality_score=score,
                   mode="relay", headers=hdrs)
            for i, (u, (label, score)) in enumerate(
                sorted(found.items(), key=lambda kv: -kv[1][1]))
        ]


def _label(h, default: str) -> str:
    try:
        h = int(h)
    except (TypeError, ValueError):
        return default
    if not h:
        return default
    from ._ytdlp import quality_label

    return quality_label(h)


def _extract_context(page: str) -> dict | None:
    """從 embed 頁面取出 contextJSON 並解析成 dict。

    值本身是「JS 字串裡包著 JSON」，而且長度可達 2 萬字、內含引號與跳脫 →
    不能直接用 regex。作法：
      1) 從 `"contextJSON":"` 開始，逐字元掃到「未轉義的結尾引號」
      2) 還原 `\\"` → `"`
      3) 再用**括號配對**框出完整 JSON 物件後 parse
    """
    key = '"contextJSON":"'
    i = page.find(key)
    if i < 0:
        return None
    i += len(key)
    out: list[str] = []
    esc = False
    while i < len(page):
        c = page[i]
        if esc:
            out.append(c)
            esc = False
        elif c == "\\":
            out.append(c)
            esc = True
        elif c == '"':
            break
        else:
            out.append(c)
        i += 1

    text = "".join(out).replace('\\"', '"').replace("\\\\", "\\")

    # 括號配對（略過字串內的括號）
    depth = 0
    start = -1
    in_str = False
    esc2 = False
    for j, ch in enumerate(text):
        if in_str:
            if esc2:
                esc2 = False
            elif ch == "\\":
                esc2 = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = j
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:j + 1])
                except Exception:  # noqa: BLE001
                    return None
    return None
