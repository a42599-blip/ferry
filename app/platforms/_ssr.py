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

