"""SSR 助手：用「真的瀏覽器」把頁面渲染完，拿回 HTML。

為什麼需要：
  有些平台（抖音／脆 Threads／小紅書…）的資料**只在真瀏覽器渲染後才有**，
  純 HTTP 抓到的是空殼。這裡用共用瀏覽器（`services/browser.py`）統一處理。

⚠️ 一律使用 `channel="chromium"` 的新版無頭模式（headless shell 會被判機器人）。
"""
from __future__ import annotations

import asyncio
import html as html_lib
import re
from typing import Iterable

#: 預設最多等幾秒（0.4 秒一次）
DEFAULT_TRIES = 50

#: 整個渲染流程的硬性預算（秒）—— 超過就直接放棄，絕不卡死請求
DEFAULT_BUDGET = 25.0


async def render_html(
    url: str,
    *,
    context_key: str,
    wait_for: Iterable[str] = (),
    anchor: str | None = None,
    tries: int = DEFAULT_TRIES,
    goto_timeout: int = 15000,
    budget: float = DEFAULT_BUDGET,
    user_agent: str | None = None,
) -> str:
    """開頁 → 等到 `wait_for` 其中一個字串出現（或 `anchor` 直達）→ 回傳 HTML。

    `anchor`：若提供，會直接導覽到這個網址（用於把短連結換成正式頁）。
    `budget`：整個流程的硬性上限；超過回空字串（不要讓使用者卡住）。
    """
    try:
        return await asyncio.wait_for(
            _render(
                url,
                context_key=context_key,
                wait_for=wait_for,
                anchor=anchor,
                tries=tries,
                goto_timeout=goto_timeout,
                user_agent=user_agent,
            ),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        return ""


async def _render(
    url: str,
    *,
    context_key: str,
    wait_for: Iterable[str],
    anchor: str | None,
    tries: int,
    goto_timeout: int,
    user_agent: str | None,
) -> str:
    from ..services.browser import get_context

    kwargs = {"user_agent": user_agent} if user_agent else {}
    ctx = await get_context(context_key, **kwargs)
    page = await ctx.new_page()
    try:
        try:
            await page.goto(anchor or url, wait_until="commit", timeout=goto_timeout)
        except Exception:  # noqa: BLE001 — 沒載完也可能已經有資料
            pass

        wanted = list(wait_for)
        html = ""
        for _ in range(tries):
            await asyncio.sleep(0.4)
            try:
                html = await page.content()
            except Exception:  # noqa: BLE001 — 頁面正在換頁
                continue
            if not wanted or any(k in html for k in wanted):
                break
        return html
    finally:
        try:
            await asyncio.wait_for(page.close(), timeout=5)
        except Exception:  # noqa: BLE001
            pass


# ── 安全的 HTML meta 解析（避免災難性回溯）──────────────
# ⚠️ 教訓：`<meta[^>]+content=["'](.*?)["'][^>]+property=...` 這種寫法
#    在幾百 KB 的頁面上會**災難性回溯**，把整個 event loop 卡死（連逾時都救不了）。
#    正確做法：先把 <meta …> 標籤逐一切出來（[^>]* 線性），再從標籤內取屬性。
#: 掃描 meta 的頁面上限（避免超大頁面拖慢）
_META_SCAN_LIMIT = 600_000

_META_RE = re.compile(r"<meta[^>]*>", re.I)
_ATTR_RE = re.compile(r'''([a-zA-Z:_-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')''')


def meta_content(html: str, prop: str, *, limit: int = 600_000) -> str:
    """取 <meta property/name="prop" content="…"> 的值（安全版）。"""
    if not html:
        return ""

    window = html[:limit]
    for tag in _META_RE.findall(window):
        attrs: dict[str, str] = {}
        for k, v1, v2 in _ATTR_RE.findall(tag):
            attrs[k.lower()] = v1 or v2
        key = attrs.get("property") or attrs.get("name") or ""
        if key.lower() == prop.lower():
            return html_lib.unescape(attrs.get("content", ""))
    return ""



# ── 從「真的渲染過的頁面」找影片（抖音系／頭條／小紅書…都適用）──
#: HTML 裡常見的影片網址欄位名
_VIDEO_KEYS = (
    "main_url", "backup_url", "play_addr", "playAddr", "masterUrl",
    "originVideoKey", "video_url", "contentUrl", "backupUrls",
)
_VIDEO_URL_RE = re.compile(
    r'"?(?:' + "|".join(_VIDEO_KEYS) + r')"?\s*:\s*"([^"]{20,600}?)"'
)


async def page_video_info(
    url: str,
    *,
    context_key: str,
    wait_for: Iterable[str] = (),
    tries: int = 30,
    user_agent: str | None = None,
    budget: float = 30.0,
) -> dict:
    """開頁 → 等影片出現 → 回傳 {title, poster, urls[], html_len}。

    作法（兩條路一起用，命中率最高）：
      ① 讀 `<video>` 元素的 src（真瀏覽器才有）
      ② 從 HTML 抓 main_url / backup_url / playAddr … 等欄位
    """
    try:
        return await asyncio.wait_for(
            _page_video(url, context_key=context_key, wait_for=wait_for,
                        tries=tries, user_agent=user_agent),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        return {"urls": [], "title": "", "poster": "", "html_len": 0}


#: 影片網址的特徵（用來濾掉封面圖、頁面網址等雜訊）
_VIDEO_HINT = ("/video/tos/", ".mp4", "douyinvod", "toutiaovod", "xhscdn.com",
               "douyinpic.com/aweme", "sns-video")


def _looks_video(u: str) -> bool:
    low = u.lower()
    return any(h in low for h in _VIDEO_HINT)


def _clean_url(raw: str) -> str:
    """把抓到的值變成可用的網址。

    ⚠️ 抖音系／頭條的欄位值常是 **URL 編碼**（`https%3A%2F%2F…`）
       或 JSON 轉義（`/`），要先還原才能用。
    """
    if not raw:
        return ""
    u = raw.strip().replace("\u002F", "/").replace("\/", "/")
    if u.startswith("http%3A") or "%2F" in u[:40]:
        from urllib.parse import unquote

        u = unquote(u)
    if not u.startswith("http"):
        return ""
    # 去掉尾端 JSON 殘留
    u = u.split('"')[0].split("\\")[0].strip()
    return u


async def _page_video(url: str, *, context_key: str, wait_for: Iterable[str],
                      tries: int, user_agent: str | None) -> dict:
    from ..services.browser import get_context

    kwargs = {"user_agent": user_agent} if user_agent else {}
    ctx = await get_context(context_key, **kwargs)
    page = await ctx.new_page()
    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception:  # noqa: BLE001
            pass

        wanted = list(wait_for)
        info: dict = {"urls": [], "title": "", "poster": "", "html_len": 0}
        for _ in range(tries):
            await asyncio.sleep(0.6)
            try:
                info = await page.evaluate("""() => {
                  const v = document.querySelector('video');
                  const urls = [];
                  if (v) {
                    const s = v.src || v.currentSrc || '';
                    if (s.startsWith('http')) urls.push(s);
                    v.querySelectorAll('source').forEach((el) => {
                      if (el.src && el.src.startsWith('http')) urls.push(el.src);
                    });
                  }
                  return {
                    urls,
                    title: (document.title || '').replace(/\s*-\s*今日头条\s*$/, '').replace(/\s*-\s*小红书\s*$/, ''),
                    poster: v ? (v.poster || '') : '',
                    html_len: document.documentElement.innerHTML.length,
                  };
                }""")
            except Exception:  # noqa: BLE001
                continue
            if info.get("urls"):
                break
            # 註：就算 HTML 已經出現關鍵字，影片元素也可能還沒載入 → 繼續等

        # ② 從 HTML 補抓欄位式的影片網址
        try:
            html = await page.content()
        except Exception:  # noqa: BLE001
            html = ""
        info["html_len"] = len(html)
        for raw in _VIDEO_URL_RE.findall(html):
            u = _clean_url(raw)
            if u and _looks_video(u) and u not in info["urls"]:
                info["urls"].append(u)
        # 兜底：整頁掃「被 URL 編碼的影片網址」（頭條的 main_url 是這種）
        for raw in re.findall(r"https?%3A%2F%2F[^\"']{30,400}", html):
            u = _clean_url(raw)
            if u and _looks_video(u) and u not in info["urls"]:
                info["urls"].append(u)
        # 影像封面（安全版）
        if not info.get("poster"):
            info["poster"] = meta_content(html, "og:image")
        if not info.get("title"):
            info["title"] = meta_content(html, "og:title")
        return info
    finally:
        try:
            await asyncio.wait_for(page.close(), timeout=5)
        except Exception:  # noqa: BLE001
            pass
