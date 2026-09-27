"""解析服務：挑平台 → 呼叫 → 驗證 → 回傳。

上層（API）只呼叫這裡，不知道有幾個平台、哪個平台。
（規格書第 21-2 章）
"""
from __future__ import annotations

import asyncio
import time

from ..core import registry
from ..core.config import settings
from ..core.errors import PlatformDisabled, PlatformTimeout, UnsupportedUrl
from ..core.models import VideoInfo
from . import flags, probe


async def resolve(url: str) -> VideoInfo:
    """解析一個網址。失敗丟 AppError/PlatformError 的子類。"""
    url = (url or "").strip()
    if not url:
        raise UnsupportedUrl("請貼上影片連結")

    resolver = await registry.detect(url)
    if resolver is None:
        raise UnsupportedUrl("目前不支援這個平台的連結")

    if not flags.platform_enabled(resolver.name):
        raise PlatformDisabled(f"{resolver.label or resolver.name} 目前維護中，請稍後再試")

    started = time.perf_counter()
    try:
        info = await asyncio.wait_for(
            resolver.resolve(url), timeout=settings.resolve_timeout
        )
    except asyncio.TimeoutError as exc:
        raise PlatformTimeout(
            f"{resolver.label or resolver.name} 解析逾時（{settings.resolve_timeout} 秒）",
            platform=resolver.name,
        ) from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    _validate(info)
    # 有些平台只回「高清／原畫」沒給幾 P → 用 ffprobe 讀真實解析度再標籤
    try:
        await probe.enrich_dimensions(info)
    except Exception:  # noqa: BLE001 — probe 失敗不影響解析
        pass
    info.extra.setdefault("elapsed_ms", elapsed_ms)
    return info


def _validate(info: VideoInfo) -> None:
    """統一驗證（所有平台都一樣的檢查）。"""
    if not info.title:
        info.title = "未命名影片"
    if not info.cover:
        info.cover = ""
    # 畫質清單依「實際有的」排序：影片高→低；音訊放最後
    info.formats.sort(key=lambda f: (f.audio, -(f.quality_score or 0)))
    # 去重（同 url 只留一個）
    seen: set[str] = set()
    unique = []
    for f in info.formats:
        if f.url and f.url not in seen:
            seen.add(f.url)
            unique.append(f)
    info.formats = unique


async def supported_platforms() -> list[dict]:
    """給前端「支援平台」圖示用（會連動開關）。"""
    out = []
    for r in registry.all_platforms():
        out.append({"id": r.name, "label": r.label, "enabled": flags.platform_enabled(r.name)})
    return out
