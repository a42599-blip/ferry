"""平台 cookies 管理。

有些平台（Instagram／Facebook／X／微博／今日頭條／西瓜視頻…）沒有 cookies
就一律被擋。我們不寫死私人 cookies，而是：

    COOKIES_DIR/
        instagram.txt        ← Netscape 格式（瀏覽器外掛可匯出）
        facebook.txt
        x.txt
        weibo.txt
        ...

模組存在時 → yt-dlp 自動帶上；不存在 → 該平台會回「需要 cookies」的明確訊息。

（規格書：不寫死 cookies；後台可更新 = P5 之後接）
"""
from __future__ import annotations

import os

from ..core.config import settings


def cookiefile_for(platform: str) -> str | None:
    """回傳該平台的 cookie 檔路徑；沒有就回 None。"""
    root = settings.cookies_dir
    if not root or not os.path.isdir(root):
        return None
    for name in (f"{platform}.txt", f"{platform}_cookies.txt"):
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def available() -> dict[str, bool]:
    """哪些平台目前有 cookies（給後台／健康檢查用）。"""
    from ..core import registry

    return {name: cookiefile_for(name) is not None for name in registry.platform_names()}
