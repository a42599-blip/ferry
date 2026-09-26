"""平台解析健康檢查工具。

用法：
    python tools/check_platform.py <url>...            # 逐一解析並印出結果
    python tools/check_platform.py --title <url>      # 額外用真瀏覽器讀頁面標題做交叉比對

（規格書第 22 章：每平台都要有可重複執行的驗證方式）
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import registry  # noqa: E402


async def resolve_one(url: str, want_title: bool) -> None:
    resolver = await registry.detect(url)
    if resolver is None:
        print(f"  X  不支援的網址：{url}")
        return
    t0 = time.perf_counter()
    try:
        info = await resolver.resolve(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  X  {resolver.name}: {type(exc).__name__} {str(exc)[:140]}")
        return
    elapsed = time.perf_counter() - t0
    print(f"  OK {resolver.name}  {elapsed:.1f}s  route={info.extra.get('route', '-')}")
    print(f"     標題 : {info.title[:70]}")
    print(f"     作者 : {info.author}   時長: {info.duration}")
    print(f"     畫質 : {', '.join(f.label for f in info.formats[:8])}")
    print(f"     封面 : {(info.cover or '')[:80]}")

    if want_title:
        title = await browser_title(url)
        print(f"     瀏覽器標題: {title[:70] if title else '(讀不到)'}")


async def browser_title(url: str) -> str:
    from app.platforms.douyin import _ID_RE
    from app.services.browser import close, get_context

    m = _ID_RE.search(url)
    target = f"https://www.douyin.com/video/{m.group(1)}" if m else url
    ctx = await get_context("douyin")
    page = await ctx.new_page()
    try:
        try:
            await page.goto(target, wait_until="commit", timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(8)
        return await page.title()
    finally:
        await page.close()
        await close()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--title", action="store_true", help="用真瀏覽器讀頁面標題交叉比對")
    args = ap.parse_args()

    for url in args.urls:
        print("─" * 70)
        print(f"→ {url}")
        await resolve_one(url, args.title)


if __name__ == "__main__":
    asyncio.run(main())
