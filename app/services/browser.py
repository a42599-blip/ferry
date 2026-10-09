"""共享的無頭瀏覽器（只給「需要真實瀏覽器」的平台用，例如抖音）。

設計：
  - **單例瀏覽器**：整個 process 只開一個 Chromium。
  - **常駐 context**：同一個平台共用一個 context（保留 cookies，第二次更快、也更不容易被判定為機器人）。
  - **新版無頭模式**：`channel="chromium"`（⚠️ 關鍵！預設的 headless shell 會被抖音判為機器人 → 拿不到資料）

⚠️ 容器內需要 `playwright install chromium`（見 Dockerfile）。
"""
from __future__ import annotations

import asyncio
from typing import Any

_lock = asyncio.Lock()
_ctx_lock = asyncio.Lock()
_playwright: Any = None
_browser: Any = None
_contexts: dict[str, Any] = {}

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]

_STEALTH_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
    "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]});"
)


async def get_browser() -> Any:
    """取得（必要時啟動）共享的 Chromium。"""
    global _playwright, _browser
    async with _lock:
        if _browser is not None:
            try:
                if _browser.is_connected():
                    return _browser
            except Exception:  # noqa: BLE001
                _browser = None

        from playwright.async_api import async_playwright

        if _playwright is None:
            _playwright = await async_playwright().start()
        try:
            _browser = await _playwright.chromium.launch(
                headless=True,
                channel="chromium",      # ⚠️ 新版無頭模式；不能省
                args=_LAUNCH_ARGS,
            )
        except Exception as exc:  # noqa: BLE001
            # ⚠️ 2026-10-10（照 v8i8）：容器內若沒有 chromium channel（例如只裝了
            #    headless shell），launch 會直接拋錯 → 整條瀏覽器路線靜默死掉。
            #    退回預設 headless 至少還能跑，並把原因印出來（線上 log 看得到）。
            print(f"[browser] channel=chromium 啟動失敗，退回預設 headless：{exc}")
            _browser = await _playwright.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        return _browser


async def get_context(key: str, **kwargs: Any) -> Any:
    """取得（必要時建立）常駐的 context；同一 key 會重用（保留 cookies）。"""
    async with _ctx_lock:
        ctx = _contexts.get(key)
        if ctx is not None:
            return ctx

        # ⚠️ get_browser() 自己會拿 _lock；這裡用的是另一把 _ctx_lock，不會死鎖。
        browser = await get_browser()
        defaults = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1440, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        defaults.update(kwargs)
        ctx = await browser.new_context(**defaults)
        await ctx.add_init_script(_STEALTH_JS)
        _contexts[key] = ctx
        return ctx


async def close() -> None:
    """關掉所有 context／瀏覽器／playwright（維運或測試用）。"""
    global _playwright, _browser
    for ctx in list(_contexts.values()):
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass
    _contexts.clear()
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:  # noqa: BLE001
            pass
        _browser = None
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        _playwright = None

