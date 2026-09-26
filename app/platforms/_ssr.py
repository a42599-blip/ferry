"""SSR 助手：用「真的瀏覽器」把頁面渲染完，拿回 HTML。

為什麼需要：
  有些平台（抖音／脆 Threads／小紅書…）的資料**只在真瀏覽器渲染後才有**，
  純 HTTP 抓到的是空殼。這裡用共用瀏覽器（`services/browser.py`）統一處理。

⚠️ 一律使用 `channel="chromium"` 的新版無頭模式（headless shell 會被判機器人）。
"""
from __future__ import annotations

import asyncio
from typing import Iterable

#: 預設最多等幾秒（0.4 秒一次）
DEFAULT_TRIES = 50


async def render_html(
    url: str,
    *,
    context_key: str,
    wait_for: Iterable[str] = (),
    anchor: str | None = None,
    tries: int = DEFAULT_TRIES,
    goto_timeout: int = 15000,
    user_agent: str | None = None,
) -> str:
    """開頁 → 等到 `wait_for` 其中一個字串出現（或 `anchor` 直達）→ 回傳 HTML。

    `anchor`：若提供，會直接導覽到這個網址（用於把短連結換成正式頁）。
    """
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
            await page.close()
        except Exception:  # noqa: BLE001
            pass
