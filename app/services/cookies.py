"""平台 cookies（給 Facebook 這種官方限制較嚴的平台用）。

我們不寫死私人 cookies，而是看目錄裡有沒有檔：

    COOKIES_DIR/
        facebook.txt        ← Netscape 格式（瀏覽器外掛可匯出）

有檔 → yt-dlp 自動帶上；沒有 → 該平台會回「需要 cookies」的明確訊息。
（規格書：不寫死 cookies。上傳介面 2026-09-27 依小羅要求移除，
  需要時把檔案放進 Railway Volume 的 cookies 目錄即可。）
"""
from __future__ import annotations

import os

from ..core.config import settings


def _default_dir() -> str:
    return os.path.join(os.environ.get("DATA_DIR") or os.path.join(os.getcwd(), "data"),
                        "cookies")


def cookiefile_for(platform: str) -> str | None:
    """回傳該平台的 cookie 檔路徑；沒有就回 None。"""
    root = settings.cookies_dir or _default_dir()
    if not os.path.isdir(root):
        return None
    for name in (f"{platform}.txt", f"{platform}_cookies.txt"):
        path = os.path.join(root, name)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None
